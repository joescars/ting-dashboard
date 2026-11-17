"""Ting cloud REST client with persisted refresh-token auth.

The account password is used once (sign-in) and never written to disk.
Only the Cognito refresh token plus account identifiers are persisted, in
``<data>/auth.json`` with 0600 permissions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

from .auth import AuthenticationError, CognitoServiceError, WhiskerAuth
from .const import (
    API_BASE_URL,
    API_NOTIFICATIONS_ENDPOINT,
    API_USERS_ENDPOINT,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
_TRANSIENT = (CognitoServiceError, aiohttp.ClientError, TimeoutError, KeyError, ValueError)


class NotSignedIn(Exception):
    """No usable credentials; the user must sign in."""


class TingApiError(Exception):
    """Non-auth API failure (network, 5xx, bad payload)."""


class AuthStore:
    """Persists refresh token + account identifiers (never the password)."""

    def __init__(self, data_dir: str) -> None:
        self.path = Path(data_dir) / "auth.json"

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def _attrs(attributes: Any) -> tuple[int, str]:
    values = {
        a["Name"]: a["Value"]
        for a in attributes or []
        if isinstance(a, dict) and "Name" in a and "Value" in a
    }
    try:
        user_id = int(values["custom:user_id"])
        api_key = values["custom:api_key"]
    except (KeyError, TypeError, ValueError) as err:
        raise NotSignedIn("Account is missing Ting identifiers") from err
    if not user_id or not api_key:
        raise NotSignedIn("Account is missing Ting identifiers")
    return user_id, api_key


def reverse_mac(mac: str | None) -> str | None:
    """The API reports MACs in reversed byte order; return physical order."""
    if not mac:
        return mac
    hex_only = mac.replace(":", "").replace("-", "")
    if len(hex_only) != 12:
        return mac
    try:
        int(hex_only, 16)
    except ValueError:
        return mac
    return ":".join(reversed([hex_only[i : i + 2] for i in range(0, 12, 2)])).lower()


class TingClient:
    def __init__(self, session: aiohttp.ClientSession, store: AuthStore) -> None:
        self._session = session
        self._store = store
        self._auth = WhiskerAuth(session)
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._expiry = 0.0
        saved = store.load() or {}
        self.username: str | None = saved.get("username")
        self._refresh_token: str | None = saved.get("refresh_token")
        self.user_id: int | None = saved.get("user_id")
        self.api_key: str | None = saved.get("api_key")

    @property
    def signed_in(self) -> bool:
        return bool(self._refresh_token)

    def _persist(self) -> None:
        self._store.save(
            {
                "username": self.username,
                "refresh_token": self._refresh_token,
                "user_id": self.user_id,
                "api_key": self.api_key,
            }
        )

    async def sign_in(self, username: str, password: str) -> None:
        """Exchange email/password for tokens; the password is not retained."""
        try:
            result = await self._auth.authenticate(username, password)
        except AuthenticationError as err:
            raise NotSignedIn(str(err)) from err
        except _TRANSIENT as err:
            raise TingApiError("Could not reach the Ting sign-in service") from err
        user_id, api_key = _attrs(result.get("user_attributes"))
        async with self._lock:
            self.username = username
            self._refresh_token = result["refresh_token"]
            self._access_token = result["access_token"]
            self._expiry = time.time() + int(result.get("expires_in", 3600))
            self.user_id, self.api_key = user_id, api_key
            self._persist()

    def sign_out(self) -> None:
        self._store.clear()
        self._refresh_token = self._access_token = None
        self.user_id = self.api_key = self.username = None

    def reload(self) -> None:
        """Pick up a token written by `python -m app.login` while running."""
        saved = self._store.load() or {}
        if saved.get("refresh_token") and not self._refresh_token:
            self.username = saved.get("username")
            self._refresh_token = saved["refresh_token"]
            self.user_id, self.api_key = saved.get("user_id"), saved.get("api_key")

    async def get_access_token(self) -> str:
        return await self._token()

    async def _token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._access_token and time.time() < self._expiry - 300:
                return self._access_token
            if not self._refresh_token:
                raise NotSignedIn("Not signed in")
            try:
                result = await self._auth.refresh_tokens(self._refresh_token)
            except AuthenticationError as err:
                raise NotSignedIn("Ting session expired; sign in again") from err
            except _TRANSIENT as err:
                raise TingApiError("Could not refresh the Ting session") from err
            access: str = result["AccessToken"]
            self._access_token = access
            self._expiry = time.time() + int(result.get("ExpiresIn", 3600))
            rotated = result.get("RefreshToken")
            if rotated and rotated != self._refresh_token:
                self._refresh_token = rotated
                self._persist()
            if not self.user_id or not self.api_key:
                try:
                    info = await self._auth.get_user(access)
                except _TRANSIENT as err:
                    raise TingApiError("Could not load Ting account") from err
                self.user_id, self.api_key = _attrs(info.get("UserAttributes"))
                self._persist()
            return access

    async def _get(self, endpoint: str) -> Any:
        url = API_BASE_URL + endpoint
        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            headers = {
                "Authorization": " ".join(("Bearer", token)),
                "Accept": "application/json",
                "x-wl-api-key": self.api_key or "",
            }
            try:
                async with self._session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    if resp.status == 401:
                        raise NotSignedIn("Ting rejected the session; sign in again")
                    if resp.status != 200:
                        raise TingApiError(f"Ting API returned HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise TingApiError(f"Ting API unreachable ({type(err).__name__})") from err
            except ValueError as err:
                raise TingApiError("Ting API returned invalid JSON") from err
        raise TingApiError("unreachable")

    async def get_user(self) -> dict[str, Any]:
        await self._token()
        data = await self._get(API_USERS_ENDPOINT.format(user_id=self.user_id))
        if not isinstance(data, dict):
            raise TingApiError("Unexpected user payload")
        return parse_user(data)

    async def get_notifications(self) -> list[dict[str, Any]]:
        await self._token()
        data = await self._get(API_NOTIFICATIONS_ENDPOINT.format(user_id=self.user_id))
        if not isinstance(data, list):
            return []
        return [parse_notification(n) for n in data if isinstance(n, dict)]


def _hazard(d: dict | None) -> dict[str, Any]:
    d = d or {}
    return {
        "status": d.get("status"),
        "level": d.get("level"),
        "message": d.get("message") or "No Hazards Detected",
        "timestamp": d.get("timestampUtc"),
        "color": d.get("hexColor"),
    }


def parse_user(data: dict[str, Any]) -> dict[str, Any]:
    """Reduce the Users payload to what the dashboard shows (no PII beyond name)."""
    sites = {
        s.get("id"): s for s in (data.get("sites") or []) if isinstance(s, dict)
    }
    devices = []
    for d in data.get("devices") or []:
        if not isinstance(d, dict):
            continue
        fhs = d.get("fireHazardStatus") or {}
        site = sites.get(d.get("siteId")) or {}
        serial = d.get("serialNumber") or ""
        devices.append(
            {
                "serial": serial,
                "name": d.get("name") or serial or "Ting sensor",
                "type": d.get("type"),
                "firmware": d.get("version"),
                "wifi_mac": reverse_mac(d.get("wifiMacAddress")),
                "site_name": site.get("displayName"),
                "subscription_start": d.get("subscriptionStartDate"),
                "is_fire": bool(d.get("isFire")),
                "has_frozen_pipe": bool(d.get("hasFrozenPipe")),
                "power_quality_hazard": bool(site.get("isPowerQualityHazard")),
                "learning_mode": bool(fhs.get("learningMode")),
                "hazard_message": fhs.get("message") or "No Hazards Detected",
                "efh": _hazard(fhs.get("efhStatus")),
                "ufh": _hazard(fhs.get("ufhStatus")),
            }
        )
    return {
        "first_name": data.get("firstName") or "",
        "last_name": data.get("lastName") or "",
        "devices": devices,
    }


def parse_notification(n: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": n.get("id"),
        "type": n.get("eventType") or "unknown",
        "category": n.get("eventCategory"),
        "title": n.get("title"),
        "subtitle": n.get("subtitle"),
        "message": n.get("message"),
        "time": n.get("eventTimestampLocal") or n.get("sentUtc"),
        "serial": n.get("serialNumber"),
        "cleared": bool(n.get("isCleared")),
    }
