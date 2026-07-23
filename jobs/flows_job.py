#!/usr/bin/env python3
"""Phase 2 — structural flows from UN Comtrade -> flows.json.

STATUS: complete & registry-driven. Endpoint VERIFIED: the current UN Comtrade
v1 goods endpoint is `https://comtradeapi.un.org/data/v1/get/C/M/HS` and it
requires a subscription key sent as the `Ocp-Apim-Subscription-Key` header (the
legacy no-key `comtrade.un.org/api` was retired). Set COMTRADE_KEY (free tier
works) and the job pulls real monthly exports; without it, the existing
flows.json is preserved rather than overwritten with fake data.

Design invariant: which countries / commodities / HS codes to pull is derived
entirely from registry.json (country_meta + terminals). Adding coverage = edit
the registry.

What it emits (spec §6 contract): the latest period as `flows[]` (per country ×
commodity, with destination split + hs_codes) plus a `trend` object carrying the
last N months of tonnage per `country_commodity` for the sidebar sparklines.

Run: python -m jobs.flows_job    (from repo root)
Schedule via .github/workflows/flows.yml (monthly cron; period defaults to
current month - 2 to respect the customs reporting lag).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.registry import Registry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s flows_job %(levelname)s %(message)s")
log = logging.getLogger("flows_job")

COMTRADE_KEY = os.environ.get("COMTRADE_KEY", "")
# VERIFIED path (2026): v1 goods, Monthly frequency, HS classification.
COMTRADE_URL = os.environ.get("COMTRADE_URL", "https://comtradeapi.un.org/data/v1/get/C/M/HS")
TREND_MONTHS = int(os.environ.get("FLOWS_TREND_MONTHS", "6"))
LAG_NOTE = "Customs data typically lags 1-3 months; national sources (Cochilco/SUNAT) lead it."
OUT = ROOT / "data" / "flows.json"
WEB_OUT = ROOT / "web" / "data" / "flows.json"  # keep the served copy in sync (best-effort)


# --- period helpers -------------------------------------------------------
def _shift_period(yyyymm: str, delta_months: int) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    idx = y * 12 + (m - 1) + delta_months
    return f"{idx // 12:04d}{idx % 12 + 1:02d}"


def _period_range(latest: str, n: int) -> list[str]:
    """[oldest ... latest], length n, ending at `latest` (YYYYMM)."""
    return [_shift_period(latest, -k) for k in range(n - 1, -1, -1)]


def _fmt_period(yyyymm: str) -> str:
    return f"{yyyymm[:4]}-{yyyymm[4:6]}"


def _default_period() -> str:
    now = datetime.now(timezone.utc)
    return _shift_period(f"{now.year:04d}{now.month:02d}", -2)  # respect ~2-month customs lag


# --- registry-derived query planning --------------------------------------
# Attribution and query granularity both come from the registry (registry.py is
# the sole reader). We query Comtrade with the EXACT codes the registry lists
# (so 6-digit lithium subheadings like 283691 aren't collapsed to all-carbonates
# 2836) and attribute returned rows via reg.commodity_for_hs().
def _norm(code: str) -> str:
    return str(code).replace(".", "").strip()


def _planned_queries(reg: Registry):
    """Yield (iso2, reporter_code, [normalized_hs_codes]) per producing country."""
    per_country: dict[str, set[str]] = defaultdict(set)
    for t in reg.terminals:
        for hs in t.hs_codes:
            per_country[t.country].add(_norm(hs))
    for iso2, codes in per_country.items():
        meta = reg.country_meta.get(iso2)
        if not meta or not meta.get("comtrade_reporter"):
            log.warning("no comtrade_reporter for %s in registry.country_meta; skipping", iso2)
            continue
        yield iso2, meta["comtrade_reporter"], sorted(codes)


def _country_commodity_hs(reg: Registry) -> dict[str, dict[str, set[str]]]:
    """iso2 -> commodity -> {original HS code strings}, for each flow row's
    `hs_codes` display list (keeps the registry's dotted form, e.g. 2836.91)."""
    m: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for t in reg.terminals:
        for hs in t.hs_codes:
            commodity = reg.commodity_for_hs(hs) or t.primary_commodity
            m[t.country][commodity].add(hs)
    return m


# --- Comtrade fetch (single clearly-scoped function) ----------------------
def fetch_comtrade(reporter: str, hs_headings: list[str], periods: list[str]) -> list[dict]:
    """Query Comtrade monthly exports for several periods at once. Returns raw
    rows. Requires COMTRADE_KEY. Aggregate params force one row per (partner,
    period, cmd) so we don't double-count mode-of-transport / customs / second-
    partner breakdowns."""
    import requests  # local import so the module imports without requests present

    params = {
        "reporterCode": reporter,
        "period": ",".join(periods),   # up to 12 monthly periods per call
        "flowCode": "X",               # exports
        "cmdCode": ",".join(hs_headings),
        "partnerCode": "",             # all partners (destination split)
        "partner2Code": "0",           # aggregate over 2nd partner
        "motCode": "0",                # aggregate over mode of transport
        "customsCode": "C00",          # aggregate over customs procedure
    }
    headers = {"Ocp-Apim-Subscription-Key": COMTRADE_KEY}
    r = requests.get(COMTRADE_URL, params=params, headers=headers, timeout=90)
    r.raise_for_status()
    return r.json().get("data", [])


# --- aggregation ----------------------------------------------------------
def _is_world(code) -> bool:
    return str(code) in ("0", "", "None")


def _aggregate(rows: list[dict], attribute) -> dict[str, dict[str, dict]]:
    """rows -> agg[period][commodity] = {tonnes, value_usd, dest{partner: tonnes}}.

    `attribute` maps an HS cmdCode -> commodity (reg.commodity_for_hs). Skips the
    World-total partner row (partnerCode 0) and any second-partner breakdown; the
    country total is the sum over destination partners so the destination shares
    are internally consistent.
    """
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if not _is_world(row.get("partner2Code", "0")):
            continue  # defensive: drop 2nd-partner breakdown if the API returned it
        if _is_world(row.get("partnerCode", "0")):
            continue  # drop World aggregate; we sum partners instead
        cmd = str(row.get("cmdCode", ""))
        commodity = attribute(cmd)
        if commodity is None:
            log.warning("unmapped HS code %r in Comtrade rows; add it to registry.hs_commodity", cmd)
            continue
        period = str(row.get("period", ""))
        cell = out[period].setdefault(
            commodity, {"tonnes": 0.0, "value_usd": 0.0, "dest": defaultdict(float)}
        )
        tonnes = float(row.get("netWgt") or 0) / 1000.0
        cell["tonnes"] += tonnes
        cell["value_usd"] += float(row.get("primaryValue") or 0)
        partner = row.get("partnerISO") or str(row.get("partnerCode") or "other")
        cell["dest"][partner] += tonnes
    return out


def _destinations(dest: dict[str, float], total: float, top_n: int = 5) -> list[dict]:
    items = sorted(dest.items(), key=lambda kv: -kv[1])
    result = [
        {"partner": p, "tonnes": round(t), "share": round(t / total, 3) if total else 0.0}
        for p, t in items[:top_n]
    ]
    rest = sum(t for _, t in items[top_n:])
    if rest > 0:
        result.append({"partner": "other", "tonnes": round(rest),
                       "share": round(rest / total, 3) if total else 0.0})
    return result


def build_flows(reg: Registry, latest_period: str, trend_months: int = TREND_MONTHS, fetch=fetch_comtrade) -> dict:
    cc_hs = _country_commodity_hs(reg)
    periods = _period_range(latest_period, trend_months)
    flows: list[dict] = []
    trend: dict[str, list] = {}

    for iso2, reporter, headings in _planned_queries(reg):
        rows = fetch(reporter, headings, periods)
        agg = _aggregate(rows, reg.commodity_for_hs)

        commodities: set[str] = set()
        for p in periods:
            commodities |= set(agg.get(p, {}).keys())
        for commodity in commodities:
            trend[f"{iso2}_{commodity}"] = [
                round(agg.get(p, {}).get(commodity, {}).get("tonnes", 0.0)) for p in periods
            ]

        for commodity, a in agg.get(latest_period, {}).items():
            total = a["tonnes"] or 1.0
            flows.append({
                "country": iso2,
                "commodity": commodity,
                "hs_codes": sorted(cc_hs[iso2].get(commodity, [])),
                "period": _fmt_period(latest_period),
                "tonnes": round(a["tonnes"]),
                "value_usd": round(a["value_usd"]),
                "source": "UN Comtrade",
                "destinations": _destinations(a["dest"], total),
            })

    trend["trend_periods"] = [_fmt_period(p) for p in periods]
    flows.sort(key=lambda f: (f["country"], f["commodity"]))
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "UN Comtrade",
        "period": _fmt_period(latest_period),
        "lag_note": LAG_NOTE,
        "flows": flows,
        "trend": trend,
    }


def main() -> None:
    reg = Registry.load(ROOT / "registry.json")
    if not COMTRADE_KEY:
        log.warning("COMTRADE_KEY not set — leaving existing %s untouched. Set the key "
                    "(free tier) to enable live pulls; endpoint is already verified.", OUT.name)
        return
    period = (os.environ.get("FLOWS_PERIOD") or "").replace("-", "").strip() or _default_period()
    if len(period) != 6 or not period.isdigit():
        log.error("FLOWS_PERIOD must be YYYYMM (got %r).", period)
        return
    log.info("pulling Comtrade exports for period %s (+%d-month trend)", period, TREND_MONTHS)
    try:
        data = build_flows(reg, period, TREND_MONTHS)
    except Exception:
        log.exception("flows pull failed; leaving existing %s untouched", OUT.name)
        return
    if not data["flows"]:
        log.warning("no flow rows for %s — leaving existing %s untouched "
                    "(period may not be published yet).", period, OUT.name)
        return
    payload = json.dumps(data, indent=2)
    OUT.write_text(payload)
    log.info("wrote %s with %d flow rows (period %s)", OUT, len(data["flows"]), data["period"])
    if WEB_OUT.parent.exists():
        WEB_OUT.write_text(payload)
        log.info("synced %s", WEB_OUT)


if __name__ == "__main__":
    main()
