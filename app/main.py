"""Ting dashboard service: polls the Ting cloud, streams live voltage,
persists history to SQLite and serves a read-only web dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from . import const
from .client import AuthStore, NotSignedIn, TingApiError, TingClient
from .protocol import VoltageData
from .store import Store
from .stream import VoltageStream

_LOGGER = logging.getLogger("ting")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
RECENT_SECONDS = 300
RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}


class Hub:
    """All runtime state plus fan-out to connected browsers."""

    def __init__(self, demo: bool = False) -> None:
        self.demo = demo
        self.auth_state = "starting"  # starting|signed_in|signed_out|demo
        self.auth_message: str | None = None
        self.account: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []
        self.last_poll: float | None = None
        self.poll_error: str | None = None
        self.stream_status: dict[str, dict[str, Any]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.recent: dict[str, deque] = {}
        self.subscribers: set[asyncio.Queue] = set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "demo": self.demo,
            "auth": {"state": self.auth_state, "message": self.auth_message},
            "account": self.account,
            "devices": self.devices,
            "last_poll": self.last_poll,
            "poll_error": self.poll_error,
            "poll_interval": const.POLL_INTERVAL,
            "streams": self.stream_status,
            "latest": self.latest,
            "server_time": time.time(),
        }

    def publish(self, event: str, data: Any) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for q in list(self.subscribers):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # drop oldest for slow clients
            q.put_nowait(payload)

    def on_sample(self, station: str, s: VoltageData, store: Store) -> None:
        ts = s.timestamp.timestamp()
        point = {
            "st": station,
            "t": round(ts, 3),
            "v": round(s.voltage, 2),
            "hi": round(s.voltage_hi, 2),
            "lo": round(s.voltage_lo, 2),
        }
        self.latest[station] = point
        buf = self.recent.setdefault(station, deque(maxlen=RECENT_SECONDS * 6))
        buf.append(point)
        cutoff = ts - RECENT_SECONDS
        while buf and buf[0]["t"] < cutoff:
            buf.popleft()
        store.add_sample(station, ts, s.voltage, s.voltage_hi, s.voltage_lo)
        self.publish("sample", point)

    def on_stream_status(self, station: str, state: str, reason: str | None) -> None:
        self.stream_status[station] = {"state": state, "reason": reason, "since": time.time()}
        self.publish("state", self.snapshot())


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------

async def poll_loop(app: web.Application) -> None:
    hub: Hub = app["hub"]
    client: TingClient = app["client"]
    store: Store = app["store"]
    streams: dict[str, VoltageStream] = app["streams"]
    session: aiohttp.ClientSession = app["session"]
    last_notifications = 0.0
    last_prune = 0.0

    async def creds() -> tuple[str, int]:
        await client.get_access_token()
        assert client.api_key and client.user_id
        return client.api_key, client.user_id

    while True:
        if not client.signed_in:
            hub.auth_state = "signed_out"
            hub.auth_message = hub.auth_message or (
                "Not signed in. Add TING_EMAIL / TING_PASSWORD to .env, or run "
                "`docker compose run --rm ting python -m app.login`."
            )
            hub.publish("state", hub.snapshot())
            await asyncio.sleep(const.POLL_INTERVAL)
            client.reload()
            continue
        try:
            user = await client.get_user()
            hub.auth_state, hub.auth_message = "signed_in", None
            hub.account = {"first_name": user["first_name"], "email": client.username}
            hub.devices = user["devices"]
            hub.last_poll, hub.poll_error = time.time(), None
            for d in hub.devices:
                serial = d["serial"]
                if not serial:
                    continue
                store.log_status(serial, {
                    "efh": d["efh"]["level"], "ufh": d["ufh"]["level"],
                    "message": d["hazard_message"], "learning": d["learning_mode"],
                    "is_fire": d["is_fire"], "pq": d["power_quality_hazard"],
                    "frozen_pipe": d["has_frozen_pipe"],
                })
                if serial not in streams:
                    stream = VoltageStream(
                        session, serial, creds,
                        lambda st, s: hub.on_sample(st, s, store),
                        hub.on_stream_status,
                    )
                    streams[serial] = stream
                    stream.start()
            if time.time() - last_notifications >= const.NOTIFICATION_INTERVAL:
                store.upsert_notifications(await client.get_notifications())
                last_notifications = time.time()
        except NotSignedIn as err:
            _LOGGER.warning("Signed out: %s", err)
            hub.auth_state, hub.auth_message = "signed_out", str(err)
            client.sign_out()
            for s in streams.values():
                await s.stop()
            streams.clear()
        except TingApiError as err:
            _LOGGER.warning("Poll failed: %s", err)
            hub.poll_error = str(err)
        except Exception as err:  # noqa: BLE001 - keep polling
            _LOGGER.exception("Unexpected poll error")
            hub.poll_error = f"Unexpected error: {type(err).__name__}"
        if time.time() - last_prune > 86400:
            pruned = store.prune(const.RETENTION_DAYS)
            last_prune = time.time()
            _LOGGER.info("Pruned %d voltage buckets older than %d days", pruned, const.RETENTION_DAYS)
        hub.publish("state", hub.snapshot())
        await asyncio.sleep(const.POLL_INTERVAL)


async def flush_loop(app: web.Application) -> None:
    store: Store = app["store"]
    while True:
        await asyncio.sleep(30)
        # Close buckets that have ended so history is fresh even if the
        # stream stops mid-bucket.
        cutoff = int(time.time()) - store.bucket_seconds
        for station, bucket in list(store._open.items()):
            if bucket.ts < cutoff:
                store._flush(station, bucket)
                store._open.pop(station, None)


async def demo_loop(app: web.Application) -> None:
    """Synthetic data for previewing the UI without an account. Clearly
    flagged as demo in every API response and in the UI."""
    from datetime import UTC, datetime

    hub: Hub = app["hub"]
    store: Store = app["store"]
    serial = "DEMO0001"
    hub.auth_state, hub.account = "demo", {"first_name": "Demo", "email": None}
    hub.devices = [{
        "serial": serial, "name": "Demo Ting (synthetic data)", "type": "Ting",
        "firmware": "demo", "wifi_mac": None, "site_name": "Demo home",
        "subscription_start": None, "is_fire": False, "has_frozen_pipe": False,
        "power_quality_hazard": False, "learning_mode": False,
        "hazard_message": "No Hazards Detected",
        "efh": {"status": "OK", "level": "Low", "message": "No Hazards Detected", "timestamp": None, "color": None},
        "ufh": {"status": "OK", "level": "Low", "message": "No Hazards Detected", "timestamp": None, "color": None},
    }]
    hub.last_poll = time.time()
    # Backfill 24h so history charts have something to draw.
    now = time.time()
    with contextlib.suppress(Exception):
        for i in range(int(86400 / store.bucket_seconds), 0, -1):
            t = now - i * store.bucket_seconds
            base = 121 + 1.5 * math.sin(t / 7200) + random.gauss(0, 0.2)
            store.add_sample(serial, t, base, base + 0.6, base - 0.6)
        store.flush_all()
    hub.on_stream_status(serial, "live", None)
    while True:
        t = time.time()
        v = 121 + 1.5 * math.sin(t / 7200) + random.gauss(0, 0.25)
        sample = VoltageData(datetime.fromtimestamp(t, UTC), v, v + random.uniform(0.2, 0.9),
                             v - random.uniform(0.2, 0.9), v + 0.5)
        hub.on_sample(serial, sample, store)
        hub.last_poll = time.time()
        await asyncio.sleep(0.25)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

async def api_state(request: web.Request) -> web.Response:
    return web.json_response(request.app["hub"].snapshot())


def _station(request: web.Request) -> str:
    hub: Hub = request.app["hub"]
    station = request.query.get("station", "")
    if station not in {d["serial"] for d in hub.devices}:
        raise web.HTTPNotFound(text=json.dumps({"error": "unknown station"}),
                               content_type="application/json")
    return station


async def api_history(request: web.Request) -> web.Response:
    station = _station(request)
    rng = request.query.get("range", "24h")
    if rng not in RANGES:
        raise web.HTTPBadRequest(text=json.dumps({"error": "bad range"}),
                                 content_type="application/json")
    data = await asyncio.to_thread(request.app["store"].history, station, RANGES[rng])
    return web.json_response({"range": rng, **data})


async def api_recent(request: web.Request) -> web.Response:
    """Last 5 minutes. Falls back to stored buckets so a freshly opened page
    draws the whole window immediately instead of filling in from the right."""
    hub: Hub = request.app["hub"]
    station = _station(request)
    points = list(hub.recent.get(station, []))
    if len(points) < 30:
        hist = await asyncio.to_thread(request.app["store"].history, station, RECENT_SECONDS)
        seeded = [
            {"st": station, "t": p["t"] + hist["step"], "v": p["avg"], "hi": p["hi"], "lo": p["lo"]}
            for p in hist["points"]
        ]
        if len(seeded) > len(points):
            last = seeded[-1]["t"]
            seeded += [p for p in points if p["t"] > last]
            points = seeded
    return web.json_response(points)


async def api_events(request: web.Request) -> web.Response:
    store: Store = request.app["store"]
    station = request.query.get("station")
    return web.json_response({
        "notifications": await asyncio.to_thread(store.notifications, 100),
        "status_changes": await asyncio.to_thread(store.status_changes, station, 50)
        if station else [],
    })


async def api_live(request: web.Request) -> web.StreamResponse:
    hub: Hub = request.app["hub"]
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    hub.subscribers.add(q)
    try:
        await resp.write(f"event: state\ndata: {json.dumps(hub.snapshot())}\n\n".encode())
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15)
            except TimeoutError:
                payload = ": keepalive\n\n"
            await resp.write(payload.encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.subscribers.discard(q)
    return resp


async def healthz(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    return web.json_response({"ok": True, "auth": hub.auth_state})


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def security_headers(request: web.Request, handler):
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'",
    )
    return resp


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession()
    app["store"] = Store(const.DATA_DIR, const.BUCKET_SECONDS)
    app["streams"] = {}
    app["tasks"] = [asyncio.create_task(flush_loop(app))]
    hub: Hub = app["hub"]
    if hub.demo:
        app["tasks"].append(asyncio.create_task(demo_loop(app)))
        return
    client = TingClient(app["session"], AuthStore(const.DATA_DIR))
    app["client"] = client
    email, password = os.environ.get("TING_EMAIL"), os.environ.get("TING_PASSWORD")
    if not client.signed_in and email and password:
        try:
            await client.sign_in(email, password)
            _LOGGER.info("Signed in; refresh token saved. You may now remove TING_PASSWORD from .env.")
        except (NotSignedIn, TingApiError) as err:
            hub.auth_state, hub.auth_message = "signed_out", f"Sign-in failed: {err}"
            _LOGGER.error("Sign-in failed: %s", err)
    app["tasks"].append(asyncio.create_task(poll_loop(app)))


async def on_cleanup(app: web.Application) -> None:
    for task in app["tasks"]:
        task.cancel()
    for stream in app["streams"].values():
        await stream.stop()
    for task in app["tasks"]:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    app["store"].close()
    await app["session"].close()


def make_app(demo: bool | None = None) -> web.Application:
    if demo is None:
        demo = os.environ.get("TING_DEMO", "").lower() in ("1", "true", "yes")
    app = web.Application(middlewares=[security_headers])
    app["hub"] = Hub(demo=demo)
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/recent", api_recent)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/live", api_live)
    app.router.add_static("/static", STATIC_DIR, show_index=False)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    web.run_app(make_app(), host="0.0.0.0", port=const.PORT, access_log=None)


if __name__ == "__main__":
    main()
