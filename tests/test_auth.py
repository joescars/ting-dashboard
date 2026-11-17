"""Access control, onboarding and metadata tests."""

import asyncio
import json

import pytest
from aiohttp import web

from app import client as client_mod
from app import const, main
from app.authz import Authz


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Fresh data dir, no password unless the test sets one."""
    monkeypatch.setattr(const, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    monkeypatch.delenv("TING_DEMO", raising=False)
    return tmp_path


def test_session_cookie_signing(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")
    a = Authz(str(tmp_path))
    cookie = a.issue()
    assert a.valid(cookie)
    assert not a.valid(cookie[:-1] + ("0" if cookie[-1] != "0" else "1"))
    assert not a.valid("garbage")
    assert (tmp_path / "session.key").stat().st_mode & 0o777 == 0o600
    # a restart with the same data dir keeps sessions valid
    assert Authz(str(tmp_path)).valid(cookie)


def test_setup_token_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUP_TOKEN", "abc123")
    a = Authz(str(tmp_path))
    assert a.check_setup_token("abc123") and not a.check_setup_token("nope")
    token = a.setup_token
    (tmp_path / "auth.json").write_text("{}")
    a2 = Authz(str(tmp_path))
    assert a2.setup_token is None  # already linked
    a.consume_setup_token()
    assert token and not (tmp_path / "setup_token").exists()


async def test_password_gate(aiohttp_client, app_env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "letmein")
    cl = await aiohttp_client(main.make_app(demo=True))

    assert (await cl.get("/api/state")).status == 401
    r = await cl.get("/", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/login"
    assert (await cl.get("/login")).status == 200
    assert (await cl.get("/healthz")).status == 200
    assert (await cl.get("/static/app.css")).status == 200

    assert (await cl.post("/api/login", json={"password": "wrong"})).status == 401
    r = await cl.post("/api/login", json={"password": "letmein"})
    assert r.status == 200 and "ting_session" in r.cookies
    assert (await cl.get("/api/state")).status == 200
    assert (await cl.get("/")).status == 200

    # rate limit kicks in after repeated failures from one peer
    for _ in range(5):
        await cl.post("/api/login", json={"password": "nope"})
    assert (await cl.post("/api/login", json={"password": "letmein"})).status == 429

    # sign out invalidates the cookie
    await cl.post("/api/logout")
    cl.session.cookie_jar.clear()
    assert (await cl.get("/api/state")).status == 401


async def test_cross_origin_login_rejected(aiohttp_client, app_env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "letmein")
    cl = await aiohttp_client(main.make_app(demo=True))
    r = await cl.post("/api/login", json={"password": "letmein"},
                      headers={"Origin": "https://evil.example"})
    assert r.status == 403


class FakeClient:
    """Stands in for TingClient during onboarding."""

    def __init__(self, fail=False):
        self.fail = fail
        self.signed_in = False
        self.seen = None

    async def sign_in(self, email, password):
        self.seen = (email, password)
        if self.fail:
            raise client_mod.NotSignedIn("bad credentials")
        self.signed_in = True

    def sign_out(self):
        self.signed_in = False


async def test_setup_flow(aiohttp_client, app_env, monkeypatch):
    app = main.make_app(demo=False)
    cl = await aiohttp_client(app)
    token = app["authz"].setup_token
    assert token

    r = await cl.get("/", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/setup"
    assert (await cl.get("/setup")).status == 200

    fake = FakeClient(fail=True)
    app["client"] = fake
    r = await cl.post("/api/setup", json={"email": "a@b.c", "password": "pw", "token": "wrong"})
    assert r.status == 403
    r = await cl.post("/api/setup", json={"email": "a@b.c", "password": "pw", "token": token})
    assert r.status == 401 and "rejected" in (await r.json())["error"]

    fake.fail = False
    r = await cl.post("/api/setup", json={"email": "a@b.c", "password": "pw", "token": token})
    assert r.status == 200
    assert fake.seen == ("a@b.c", "pw")
    # the token is one-time and the account is now bound
    assert app["authz"].setup_token is None
    r = await cl.post("/api/setup", json={"email": "a@b.c", "password": "pw", "token": token})
    assert r.status == 409
    assert (await (await cl.get("/api/session")).json())["signed_in"] is True


async def test_setup_requires_private_network(aiohttp_client, app_env):
    app = main.make_app(demo=False)
    cl = await aiohttp_client(app)
    token = app["authz"].setup_token
    app["client"] = FakeClient()
    r = await cl.post("/api/setup", json={"email": "a@b.c", "password": "pw", "token": token},
                      headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status == 403


async def test_no_password_means_open_dashboard(aiohttp_client, app_env):
    cl = await aiohttp_client(main.make_app(demo=True))
    body = await (await cl.get("/api/session")).json()
    assert body == {
        "password_required": False, "authenticated": True, "signed_in": True,
        "demo": True, "setup_available": False,
    }
    assert (await cl.get("/api/state")).status == 200


async def test_version_and_manifest(aiohttp_client, app_env, monkeypatch):
    monkeypatch.setenv("TING_VERSION", "9.9.9")
    cl = await aiohttp_client(main.make_app(demo=True))
    assert (await (await cl.get("/api/version")).json())["version"] == "9.9.9"
    m = await (await cl.get("/manifest.webmanifest")).json()
    assert m["short_name"] == "Ting" and m["display"] == "standalone"
    for icon in m["icons"]:
        assert (await cl.get(icon["src"])).status == 200


async def test_unlink(aiohttp_client, app_env):
    app = main.make_app(demo=False)
    cl = await aiohttp_client(app)
    app["client"] = FakeClient()
    app["client"].signed_in = True
    r = await cl.post("/api/unlink")
    assert r.status == 200
    assert app["authz"].setup_token  # a fresh onboarding code is issued
    assert (await (await cl.get("/api/session")).json())["signed_in"] is False
