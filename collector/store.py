"""Tiny SQLite persistence layer.

Two things need to outlive process restarts: (1) the port-call event log, which
is what activity baselines are computed from, and (2) the per-MMSI static cache
(ship name/type/class), because ShipStaticData arrives far less often than
PositionReports. Live vessel positions stay in memory (ephemeral by nature).
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                mmsi       INTEGER NOT NULL,
                terminal   TEXT    NOT NULL,
                kind       TEXT    NOT NULL,          -- 'arrival' | 'departure'
                ts         TEXT    NOT NULL,          -- ISO8601 UTC
                dwell_h    REAL,                      -- set on departures
                commodity  TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_events_term_ts ON events(terminal, kind, ts);

            CREATE TABLE IF NOT EXISTS vessel_static (
                mmsi     INTEGER PRIMARY KEY,
                name     TEXT,
                ais_type INTEGER,
                vclass   TEXT,
                length   REAL,
                updated  TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_log (
                terminal TEXT PRIMARY KEY,
                last_ts  TEXT
            );
            """
        )
        self.db.commit()

    # --- events ----------------------------------------------------------
    def add_event(self, mmsi: int, terminal: str, kind: str,
                  ts: datetime | None = None, dwell_h: float | None = None,
                  commodity: str | None = None) -> None:
        ts = ts or _now()
        self.db.execute(
            "INSERT INTO events(mmsi, terminal, kind, ts, dwell_h, commodity) VALUES (?,?,?,?,?,?)",
            (mmsi, terminal, kind, ts.astimezone(UTC).isoformat(), dwell_h, commodity),
        )
        self.db.commit()

    def count_events(self, terminal: str, kind: str, days: float) -> int:
        since = (_now() - timedelta(days=days)).isoformat()
        row = self.db.execute(
            "SELECT COUNT(*) FROM events WHERE terminal=? AND kind=? AND ts>=?",
            (terminal, kind, since),
        ).fetchone()
        return int(row[0])

    def median_dwell_h(self, terminal: str, days: float = 30) -> float | None:
        since = (_now() - timedelta(days=days)).isoformat()
        rows = self.db.execute(
            "SELECT dwell_h FROM events WHERE terminal=? AND kind='departure' AND dwell_h IS NOT NULL AND ts>=?",
            (terminal, since),
        ).fetchall()
        vals = [r[0] for r in rows]
        return round(statistics.median(vals), 1) if vals else None

    def weekly_departures(self, terminal: str, weeks: int) -> list[int]:
        """Departure counts per ISO week for the trailing `weeks` weeks,
        oldest first, EXCLUDING the current (incomplete) week."""
        now = _now()
        # start of current ISO week (Monday 00:00 UTC)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        buckets: list[int] = []
        for i in range(weeks, 0, -1):
            start = week_start - timedelta(weeks=i)
            end = start + timedelta(weeks=1)
            row = self.db.execute(
                "SELECT COUNT(*) FROM events WHERE terminal=? AND kind='departure' AND ts>=? AND ts<?",
                (terminal, start.isoformat(), end.isoformat()),
            ).fetchone()
            buckets.append(int(row[0]))
        return buckets

    def departures_sparkline(self, terminal: str, days: int = 30) -> list[int]:
        """Daily departure counts for the last `days` days (oldest first)."""
        now = _now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        out: list[int] = []
        for i in range(days - 1, -1, -1):
            start = today - timedelta(days=i)
            end = start + timedelta(days=1)
            row = self.db.execute(
                "SELECT COUNT(*) FROM events WHERE terminal=? AND kind='departure' AND ts>=? AND ts<?",
                (terminal, start.isoformat(), end.isoformat()),
            ).fetchone()
            out.append(int(row[0]))
        return out

    # --- static cache ----------------------------------------------------
    def upsert_static(self, mmsi: int, name: str | None, ais_type: int | None,
                      vclass: str | None, length: float | None) -> None:
        self.db.execute(
            """INSERT INTO vessel_static(mmsi,name,ais_type,vclass,length,updated)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(mmsi) DO UPDATE SET
                 name=COALESCE(excluded.name,name),
                 ais_type=COALESCE(excluded.ais_type,ais_type),
                 vclass=COALESCE(excluded.vclass,vclass),
                 length=COALESCE(excluded.length,length),
                 updated=excluded.updated""",
            (mmsi, name, ais_type, vclass, length, _now().isoformat()),
        )
        self.db.commit()

    def get_static(self, mmsi: int) -> dict | None:
        row = self.db.execute(
            "SELECT mmsi,name,ais_type,vclass,length FROM vessel_static WHERE mmsi=?",
            (mmsi,),
        ).fetchone()
        if not row:
            return None
        return {"mmsi": row[0], "name": row[1], "ais_type": row[2],
                "vclass": row[3], "length": row[4]}

    # --- alert dedupe ----------------------------------------------------
    def alert_recent(self, terminal: str, cooldown_h: float) -> bool:
        row = self.db.execute("SELECT last_ts FROM alert_log WHERE terminal=?", (terminal,)).fetchone()
        if not row:
            return False
        last = datetime.fromisoformat(row[0])
        return (_now() - last) < timedelta(hours=cooldown_h)

    def mark_alert(self, terminal: str) -> None:
        self.db.execute(
            "INSERT INTO alert_log(terminal,last_ts) VALUES (?,?) "
            "ON CONFLICT(terminal) DO UPDATE SET last_ts=excluded.last_ts",
            (terminal, _now().isoformat()),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
