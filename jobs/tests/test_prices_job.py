"""Network-free unit tests for jobs/prices_job.py (Pink Sheet parsing + merge)."""
from __future__ import annotations

import io

import openpyxl
import pytest

from jobs import prices_job


def _fake_pinksheet() -> bytes:
    """Build a minimal workbook shaped like the World Bank 'Monthly Prices' sheet:
    row 5 = names, row 6 = units, row 7+ = data with col A = 'YYYYMmm'."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monthly Prices"
    ws.append(["World Bank Commodity Price Data"])           # 1
    ws.append([])                                            # 2
    ws.append([])                                            # 3
    ws.append(["Updated on ..."])                            # 4
    ws.append([None, "Nickel", "Copper", "Aluminum"])        # 5 names
    ws.append([None, "($/mt)", "($/mt)", "($/mt)"])          # 6 units
    ws.append(["2025M10", 15000, 10739.91, 2500])            # 7 data
    ws.append(["2025M11", 15100, 10812.03, 2510])
    ws.append(["2025M12", 15200, 11785.25, 2520])
    ws.append(["2026M01", None, "..", None])                 # blank markers ignored
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_pinksheet_series_matches_column_by_name():
    series = prices_job._pinksheet_series(_fake_pinksheet(), "Copper")
    assert series == [("2025M10", 10739.91), ("2025M11", 10812.03), ("2025M12", 11785.25)]


def test_pinksheet_series_missing_column_raises():
    with pytest.raises(ValueError):
        prices_job._pinksheet_series(_fake_pinksheet(), "Lithium")


def test_period_to_iso():
    assert prices_job._period_to_iso("2025M12") == "2025-12"
    assert prices_job._period_to_iso("2025M1") == "2025-01"


def test_fetch_copper_builds_contract(monkeypatch):
    monkeypatch.setattr(prices_job, "_download", lambda url: _fake_pinksheet())
    obj = prices_job.fetch_copper()
    assert obj["price"] == 11785
    assert obj["unit"] == "USD/tonne"
    # +9.0% from 10812.03 -> 11785.25
    assert obj["change"] == 9.0
    assert obj["asof"] == "2025-12"
    assert obj["history"] == [10739.91, 10812.03, 11785.25]
    assert obj["history_dates"] == ["2025-10", "2025-11", "2025-12"]


def test_build_prices_preserves_lithium_when_copper_only(monkeypatch, tmp_path):
    # copper succeeds, lithium returns None -> existing lithium entry preserved
    monkeypatch.setattr(prices_job, "_download", lambda url: _fake_pinksheet())
    existing = tmp_path / "prices.json"
    existing.write_text(
        '{"updated":"x","prices":{"lithium":{"price":74500,"source":"SAMPLE"}}}'
    )
    monkeypatch.setattr(prices_job, "OUT", existing)

    class Term:
        commodities = ["copper", "lithium"]

    class Reg:
        terminals = [Term()]

    data, fetched = prices_job.build_prices(Reg())
    assert fetched == 1
    assert data["prices"]["copper"]["price"] == 11785
    assert data["prices"]["lithium"] == {"price": 74500, "source": "SAMPLE"}
