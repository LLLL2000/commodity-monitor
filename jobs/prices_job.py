#!/usr/bin/env python3
"""Phase 3 — prices & inventories -> prices.json.

STATUS: copper is LIVE. `fetch_copper()` pulls the World Bank "Pink Sheet"
(monthly commodity prices, public XLSX, NO API key) and builds the copper
sub-object of the contract. `fetch_lithium()` stays a stub that returns None,
by design: there is no clean free real-time lithium reference (the benchmark
indices — Fastmarkets, BMI — are paid), so we preserve the flagged sample
lithium entry rather than fake a live number.

  copper price   : World Bank "Pink Sheet" — Monthly Prices sheet, "Copper"
                   column, USD/mt (monthly average). Env-overridable URL.
  copper stocks  : LME on-warrant warehouse stocks are a tightness signal but
                   have no reliable free/no-key JSON feed, so lme_stocks is
                   omitted here (the frontend renders fine without it).
  lithium price  : no free real-time reference — see fetch_lithium().

Which commodities to price is derived from registry.json so it stays in sync
with coverage. Per-commodity, a fetcher that returns None leaves that
commodity's existing prices.json entry untouched (never overwritten with fake).

Run: python -m jobs.prices_job
Schedule via .github/workflows/prices.yml (daily cron).
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collector.registry import Registry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s prices_job %(levelname)s %(message)s")
log = logging.getLogger("prices_job")
OUT = ROOT / "data" / "prices.json"
# If the static site serves its own copy, keep it in sync too (best-effort).
WEB_OUT = ROOT / "web" / "data" / "prices.json"

# World Bank Commodity Markets "Pink Sheet" — monthly historical XLSX.
# The download path carries a rotating hash, so keep it env-overridable; the
# parser finds the "Copper" column by NAME, so a newer vintage still works.
# Landing page (to refresh the default): https://www.worldbank.org/en/research/commodity-markets
PINKSHEET_URL = os.environ.get(
    "PINKSHEET_URL",
    "https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/"
    "related/CMO-Historical-Data-Monthly.xlsx",
)
HISTORY_MONTHS = int(os.environ.get("PRICE_HISTORY_MONTHS", "12"))
_UA = {"User-Agent": "commodity-monitor/1.0 (+https://github.com/)"}


# --- helpers --------------------------------------------------------------
def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _period_to_iso(period: str) -> str:
    """Pink Sheet period 'YYYYMmm' (e.g. '2025M12') -> 'YYYY-MM'."""
    p = str(period).strip()
    if "M" in p:
        y, m = p.split("M", 1)
        return f"{y}-{int(m):02d}"
    return p


def _pinksheet_series(raw: bytes, column_name: str) -> list[tuple[str, float]]:
    """Return [(period, value), ...] for one commodity column of the Pink Sheet.

    Layout (Monthly Prices sheet): row 5 = names, row 6 = units, row 7+ = data
    with column A = period like '1960M01'. Column is matched by NAME so a newer
    file (different column order/vintage) keeps working.
    """
    import openpyxl  # local import so the module imports without openpyxl present

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb["Monthly Prices"]
    rows = list(ws.iter_rows(values_only=True))
    names = rows[4]
    idx = next(
        (j for j, n in enumerate(names) if n and str(n).strip().lower() == column_name.lower()),
        None,
    )
    if idx is None:
        raise ValueError(f"'{column_name}' column not found in Pink Sheet Monthly Prices")
    series: list[tuple[str, float]] = []
    for row in rows[6:]:
        period, cell = row[0], row[idx]
        if period is None or cell is None:
            continue
        try:
            series.append((str(period), float(cell)))
        except (TypeError, ValueError):
            continue  # blank markers ('..', '…') etc.
    return series


# --- per-commodity fetchers -----------------------------------------------
def fetch_copper() -> dict | None:
    """World Bank Pink Sheet monthly copper price (USD/mt). Returns the copper
    sub-object of the contract, or None on any failure (caller preserves the
    existing entry rather than emitting fake data)."""
    try:
        series = _pinksheet_series(_download(PINKSHEET_URL), "Copper")
    except Exception:
        log.exception("copper price fetch failed (World Bank Pink Sheet)")
        return None
    if len(series) < 2:
        log.warning("copper series too short (%d points); skipping", len(series))
        return None
    tail = series[-HISTORY_MONTHS:]
    last, prev = series[-1][1], series[-2][1]
    change = round((last - prev) / prev * 100, 1) if prev else 0.0
    return {
        "price": round(last),
        "unit": "USD/tonne",
        "change": change,
        "asof": _period_to_iso(series[-1][0]),
        "source": "World Bank Pink Sheet (monthly average, USD/mt)",
        "history": [round(v, 2) for _, v in tail],
        "history_dates": [_period_to_iso(p) for p, _ in tail],
    }


def fetch_lithium() -> dict | None:
    """No clean free real-time lithium reference exists (GFEX has no open no-key
    feed; Fastmarkets/BMI are paid). Return None so the flagged sample entry in
    prices.json is preserved rather than overwritten with a fabricated figure.
    Wire GFEX / Trading Economics here if a licensed source becomes available;
    always attach coverage_warning to the result."""
    log.info("lithium: no free real-time source wired; preserving existing entry")
    return None


FETCHERS = {"copper": fetch_copper, "lithium": fetch_lithium}


def _load_existing() -> dict:
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {"prices": {}}


def build_prices(reg: Registry) -> tuple[dict, int]:
    """Return (contract_dict, fetched_count). Preserves existing per-commodity
    entries for any commodity whose fetcher returns None."""
    existing = _load_existing()
    prices: dict[str, dict] = dict(existing.get("prices", {}))
    fetched = 0
    priced = {c for t in reg.terminals for c in t.commodities}
    for commodity in sorted(priced):
        fn = FETCHERS.get(commodity)
        if fn is None:
            log.info("no price fetcher for '%s' yet (add one in FETCHERS)", commodity)
            continue
        obj = fn()
        if obj:
            prices[commodity] = obj
            fetched += 1
            log.info("updated '%s': %s %s (asof %s)", commodity, obj["price"], obj["unit"], obj["asof"])
        elif commodity in prices:
            log.info("preserving existing '%s' entry (no live update)", commodity)
    return {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "prices": prices}, fetched


def main() -> None:
    reg = Registry.load(ROOT / "registry.json")
    data, fetched = build_prices(reg)
    if not fetched:
        log.warning("no live price fetch succeeded — leaving existing %s untouched.", OUT.name)
        return
    payload = json.dumps(data, indent=2)
    OUT.write_text(payload)
    log.info("wrote %s (%d commodities, %d live-updated)", OUT, len(data["prices"]), fetched)
    if WEB_OUT.parent.exists():
        WEB_OUT.write_text(payload)
        log.info("synced %s", WEB_OUT)


if __name__ == "__main__":
    main()
