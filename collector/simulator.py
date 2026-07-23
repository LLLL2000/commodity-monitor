"""Synthetic AIS feed so the whole pipeline runs with NO API key.

Two jobs:
  1) seed_history(): backfill the SQLite event log with realistic weekly
     departure counts per terminal so activity_index / z-score are meaningful
     immediately (and one terminal is nudged into an anomaly so alerting is
     demonstrable).
  2) run_sim(): drive live-ish PositionReport/ShipStaticData through the
     detector at accelerated time — vessels approach a terminal, slow inside
     the berth box (arrival), dwell, then leave (departure), plus a couple of
     vessels loitering in the anchorage (queue).

This is a *demo/test harness*, not a model of real traffic.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from . import config
from .detector import Detector
from .registry import Registry

log = logging.getLogger("sim")
UTC = timezone.utc


def seed_history(reg: Registry, det: Detector, weeks: int = 14, seed: int = 7) -> None:
    rng = random.Random(seed)
    now = datetime.now(UTC)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    # baseline weekly departures roughly scaled by purity/port size
    base_rate = {"high": 4, "medium": 6, "low": 9}
    for t in reg.terminals:
        mean = base_rate.get(t.purity, 4)
        for i in range(weeks, 0, -1):
            start = week_start - timedelta(weeks=i)
            n = max(0, int(rng.gauss(mean, max(1.0, mean * 0.3))))
            # nudge the flagship high-purity terminal into a recent spike
            if t.id == "coloso" and i <= 1:
                n = mean * 3
            for _ in range(n):
                offset = timedelta(days=rng.uniform(0, 6.5), hours=rng.uniform(0, 23))
                arr = start + offset
                dwell = round(rng.uniform(18, 40), 1)
                dep = arr + timedelta(hours=dwell)
                mmsi = 200000000 + rng.randint(0, 8_000_000)
                det.store.add_event(mmsi, t.id, "arrival", ts=arr, commodity=t.primary_commodity)
                det.store.add_event(mmsi, t.id, "departure", ts=dep, dwell_h=dwell,
                                    commodity=t.primary_commodity)
    log.info("seeded ~%d weeks of history for %d terminals", weeks, len(reg.terminals))


async def run_sim(reg: Registry, det: Detector, stop: asyncio.Event,
                  tick_sleep: float = 0.02, sim_step_min: float = 4.0) -> None:
    """Accelerated sim: each tick advances virtual time by sim_step_min minutes."""
    rng = random.Random(42)
    t = reg.terminals[0]  # drive a live berthing at the first terminal (coloso)
    tq = reg.terminals[1] if len(reg.terminals) > 1 else t
    vt = datetime.now(UTC) - timedelta(hours=6)

    # one docking vessel + two anchorage loiterers (queue)
    dock_mmsi = 311000111
    det.on_static(dock_mmsi, "SIM BULK ALPHA", 70, 250.0)
    queue_mmsi = [311000222, 311000333]
    for i, m in enumerate(queue_mmsi):
        det.on_static(m, f"SIM QUEUE {i}", 70, 220.0)

    # approach waypoints -> into berth -> dwell -> depart
    blat, blon = t.centroid_lat, t.centroid_lon
    phases = (
        [("approach", blat - 0.20 + j * 0.02, blon - 0.20 + j * 0.02, 12.0) for j in range(10)]
        + [("berth", blat, blon, 0.1)] * 30          # sit at berth (=> arrival after MIN_ARRIVAL_MIN)
        + [("leave", blat + 0.05 + j * 0.03, blon + 0.05 + j * 0.03, 11.0) for j in range(8)]
    )

    step = 0
    while not stop.is_set() and step < len(phases) + 5:
        _, plat, plon, psog = phases[min(step, len(phases) - 1)]
        det.on_position(dock_mmsi, plat, plon, sog=psog, cog=45.0,
                        nav=(5 if psog < 1 else 0), ts=vt)
        # queue vessels loiter just inside the anchorage radius, anchored
        for k, m in enumerate(queue_mmsi):
            qlat = tq.centroid_lat + 0.04 + 0.005 * k
            qlon = tq.centroid_lon + 0.04
            det.on_position(m, qlat, qlon, sog=0.0, cog=0.0, nav=1, ts=vt)
        # a little background noise: a fishing boat passing by
        det.on_position(412999999, blat + rng.uniform(-0.3, 0.3),
                        blon + rng.uniform(-0.3, 0.3), sog=6.0, cog=90.0, nav=0, ts=vt)

        vt += timedelta(minutes=sim_step_min)
        step += 1
        await asyncio.sleep(tick_sleep)

    log.info("simulation sequence complete (dock vessel arrived + departed)")
