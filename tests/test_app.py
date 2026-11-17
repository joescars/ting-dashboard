"""Tests: storage, parsing, auth persistence, stream against a fake hub, HTTP API."""

import asyncio
import json
import os
import stat
import time
from datetime import UTC, datetime

import aiohttp
import pytest
from aiohttp import web

from app import client as client_mod
from app import protocol, stream as stream_mod
from app.client import AuthStore, TingClient, parse_user, reverse_mac
from app.store import Store


# ------------------------------------------------------------------ store
def test_store_buckets_and_history(tmp_path):
    s = Store(str(tmp_path), 5)
    now = int(time.time())
    base = now - now % 5 - 60
    for i in range(48):  # 12 s of 4 Hz-ish samples spread across buckets
        t = base + i * 0.25
        s.add_sample("S1", t, 120 + (i % 4), 122 + (i % 4), 118, i % 3)
    s.flush_all()
    h = s.history("S1", 3600)
    assert h["step"] == 5
    assert len(h["points"]) == 3
    p = h["points"][0]
    assert p["min"] == 120 and p["max"] == 123 and p["avg"] == 121.5
    assert p["hi"] == 125 and p["lo"] == 118
    assert p["peak"] == 0.95 and p["peak_hi"] == 2
    assert h["stats"]["samples"] == 48
    # long ranges are downsampled to <= 720 points with whole-bucket steps
    h30 = s.history("S1", 30 * 86400)
    assert h30["step"] % 5 == 0 and 30 * 86400 / h30["step"] <= 720
    assert s.history("nope", 3600)["stats"] is None


def test_status_log_dedupes(tmp_path):
    s = Store(str(tmp_path), 5)
    assert s.log_status("S1", {"efh": "Low"})
    assert not s.log_status("S1", {"efh": "Low"})
    assert s.log_status("S1", {"efh": "High"})
    s2 = Store(str(tmp_path), 5)  # survives restart
    assert not s2.log_status("S1", {"efh": "High"})


def test_prune(tmp_path):
    s = Store(str(tmp_path), 5)
    s.add_sample("S1", time.time() - 100 * 86400, 120, 121, 119)
    s.add_sample("S1", time.time(), 120, 121, 119)
    s.flush_all()
    assert s.prune(90) == 1


# ------------------------------------------------------------------ parsing
def test_reverse_mac():
    assert reverse_mac("8A:2A:37:10:6A:80") == "80:6a:10:37:2a:8a"
    assert reverse_mac("bad") == "bad"
    assert reverse_mac(None) is None


def test_parse_user_minimal_and_full():
    data = {
        "firstName": "Joe",
        "sites": [{"id": 7, "displayName": "Home", "isPowerQualityHazard": True}],
        "devices": [{
            "serialNumber": "ABC", "name": "Panel", "siteId": 7, "isFire": False,
            "fireHazardStatus": {"learningMode": True, "message": "OK",
                                 "efhStatus": {"level": "Low"}, "ufhStatus": None},
        }, "junk"],
    }
    u = parse_user(data)
    d = u["devices"][0]
    assert len(u["devices"]) == 1
    assert d["site_name"] == "Home" and d["power_quality_hazard"] and d["learning_mode"]
    assert d["efh"]["level"] == "Low" and d["ufh"]["message"] == "No Hazards Detected"
    assert parse_user({})["devices"] == []


# ------------------------------------------------------------------ auth persistence
class FakeAuth:
    def __init__(self, *_):
        self.refreshes = 0

    async def authenticate(self, username, password):
        return {
            "access_token": "acc", "refresh_token": "ref-1", "expires_in": 3600,
            "user_attributes": [{"Name": "custom:user_id", "Value": "42"},
                                {"Name": "custom:api_key", "Value": "key"}],
        }

    async def refresh_tokens(self, rt):
        self.refreshes += 1
        return {"AccessToken": f"acc{self.refreshes}", "ExpiresIn": 3600, "RefreshToken": "ref-2"}

    async def get_user(self, token):
        return {"UserAttributes": []}


async def test_sign_in_persists_refresh_token_not_password(tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod, "WhiskerAuth", FakeAuth)
    async with aiohttp.ClientSession() as session:
        c = TingClient(session, AuthStore(str(tmp_path)))
        secret = "pw-" + "x" * 8
        await c.sign_in("a@b.c", secret)
        raw = (tmp_path / "auth.json").read_text()
        assert secret not in raw
        saved = json.loads(raw)
        assert saved == {"username": "a@b.c", "refresh_token": "ref-1", "user_id": 42, "api_key": "key"}
        assert stat.S_IMODE(os.stat(tmp_path / "auth.json").st_mode) == 0o600
        # new client resumes from disk and rotates the refresh token
        c2 = TingClient(session, AuthStore(str(tmp_path)))
        assert c2.signed_in
        assert await c2.get_access_token() == "acc1"
        assert json.loads((tmp_path / "auth.json").read_text())["refresh_token"] == "ref-2"
        c2.sign_out()
        assert not (tmp_path / "auth.json").exists()


# ------------------------------------------------------------------ stream vs fake hub
def _voltage_frame(v):
    payload = {"Voltage": v, "VoltageHi": v + 1, "VoltageLo": v - 1,
               "DataTimeUtc": datetime.now(UTC).isoformat()}
    return protocol.frame_messagepack([1, {}, None, "updateComboBinaryData", [payload]])


def test_decode_extra_measurements():
    """THD / frequency / peak count ride along as extras, non-scalars dropped."""
    payload = {
        "Voltage": 120.8, "VoltageHi": 124.9, "VoltageLo": 116.3,
        "DataTimeUtc": datetime.now(UTC).isoformat(),
        "Thd": 2.76, "Frequency": 60.01, "AveragePeaksMax": 4,
        "StationName": "kitchen",          # string, not a measurement
        "Nested": {"ignored": 1.0},        # not a scalar
        "Bad": float("nan"),               # not finite
    }
    msg = [1, {}, None, "updateComboBinaryData", [payload]]
    data = protocol.decode_voltage_update(msg)
    assert data is not None
    assert data.extras == {"Thd": 2.76, "Frequency": 60.01, "AveragePeaksMax": 4.0}
    assert "StationName" in data.fields


async def test_signal_reaches_state_and_samples(aiohttp_client, tmp_path, monkeypatch):
    from app import const, main

    monkeypatch.setattr(const, "DATA_DIR", str(tmp_path))
    cl = await aiohttp_client(main.make_app(demo=True))
    state = None
    for _ in range(50):
        state = await (await cl.get("/api/state")).json()
        if state["latest"]:
            break
        await asyncio.sleep(0.1)
    assert state is not None
    station = state["devices"][0]["serial"]
    sig = state["signal"][station]
    assert set(sig) == {"Thd", "Frequency", "AveragePeaksMax"}
    recent = await (await cl.get(f"/api/recent?station={station}")).json()
    assert "m" in recent[-1] and "Thd" in recent[-1]["m"]


async def test_stream_against_fake_hub(monkeypatch, aiohttp_server):
    seen = {"invocations": [], "headers": None}

    async def hub(request):
        seen["headers"] = dict(request.headers)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        msg = await ws.receive()
        assert json.loads(msg.data.rstrip("\x1e")) == {"protocol": "messagepack", "version": 1}
        await ws.send_str("{}\x1e")
        for _ in range(2):
            m = await ws.receive()
            for hm in protocol.decode_hub_messages(m.data):
                if hm[0] == 1:
                    seen["invocations"].append((hm[3], hm[4]))
        await ws.send_bytes(protocol.frame_messagepack([3, {}, "2", 3, None]))
        for v in (120.5, 121.0, 119.5):
            await ws.send_bytes(_voltage_frame(v))
            await asyncio.sleep(0.01)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/dataHub", hub)
    server = await aiohttp_server(app)
    monkeypatch.setattr(stream_mod, "SIGNALR_URL", str(server.make_url("/dataHub")))

    samples, states = [], []

    async def creds():
        return "key", 42

    async with aiohttp.ClientSession() as session:
        vs = stream_mod.VoltageStream(session, "ABC", creds,
                                      lambda st, s: samples.append((st, s.voltage)),
                                      lambda st, state, r: states.append(state))
        with pytest.raises(ConnectionError):
            await vs._session_once()

    assert [s[1] for s in samples] == [120.5, 121.0, 119.5]
    assert "live" in states
    assert [i[0] for i in seen["invocations"]] == ["UnInitializeStreaming", "InitializeStreaming"]
    assert seen["invocations"][1][1][0] == {"StationId": "ABC", "DataElement": "ComboBinaryData"}
    assert seen["invocations"][1][1][1:] == ["key", "42"]
    assert seen["headers"]["x-wl-api-key"] == "key"


async def test_stream_rejection(monkeypatch, aiohttp_server):
    async def hub(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()
        await ws.send_str("{}\x1e")
        await ws.receive()
        await ws.receive()
        await ws.send_bytes(protocol.frame_messagepack([3, {}, "2", 1, "Unauthorized"]))
        await asyncio.sleep(1)
        return ws

    app = web.Application()
    app.router.add_get("/dataHub", hub)
    server = await aiohttp_server(app)
    monkeypatch.setattr(stream_mod, "SIGNALR_URL", str(server.make_url("/dataHub")))

    async def creds():
        return "key", 42

    async with aiohttp.ClientSession() as session:
        vs = stream_mod.VoltageStream(session, "ABC", creds, lambda *a: None, lambda *a: None)
        with pytest.raises(stream_mod.StreamRejected, match="Unauthorized"):
            await vs._session_once()


# ------------------------------------------------------------------ HTTP (demo mode)
async def test_http_demo(aiohttp_client, tmp_path, monkeypatch):
    from app import const, main

    monkeypatch.setattr(const, "DATA_DIR", str(tmp_path))
    cl = await aiohttp_client(main.make_app(demo=True))
    for _ in range(50):
        state = await (await cl.get("/api/state")).json()
        if state["latest"]:
            break
        await asyncio.sleep(0.1)
    assert state["demo"] is True and state["auth"]["state"] == "demo"
    serial = state["devices"][0]["serial"]
    h = await (await cl.get(f"/api/history?station={serial}&range=24h")).json()
    assert len(h["points"]) > 100 and h["stats"]["avg"] > 100
    assert (await cl.get("/api/history?station=nope&range=24h")).status == 404
    assert (await cl.get(f"/api/history?station={serial}&range=99y")).status == 400
    recent = await (await cl.get(f"/api/recent?station={serial}")).json()
    assert recent and recent[-1]["st"] == serial
    r = await cl.get("/")
    assert r.status == 200 and "Content-Security-Policy" in r.headers
    assert (await cl.get("/static/app.js")).status == 200
    assert (await cl.get("/static/../app/main.py")).status in (403, 404)
    # SSE delivers state + samples
    resp = await cl.get("/api/live")
    got = b""
    async with asyncio.timeout(5):
        while b"event: sample" not in got:
            got += await resp.content.readany()
    assert got.startswith(b"event: state")
    resp.close()


# ------------------------------------------------------------------ live chart lines
async def test_live_chart_plots_hifi_alongside_voltage(aiohttp_client, tmp_path, monkeypatch):
    """The live chart draws the peak ("hi-fi") count over the voltage line.

    No backend change is needed: ``AveragePeaksMax`` already rides along on
    every sample as ``p.m.AveragePeaksMax``. This guards the client contract
    — a second line, its own scale, and a legend entry naming it — and that
    the sample payload keeps carrying the field the line reads.
    """
    from app import const, main

    monkeypatch.setattr(const, "DATA_DIR", str(tmp_path))
    cl = await aiohttp_client(main.make_app(demo=True))
    state = None
    for _ in range(50):
        state = await (await cl.get("/api/state")).json()
        if state["latest"]:
            break
        await asyncio.sleep(0.1)
    assert state and state["latest"], "demo mode never produced a sample"
    serial = state["devices"][0]["serial"]

    # The field the second line reads must reach the browser on live samples.
    recent = await (await cl.get(f"/api/recent?station={serial}")).json()
    assert recent, "expected recent samples"
    assert "AveragePeaksMax" in recent[-1]["m"]

    history = await (await cl.get(f"/api/history?station={serial}&range=24h")).json()
    assert history["points"] and any(point["peak"] > 0 for point in history["points"])

    js = (await (await cl.get("/static/app.js")).text())
    # a high/low band is drawn for voltage but must NOT be drawn for peaks
    assert "band:" in js, "voltage band wiring is missing"
    assert "peak:" in js, "the hi-fi line is not wired into the live chart"
    assert "drawChart(" in js
    # the series must read Ting's peak-count measurement, matched case-insensitively
    assert "averagepeaksmax" in js, "the hi-fi series does not read the peak measurement"

    html = (await (await cl.get("/")).text())
    assert "Hi-Fi" in html, "the live chart legend does not name the hi-fi line"
