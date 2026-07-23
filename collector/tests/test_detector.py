"""Detector unit tests — no network, no live services.

We drive positions through the state machine at controlled virtual timestamps
and assert arrivals/departures/dwell/queue/anomaly behave per spec (§5).
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from collector import config
from collector.detector import Detector, INSIDE
from collector.registry import Registry
from collector.store import Store

UTC = timezone.utc

# A tiny registry with one tight, VERIFIED high-purity terminal so anomalies can fire.
REG = {
    "commodity_taxonomy": {"metals": ["copper", "lithium"]},
    "vessel_class_hint": {"bulk_carrier": ["copper"], "container": ["lithium"]},
    "terminals": [{
        "id": "t1", "name": "Test Terminal", "country": "CL",
        "primary_commodity": "copper", "commodities": ["copper"], "hs_codes": ["2603"],
        "purity": "high", "expected_vessel_classes": ["bulk_carrier"],
        "centroid": [0.0, 0.0],
        "berth_polygon": [[-0.01, -0.01], [0.01, -0.01], [0.01, 0.01], [-0.01, 0.01]],
        "anchorage_radius_km": 15, "polygon_verified": True, "source": "test",
    }, {
        "id": "t2", "name": "Multi Terminal", "country": "CL",
        "primary_commodity": "copper", "commodities": ["copper", "lithium"], "hs_codes": ["2603"],
        "purity": "medium", "expected_vessel_classes": ["bulk_carrier", "container"],
        "centroid": [10.0, 10.0],
        "berth_polygon": [[9.99, 9.99], [10.01, 9.99], [10.01, 10.01], [9.99, 10.01]],
        "anchorage_radius_km": 15, "polygon_verified": True, "source": "test",
    }],
}

INSIDE_PT = (0.0, 0.0)      # centre of t1 berth box
OUTSIDE_PT = (0.5, 0.5)     # ~70 km away, well outside


@pytest.fixture
def det(tmp_path):
    reg = Registry(REG)
    store = Store(tmp_path / "t.db")
    return Detector(reg, store)


def _feed(det, mmsi, pt, sog, nav, ts):
    det.on_position(mmsi, pt[0], pt[1], sog=sog, cog=0.0, nav=nav, ts=ts)


def test_arrival_after_min_dwell(det):
    # windows are relative to real 'now', so use recent timestamps
    t0 = datetime.now(UTC) - timedelta(minutes=90)
    det.on_static(1, "BULK", 70, 250.0)            # -> class "cargo"
    _feed(det, 1, OUTSIDE_PT, 12.0, 0, t0)          # transiting outside
    _feed(det, 1, INSIDE_PT, 0.1, 5, t0 + timedelta(minutes=1))   # slow inside -> PENDING
    # not yet enough time -> still PENDING, no arrival event
    assert det.store.count_events("t1", "arrival", 30) == 0
    _feed(det, 1, INSIDE_PT, 0.1, 5, t0 + timedelta(minutes=config.MIN_ARRIVAL_MIN + 2))
    assert det.store.count_events("t1", "arrival", 30) == 1
    assert det.vessels[1].calls["t1"]["status"] == INSIDE
    st = det.build_state(t0 + timedelta(minutes=config.MIN_ARRIVAL_MIN + 2))
    assert st["terminals"]["t1"]["at_berth"] == 1


def test_departure_records_dwell(det):
    # arrival ~40h ago, ~30h dwell, departure ~10h ago (all within the 30d window)
    t0 = datetime.now(UTC) - timedelta(hours=40)
    _feed(det, 2, INSIDE_PT, 0.1, 5, t0)
    _feed(det, 2, INSIDE_PT, 0.1, 5, t0 + timedelta(minutes=config.MIN_ARRIVAL_MIN + 1))
    # leaves polygon; departure confirmed only after grace
    leave = t0 + timedelta(hours=30)
    _feed(det, 2, OUTSIDE_PT, 11.0, 0, leave)
    assert det.store.count_events("t1", "departure", 30) == 0   # within grace
    _feed(det, 2, OUTSIDE_PT, 11.0, 0, leave + timedelta(minutes=config.DEPARTURE_GRACE_MIN + 1))
    assert det.store.count_events("t1", "departure", 30) == 1
    dwell = det.store.median_dwell_h("t1", 30)
    assert dwell is not None and 29 <= dwell <= 31    # ~30h dwell (credited to leave time)


def test_transit_does_not_trigger_arrival(det):
    """A fast pass straight through the polygon must NOT count as a call."""
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    _feed(det, 3, OUTSIDE_PT, 12.0, 0, t0)
    _feed(det, 3, INSIDE_PT, 12.0, 0, t0 + timedelta(minutes=1))   # inside but FAST
    _feed(det, 3, OUTSIDE_PT, 12.0, 0, t0 + timedelta(minutes=2))
    assert det.store.count_events("t1", "arrival", 30) == 0


def test_queue_counts_anchored_nearby(det):
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    # anchored ~5 km from centroid (inside 15 km anchorage), not at berth
    _feed(det, 4, (0.045, 0.0), 0.0, 1, t0)
    st = det.build_state(t0)
    assert st["terminals"]["t1"]["queue"] == 1
    assert st["terminals"]["t1"]["at_berth"] == 0


def test_anomaly_requires_high_purity_and_history(det):
    now = datetime.now(UTC)
    ws = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    # 12 quiet historical weeks with realistic variance (mean ~2/week, sd > 0)
    hist = [1, 3, 2, 2, 1, 3, 2, 1, 3, 2, 2, 3]
    for i in range(12, 0, -1):
        start = ws - timedelta(weeks=i)
        for j in range(hist[i - 1]):
            det.store.add_event(900 + j, "t1", "departure",
                                ts=start + timedelta(days=j), dwell_h=24.0, commodity="copper")
    # this week: a big spike (10 departures) -> should be anomalous at high-purity t1
    for j in range(10):
        det.store.add_event(950 + j, "t1", "departure",
                            ts=now - timedelta(hours=j), dwell_h=24.0, commodity="copper")
    st = det.build_state(now)
    m = st["terminals"]["t1"]
    assert m["z"] is not None and m["z"] > 2
    assert m["anomaly"] is True

    # same spike at medium-purity t2 must NOT raise an anomaly
    for i in range(12, 0, -1):
        start = ws - timedelta(weeks=i)
        for j in range(hist[i - 1]):
            det.store.add_event(800 + j, "t2", "departure",
                                ts=start + timedelta(days=j), dwell_h=24.0, commodity="copper")
    for j in range(10):
        det.store.add_event(850 + j, "t2", "departure",
                            ts=now - timedelta(hours=j), dwell_h=24.0, commodity="copper")
    st2 = det.build_state(now)
    assert st2["terminals"]["t2"]["anomaly"] is False


def test_multi_commodity_attribution_falls_back(det):
    """At a multi-commodity berth, commodity attribution uses the vessel class
    fallback; a container ship -> lithium, a cargo/bulk -> copper."""
    reg = det.reg
    t2 = next(t for t in reg.terminals if t.id == "t2")
    assert reg.attribute_commodity(t2, "cargo") == "copper"      # bulk_carrier hint -> copper
    # 'container' isn't emitted by AIS, but if a downstream source supplied it:
    assert reg.attribute_commodity(t2, "container") == "lithium"
