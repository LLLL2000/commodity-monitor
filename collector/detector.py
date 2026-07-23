"""Port-call detection + per-terminal metrics.

State machine per (vessel, terminal), evaluated on each position update:

    OUTSIDE --(inside & slow)-------------------> PENDING
    PENDING --(sustained >= MIN_ARRIVAL_MIN)-----> INSIDE      => ARRIVAL
    PENDING --(leaves / speeds up)---------------> OUTSIDE
    INSIDE  --(leaves polygon >= grace)----------> OUTSIDE      => DEPARTURE (dwell recorded)

Commodity for a call comes from the terminal tag (authoritative); vessel class
is a fallback only at multi-commodity berths. Metrics are recomputed from the
SQLite event log so they are consistent and restart-safe.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config
from .ais import ais_type_to_class, is_stationary
from .registry import Registry, Terminal
from .store import Store

log = logging.getLogger("detector")
UTC = timezone.utc

OUTSIDE, PENDING, INSIDE = "OUTSIDE", "PENDING", "INSIDE"


@dataclass
class VesselState:
    mmsi: int
    lat: float = 0.0
    lon: float = 0.0
    sog: float | None = None
    cog: float | None = None
    nav: int | None = None
    vclass: str = "unknown"
    name: str | None = None
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    # per-terminal machine: terminal_id -> {"status","since","arrival","left_at"}
    calls: dict[str, dict] = field(default_factory=dict)


class Detector:
    def __init__(self, registry: Registry, store: Store):
        self.reg = registry
        self.store = store
        self.vessels: dict[int, VesselState] = {}
        # warm the in-memory class cache from persisted static data lazily

    # --- ingest handlers -------------------------------------------------
    def on_static(self, mmsi: int, name: str | None, ais_type: int | None,
                  length: float | None) -> None:
        vclass = ais_type_to_class(ais_type)
        self.store.upsert_static(mmsi, name, ais_type, vclass, length)
        v = self.vessels.get(mmsi)
        if v:
            v.vclass = vclass
            if name:
                v.name = name

    def on_position(self, mmsi: int, lat: float, lon: float, sog: float | None,
                    cog: float | None, nav: int | None,
                    ts: datetime | None = None) -> None:
        ts = ts or datetime.now(UTC)
        v = self.vessels.get(mmsi)
        if v is None:
            static = self.store.get_static(mmsi)
            v = VesselState(mmsi=mmsi,
                            vclass=(static or {}).get("vclass", "unknown"),
                            name=(static or {}).get("name"))
            self.vessels[mmsi] = v
        v.lat, v.lon, v.sog, v.cog, v.nav, v.last_seen = lat, lon, sog, cog, nav, ts

        slow = is_stationary(sog, nav, config.SOG_STOPPED_KN)

        # Which terminal (if any) currently contains the vessel.
        inside_term = next((t for t in self.reg.terminals if t.contains(lat, lon)), None)

        for t in self.reg.terminals:
            self._step(v, t, inside=(t is inside_term), slow=slow, ts=ts)

    # --- state machine ---------------------------------------------------
    def _step(self, v: VesselState, t: Terminal, inside: bool, slow: bool, ts: datetime) -> None:
        c = v.calls.get(t.id)
        status = c["status"] if c else OUTSIDE

        if status == OUTSIDE:
            if inside and slow:
                v.calls[t.id] = {"status": PENDING, "since": ts, "arrival": None, "left_at": None}
            return

        if status == PENDING:
            if not inside or not slow:
                # false start (passing through / didn't settle)
                v.calls[t.id]["status"] = OUTSIDE
                return
            if (ts - c["since"]) >= timedelta(minutes=config.MIN_ARRIVAL_MIN):
                c["status"] = INSIDE
                c["arrival"] = c["since"]  # credit arrival to first slow-inside fix
                c["left_at"] = None
                commodity = self.reg.attribute_commodity(t, v.vclass)
                self.store.add_event(v.mmsi, t.id, "arrival", ts=c["arrival"], commodity=commodity)
                log.info("ARRIVAL mmsi=%s terminal=%s commodity=%s", v.mmsi, t.id, commodity)
            return

        if status == INSIDE:
            if inside:
                c["left_at"] = None
                return
            # left the polygon — start grace timer, confirm departure after grace
            if c.get("left_at") is None:
                c["left_at"] = ts
                return
            if (ts - c["left_at"]) >= timedelta(minutes=config.DEPARTURE_GRACE_MIN):
                dwell_h = round((c["left_at"] - c["arrival"]).total_seconds() / 3600.0, 2)
                commodity = self.reg.attribute_commodity(t, v.vclass)
                self.store.add_event(v.mmsi, t.id, "departure", ts=c["left_at"],
                                     dwell_h=dwell_h, commodity=commodity)
                log.info("DEPARTURE mmsi=%s terminal=%s dwell_h=%.1f", v.mmsi, t.id, dwell_h)
                c["status"] = OUTSIDE
                c["arrival"] = None
                c["left_at"] = None

    # --- maintenance -----------------------------------------------------
    def prune_stale(self, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(UTC)
        cutoff = ts - timedelta(minutes=config.STALE_POSITION_MIN)
        stale = [m for m, v in self.vessels.items()
                 if v.last_seen < cutoff and all(cc["status"] != INSIDE for cc in v.calls.values())]
        for m in stale:
            del self.vessels[m]

    # --- metrics + snapshot ---------------------------------------------
    def _terminal_metrics(self, t: Terminal) -> dict:
        at_berth = sum(
            1 for v in self.vessels.values()
            if v.calls.get(t.id, {}).get("status") == INSIDE
        )
        queue = sum(
            1 for v in self.vessels.values()
            if v.calls.get(t.id, {}).get("status") != INSIDE
            and is_stationary(v.sog, v.nav, config.SOG_STOPPED_KN)
            and t.distance_km(v.lat, v.lon) <= t.anchorage_radius_km
        )
        dep7 = self.store.count_events(t.id, "departure", 7)
        dep30 = self.store.count_events(t.id, "departure", 30)
        arr7 = self.store.count_events(t.id, "arrival", 7)
        arr30 = self.store.count_events(t.id, "arrival", 30)
        med_dwell = self.store.median_dwell_h(t.id, 30)

        weekly = self.store.weekly_departures(t.id, config.BASELINE_WEEKS)
        idx, z, anomaly = self._baseline(t, dep7, weekly)

        return {
            "commodity": t.primary_commodity,
            "purity": t.purity,
            "polygon_verified": t.polygon_verified,
            "at_berth": at_berth,
            "queue": queue,
            "arrivals_7d": arr7,
            "arrivals_30d": arr30,
            "departures_7d": dep7,
            "departures_30d": dep30,
            "median_dwell_h": med_dwell,
            "activity_index": idx,
            "z": z,
            "anomaly": anomaly,
            "sparkline": self.store.departures_sparkline(t.id, 30),
        }

    def _baseline(self, t: Terminal, dep7: int, weekly: list[int]):
        """activity_index = current-week departures / median(trailing weekly).
        z = (current - mean)/std. Only high-purity, verified terminals with
        enough history are eligible to raise an anomaly."""
        usable = [w for w in weekly]  # includes zero-weeks; that's real signal
        n_nonzero_hist = len(usable)
        if n_nonzero_hist < config.MIN_BASELINE_WEEKS or sum(usable) == 0:
            return None, None, False
        med = statistics.median(usable)
        idx = round(dep7 / med, 2) if med > 0 else None
        z = None
        anomaly = False
        if len(usable) >= 2:
            mean = statistics.fmean(usable)
            sd = statistics.pstdev(usable)
            if sd > 0:
                z = round((dep7 - mean) / sd, 2)
                eligible = t.purity == "high" and (t.polygon_verified or not config.REQUIRE_VERIFIED_POLYGON)
                anomaly = eligible and abs(z) >= config.Z_ALERT
        return idx, z, anomaly

    def build_state(self, ts: datetime | None = None) -> dict:
        ts = ts or datetime.now(UTC)
        terminals_out = {t.id: self._terminal_metrics(t) for t in self.reg.terminals}

        # Publish only vessels near a terminal (keeps state.json small and the map relevant).
        vessels_out = []
        for v in self.vessels.values():
            nearest = None
            nearest_km = 1e9
            for t in self.reg.terminals:
                d = t.distance_km(v.lat, v.lon)
                if d < nearest_km:
                    nearest_km, nearest = d, t
            if nearest is None:
                continue
            at_berth_here = v.calls.get(nearest.id, {}).get("status") == INSIDE
            if nearest_km <= nearest.anchorage_radius_km or at_berth_here:
                vessels_out.append({
                    "mmsi": v.mmsi,
                    "name": v.name,
                    "lat": round(v.lat, 5),
                    "lon": round(v.lon, 5),
                    "class": v.vclass,
                    "sog": v.sog,
                    "near": nearest.id,
                    "at_berth": at_berth_here,
                })

        anomalies = [tid for tid, m in terminals_out.items() if m["anomaly"]]
        return {
            "updated": ts.astimezone(UTC).isoformat(),
            "vessel_count": len(vessels_out),
            "anomalies": anomalies,
            "vessels": vessels_out,
            "terminals": terminals_out,
        }
