"""Network-free unit tests for jobs/flows_job.py (period math + aggregation + contract)."""
from __future__ import annotations

from pathlib import Path

from collector.registry import Registry
from jobs import flows_job

ROOT = Path(__file__).resolve().parents[2]


def test_period_helpers():
    assert flows_job._shift_period("202601", -1) == "202512"
    assert flows_job._shift_period("202512", 1) == "202601"
    assert flows_job._period_range("202603", 3) == ["202601", "202602", "202603"]
    assert flows_job._fmt_period("202605") == "2026-05"


def _row(period, partner_iso, partner_code, cmd, net_kg, value, partner2="0"):
    return {"period": period, "partnerISO": partner_iso, "partnerCode": partner_code,
            "partner2Code": partner2, "cmdCode": cmd, "netWgt": net_kg, "primaryValue": value}


def test_aggregate_skips_world_and_second_partner():
    attr = {"2603": "copper"}.get
    rows = [
        _row("202605", "W00", 0, "2603", 999_000_000, 9e9),      # World -> skipped
        _row("202605", "CN", 156, "2603", 200_000_000, 1.8e9),   # 200 kt
        _row("202605", "JP", 392, "2603", 50_000_000, 0.5e9),    # 50 kt
        _row("202605", "CN", 156, "2603", 5_000_000, 4e7, partner2="842"),  # 2nd-partner -> skipped
    ]
    agg = flows_job._aggregate(rows, attr)
    cell = agg["202605"]["copper"]
    assert round(cell["tonnes"]) == 250_000          # 200kt + 50kt, World & partner2 excluded
    assert round(cell["value_usd"]) == 2_300_000_000
    assert round(cell["dest"]["CN"]) == 200_000


def test_destinations_top_n_plus_other():
    dest = {"CN": 200.0, "JP": 50.0, "KR": 30.0, "IN": 20.0, "DE": 10.0, "US": 5.0, "NL": 5.0}
    out = flows_job._destinations(dest, total=320.0, top_n=5)
    assert [d["partner"] for d in out] == ["CN", "JP", "KR", "IN", "DE", "other"]
    assert out[-1]["tonnes"] == 10  # US + NL folded into 'other'
    assert abs(sum(d["share"] for d in out) - 1.0) < 0.02


def test_registry_hs_attribution():
    """The core fix: lithium subheadings must resolve to lithium even though they
    live on copper-primary dual-commodity terminals; copper headings to copper."""
    reg = Registry.load(ROOT / "registry.json")
    assert reg.commodity_for_hs("2603") == "copper"
    assert reg.commodity_for_hs("7403") == "copper"
    assert reg.commodity_for_hs("2836.91") == "lithium"
    assert reg.commodity_for_hs("283691") == "lithium"   # normalized form too
    assert reg.commodity_for_hs("2825.20") == "lithium"


def test_build_flows_splits_copper_and_lithium():
    """Copper (2603) and lithium (283691) rows from the same reporter must land
    in separate flow rows / trend series, not be merged under copper."""
    reg = Registry.load(ROOT / "registry.json")

    def fake_fetch(reporter, headings, periods):
        rows = []
        for p in periods:
            rows.append(_row(p, "CN", 156, "2603", 100_000_000, 9e8))     # copper
            if reporter == "152":  # Chile also exports lithium
                rows.append(_row(p, "CN", 156, "283691", 4_000_000, 3e7))  # lithium
        return rows

    data = flows_job.build_flows(reg, "202605", trend_months=3, fetch=fake_fetch)
    commodities = {(f["country"], f["commodity"]) for f in data["flows"]}
    assert ("CL", "copper") in commodities
    assert ("CL", "lithium") in commodities
    assert "CL_lithium" in data["trend"] and "CL_copper" in data["trend"]
    li = next(f for f in data["flows"] if f["country"] == "CL" and f["commodity"] == "lithium")
    assert li["hs_codes"] == ["2825.20", "2836.91"]  # all CL lithium codes, registry dotted form
    assert li["tonnes"] == 4000                        # 4,000,000 kg -> 4,000 t, NOT under copper


def test_build_flows_full_contract():
    reg = Registry.load(ROOT / "registry.json")
    periods_seen = {}

    def fake_fetch(reporter, headings, periods):
        periods_seen[reporter] = list(periods)
        # emit copper exports (HS 2603) for every requested period, rising over time
        rows = []
        for i, p in enumerate(periods):
            base = 100_000_000 + i * 10_000_000  # kg
            rows.append(_row(p, "CN", 156, "2603", base, base * 9))
            rows.append(_row(p, "JP", 392, "2603", base // 4, base // 4 * 9))
        return rows

    data = flows_job.build_flows(reg, "202605", trend_months=6, fetch=fake_fetch)

    # top-level contract
    assert data["period"] == "2026-05"
    assert data["source"] == "UN Comtrade"
    assert "lag_note" in data and data["updated"].endswith("Z")

    # trend object: one series per country_commodity + trend_periods, rising
    assert data["trend"]["trend_periods"] == ["2025-12", "2026-01", "2026-02",
                                              "2026-03", "2026-04", "2026-05"]
    cl = data["trend"]["CL_copper"]
    assert len(cl) == 6 and cl[0] < cl[-1]

    # requested 6 monthly periods ending at latest, in one call per reporter
    assert periods_seen["152"] == ["202512", "202601", "202602", "202603", "202604", "202605"]

    # latest-period flow rows carry hs_codes + destinations
    cl_copper = next(f for f in data["flows"] if f["country"] == "CL" and f["commodity"] == "copper")
    assert "2603" in cl_copper["hs_codes"]
    assert cl_copper["destinations"][0]["partner"] == "CN"
    assert cl_copper["tonnes"] > 0
