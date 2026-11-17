"""Dashboard access control and one-time setup tokens.

Two independent gates:

1. **Dashboard password** (optional, ``DASHBOARD_PASSWORD``). When set, every
   page and API route requires a signed session cookie. When unset the
   dashboard is open to the network — fine on a trusted LAN, never on the
   internet — and the UI says so.
2. **Setup token** (``SETUP_TOKEN`` or generated). Needed to submit the Ting
   account password through the browser during onboarding, so knowing the
   dashboard URL alone is not enough to bind someone else's account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time
from pathlib import Path

from aiohttp import web

_LOGGER = logging.getLogger("ting.auth")

COOKIE = "ting_session"
SESSION_TTL = 30 * 86400
PUBLIC_PATHS = {"/login", "/healthz", "/api/version", "/api/session", "/manifest.webmanifest"}
PUBLIC_PREFIXES = ("/static/",)


# Explicitly listed rather than ip.is_private: CPython also flags the
# documentation ranges (203.0.113.0/24 and friends) as private.
_PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
              "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10")
]


def private_request(request: web.Request) -> bool:
    """True for loopback/RFC1918/ULA peers, honouring one proxy hop.

    Forwarded headers are only believed when the immediate peer is loopback —
    the documented reverse-proxy layout puts the proxy on 127.0.0.1, so this
    keeps that working while stopping a remote client from claiming
    127.0.0.1 in X-Forwarded-For to reach /setup.
    """
    peer = (request.remote or "").strip()
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if ip.is_loopback:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        candidate = (request.headers.get("X-Real-IP") or forwarded or peer).strip()
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return False
    return any(ip in net for net in _PRIVATE_NETS if net.version == ip.version)


def _load_or_create(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
        return value


def writable_problem(data_dir: str) -> str | None:
    """Return an actionable message when the data dir is unusable."""
    path = Path(data_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as err:
        return (
            f"The data directory {data_dir} is not writable by this process "
            f"({err.strerror}).\n"
            f"  Fix it with:  sudo chown -R {os.getuid()}:{os.getgid()} {data_dir}\n"
            f"  Or set TING_UID/TING_GID in .env to the user that owns it."
        )
    return None


class Authz:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.password = os.environ.get("DASHBOARD_PASSWORD") or None
        self._secret = _load_or_create(self.data_dir / "session.key")
        self.setup_token: str | None = None
        self._attempts: dict[str, list[float]] = {}
        if not self.signed_in_marker():
            self.setup_token = self._init_setup_token()

    # --- setup token ------------------------------------------------
    def _init_setup_token(self) -> str:
        path = self.data_dir / "setup_token"
        env = os.environ.get("SETUP_TOKEN")
        token = env or (path.read_text().strip() if path.exists() else secrets.token_urlsafe(18))
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(token)
        return token

    def signed_in_marker(self) -> bool:
        return (self.data_dir / "auth.json").exists()

    def consume_setup_token(self) -> None:
        """Setup is one-time: drop the token once an account is bound."""
        self.setup_token = None
        (self.data_dir / "setup_token").unlink(missing_ok=True)

    def check_setup_token(self, candidate: str | None) -> bool:
        return bool(self.setup_token) and bool(candidate) and hmac.compare_digest(
            str(candidate), self.setup_token
        )

    # --- sessions ---------------------------------------------------
    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expiry = str(int(time.time()) + SESSION_TTL)
        payload = base64.urlsafe_b64encode(expiry.encode()).decode()
        return f"{payload}.{self._sign(payload)}"

    def valid(self, cookie: str | None) -> bool:
        if not cookie or "." not in cookie:
            return False
        payload, signature = cookie.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._sign(payload)):
            return False
        try:
            expiry = int(base64.urlsafe_b64decode(payload.encode()).decode())
        except (ValueError, UnicodeDecodeError):
            return False
        return expiry > time.time()

    # --- login rate limit -------------------------------------------
    def throttled(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._attempts.get(key, []) if now - t < 300]
        self._attempts[key] = hits
        return len(hits) >= 5

    def note_failure(self, key: str) -> None:
        self._attempts.setdefault(key, []).append(time.time())

    def clear_failures(self, key: str) -> None:
        self._attempts.pop(key, None)

    # --- request gate -----------------------------------------------
    def requires_auth(self, path: str) -> bool:
        return bool(self.password) and path not in PUBLIC_PATHS and not path.startswith(
            PUBLIC_PREFIXES
        ) and not path.startswith("/api/login") and not path.startswith("/api/logout")


def set_cookie(request: web.Request, resp: web.StreamResponse, value: str) -> None:
    https = request.secure or request.headers.get("X-Forwarded-Proto", "") == "https"
    resp.set_cookie(
        COOKIE, value, max_age=SESSION_TTL, httponly=True, samesite="Lax",
        secure=https, path="/",
    )


def clear_cookie(resp: web.StreamResponse) -> None:
    resp.del_cookie(COOKIE, path="/")


def same_origin(request: web.Request) -> bool:
    """Reject cross-site form posts (cookie is SameSite=Lax, this is belt+braces)."""
    origin = request.headers.get("Origin")
    if not origin:
        return True  # non-browser or same-origin GET
    host = request.headers.get("Host", "")
    return origin.split("://", 1)[-1] == host
