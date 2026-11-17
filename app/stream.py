"""Realtime voltage stream from the Ting SignalR hub (~4 samples/second).

Connection semantics follow simplytoast1/ha-whisker-ting (MIT):
handshake -> UnInitializeStreaming (release stale server-side subscription)
-> InitializeStreaming, keepalive pings every 3 s, recycle on silence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

import aiohttp

from . import protocol
from .const import SIGNALR_URL
from .protocol import SignalRProtocolError, VoltageData

_LOGGER = logging.getLogger(__name__)

PING_INTERVAL = 3
STALE_SECONDS = 30
FIRST_DATA_GRACE = 60
MIN_DELAY, MAX_DELAY, REJECTED_DELAY = 5, 300, 1800

OnSample = Callable[[str, VoltageData], None]
OnStatus = Callable[[str, str, str | None], None]
Creds = Callable[[], Awaitable[tuple[str, int]]]


class StreamRejected(Exception):
    pass


class VoltageStream:
    """Maintains one station's stream forever, with backoff reconnects."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        station_id: str,
        creds: Creds,
        on_sample: OnSample,
        on_status: OnStatus,
    ) -> None:
        self._session = session
        self.station_id = station_id
        self._creds = creds
        self._on_sample = on_sample
        self._on_status = on_status
        self._msg_id = 0
        self._logged_fields = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"stream-{self.station_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def _invoke(self, method: str, args: list) -> bytes:
        self._msg_id += 1
        return protocol.encode_invocation(str(self._msg_id), method, args)

    async def _run(self) -> None:
        attempts = 0
        while True:
            delay = min(MIN_DELAY * 2**attempts, MAX_DELAY)
            got_data = False
            try:
                self._on_status(self.station_id, "connecting", None)
                got_data = await self._session_once()
                reason = "stream went silent"
            except asyncio.CancelledError:
                raise
            except StreamRejected as err:
                reason = f"subscription rejected: {err}"
                delay = REJECTED_DELAY
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                reason = f"{type(err).__name__}: {err}"[:200]
            attempts = 0 if got_data else attempts + 1
            if got_data:
                delay = MIN_DELAY
            _LOGGER.warning("Stream %s down (%s); retry in %ss", self.station_id, reason, delay)
            self._on_status(self.station_id, "reconnecting", reason)
            await asyncio.sleep(delay)

    async def _session_once(self) -> bool:
        """Run one connection until it fails. Returns True if data arrived."""
        api_key, user_id = await self._creds()
        args = [
            {"StationId": self.station_id, "DataElement": "ComboBinaryData"},
            api_key,
            str(user_id),
        ]
        got_data = False
        async with self._session.ws_connect(
            SIGNALR_URL,
            headers={"Origin": "ionic://localhost", "x-wl-api-key": api_key},
            heartbeat=None,
        ) as ws:
            await ws.send_str('{"protocol":"messagepack","version":1}\x1e')
            msg = await ws.receive(timeout=10)
            if msg.type not in (aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.TEXT):
                raise SignalRProtocolError(f"handshake response {msg.type.name}")
            text = msg.data.decode() if isinstance(msg.data, bytes) else msg.data
            if not text.endswith("\x1e"):
                raise SignalRProtocolError("unterminated handshake")
            hs = json.loads(text.split("\x1e", 1)[0] or "{}")
            if hs.get("error"):
                raise SignalRProtocolError(f"handshake failed: {hs['error']}")

            await ws.send_bytes(self._invoke("UnInitializeStreaming", args))
            await ws.send_bytes(self._invoke("InitializeStreaming", args))

            connected_at = last = time.monotonic()
            pinger = asyncio.create_task(self._ping(ws))
            try:
                while True:
                    try:
                        msg = await ws.receive(timeout=5)
                    except TimeoutError:
                        msg = None
                    now = time.monotonic()
                    if msg is not None:
                        if msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            raise ConnectionError("hub closed the socket")
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            for hub_msg in protocol.decode_hub_messages(msg.data):
                                if self._handle(hub_msg):
                                    if not got_data:
                                        got_data = True
                                        self._on_status(self.station_id, "live", None)
                                    last = now
                    if got_data and now - last > STALE_SECONDS:
                        break
                    if not got_data and now - connected_at > FIRST_DATA_GRACE:
                        break
            finally:
                pinger.cancel()
                if not ws.closed:
                    with contextlib.suppress(Exception):
                        await ws.send_bytes(self._invoke("UnInitializeStreaming", args))
        return got_data

    def _handle(self, message: list) -> bool:
        sample = protocol.decode_voltage_update(message)
        if sample is not None:
            if not self._logged_fields:
                # Log the payload's key names once per connection: it is how a
                # new or renamed field (THD, frequency, the app's "hi-fi" value)
                # is identified from the container log without a capture.
                self._logged_fields = True
                _LOGGER.info("Stream %s payload fields: %s",
                             self.station_id, ", ".join(sample.fields))
            self._on_sample(self.station_id, sample)
            return True
        if protocol.completion_message(message):
            error = protocol.completion_error(message)
            if error:
                raise StreamRejected(error)
        elif message and message[0] == protocol.MSG_TYPE_CLOSE:
            raise ConnectionError(f"hub close: {protocol.close_error(message)}")
        return False

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            while not ws.closed:
                await asyncio.sleep(PING_INTERVAL)
                await ws.send_bytes(protocol.encode_ping())
