"""SQLite persistence: bucketed voltage, hazard-status changes, notifications."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS voltage (
    station TEXT NOT NULL,
    ts INTEGER NOT NULL,          -- bucket start, unix seconds
    n INTEGER NOT NULL,
    v_avg REAL NOT NULL,
    v_min REAL NOT NULL,
    v_max REAL NOT NULL,
    hi_max REAL NOT NULL,
    lo_min REAL NOT NULL,
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS status_log (
    station TEXT NOT NULL,
    ts INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    time TEXT,
    data TEXT NOT NULL
);
"""

# Coarsest-first so the chart never returns more than ~MAX_POINTS points.
MAX_POINTS = 720


class Bucket:
    __slots__ = ("ts", "n", "total", "vmin", "vmax", "hi", "lo")

    def __init__(self, ts: int) -> None:
        self.ts, self.n, self.total = ts, 0, 0.0
        self.vmin = self.lo = float("inf")
        self.vmax = self.hi = float("-inf")

    def add(self, v: float, hi: float, lo: float) -> None:
        self.n += 1
        self.total += v
        self.vmin = min(self.vmin, v)
        self.vmax = max(self.vmax, v)
        self.hi = max(self.hi, hi)
        self.lo = min(self.lo, lo)


class Store:
    def __init__(self, data_dir: str, bucket_seconds: int) -> None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            Path(data_dir) / "ting.db", check_same_thread=False, isolation_level=None
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.bucket_seconds = bucket_seconds
        self._open: dict[str, Bucket] = {}
        self._last_status: dict[str, str] = {}

    # --- voltage -----------------------------------------------------
    def add_sample(self, station: str, ts: float, v: float, hi: float, lo: float) -> None:
        start = int(ts) - int(ts) % self.bucket_seconds
        bucket = self._open.get(station)
        if bucket is not None and bucket.ts != start:
            self._flush(station, bucket)
            bucket = None
        if bucket is None:
            bucket = self._open[station] = Bucket(start)
        bucket.add(v, hi, lo)

    def _flush(self, station: str, b: Bucket) -> None:
        if not b.n:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO voltage VALUES (?,?,?,?,?,?,?,?)",
                (station, b.ts, b.n, b.total / b.n, b.vmin, b.vmax, b.hi, b.lo),
            )

    def flush_all(self) -> None:
        for station, bucket in list(self._open.items()):
            self._flush(station, bucket)
        self._open.clear()

    def history(self, station: str, seconds: int) -> dict[str, Any]:
        now = int(time.time())
        since = now - seconds
        b = self.bucket_seconds
        step = max(b, -(-seconds // MAX_POINTS))
        step = -(-step // b) * b  # round up to a whole number of buckets
        with self._lock:
            rows = self._db.execute(
                """
                SELECT (ts / ?) * ? AS t,
                       SUM(v_avg * n) / SUM(n), MIN(v_min), MAX(v_max),
                       MAX(hi_max), MIN(lo_min), SUM(n)
                FROM voltage WHERE station = ? AND ts >= ?
                GROUP BY t ORDER BY t
                """,
                (step, step, station, since),
            ).fetchall()
            stats = self._db.execute(
                "SELECT SUM(v_avg*n)/SUM(n), MIN(v_min), MAX(v_max), MAX(hi_max),"
                " MIN(lo_min), SUM(n), COUNT(*) FROM voltage WHERE station=? AND ts>=?",
                (station, since),
            ).fetchone()
        return {
            "step": step,
            "since": since,
            "points": [
                {"t": r[0], "avg": round(r[1], 2), "min": round(r[2], 2),
                 "max": round(r[3], 2), "hi": round(r[4], 2), "lo": round(r[5], 2)}
                for r in rows
            ],
            "stats": None if not stats or stats[5] is None else {
                "avg": round(stats[0], 2), "min": round(stats[1], 2),
                "max": round(stats[2], 2), "peak_hi": round(stats[3], 2),
                "trough_lo": round(stats[4], 2), "samples": stats[5],
                "coverage": round(min(1.0, stats[6] * self.bucket_seconds / seconds), 3),
            },
        }

    # --- status ------------------------------------------------------
    def log_status(self, station: str, data: dict[str, Any]) -> bool:
        """Record a status snapshot only when it changed. Returns True if logged."""
        key = json.dumps(data, sort_keys=True)
        if self._last_status.get(station) is None:
            with self._lock:
                row = self._db.execute(
                    "SELECT data FROM status_log WHERE station=? ORDER BY ts DESC LIMIT 1",
                    (station,),
                ).fetchone()
            if row:
                self._last_status[station] = row[0]
        if self._last_status.get(station) == key:
            return False
        self._last_status[station] = key
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO status_log VALUES (?,?,?)",
                (station, int(time.time()), key),
            )
        return True

    def status_changes(self, station: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, data FROM status_log WHERE station=? ORDER BY ts DESC LIMIT ?",
                (station, limit),
            ).fetchall()
        return [{"ts": ts, **json.loads(data)} for ts, data in rows]

    # --- notifications -----------------------------------------------
    def upsert_notifications(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO notifications VALUES (?,?,?)",
                [
                    (str(n["id"]), n.get("time"), json.dumps(n))
                    for n in items
                    if n.get("id") is not None
                ],
            )

    def notifications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM notifications ORDER BY time DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # --- maintenance -------------------------------------------------
    def prune(self, retention_days: int) -> int:
        cutoff = int(time.time()) - retention_days * 86400
        with self._lock:
            cur = self._db.execute("DELETE FROM voltage WHERE ts < ?", (cutoff,))
        return cur.rowcount

    def close(self) -> None:
        self.flush_all()
        self._db.close()
