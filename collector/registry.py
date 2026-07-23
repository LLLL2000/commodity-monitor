"""Load + validate registry.json and expose geometry helpers.

This module is the ONLY place that reads the berth registry. Everything the
rest of the collector needs about commodities/terminals flows from here, which
is what keeps the "coverage = edit registry.json only" invariant enforceable.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Point, Polygon

log = logging.getLogger("registry")

VALID_PURITY = {"high", "medium", "low"}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _box_from_centroid(lat: float, lon: float, half_km: float = 1.0):
    """Fallback berth box if a terminal omits berth_polygon (usability nicety:
    a new terminal only needs a centroid to start producing a signal)."""
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat)))
    return [[lat - dlat, lon - dlon], [lat + dlat, lon - dlon],
            [lat + dlat, lon + dlon], [lat - dlat, lon + dlon]]


@dataclass
class Terminal:
    id: str
    name: str
    country: str
    primary_commodity: str
    commodities: list[str]
    hs_codes: list[str]
    purity: str
    expected_vessel_classes: list[str]
    centroid_lat: float
    centroid_lon: float
    anchorage_radius_km: float
    operator: str = ""
    port: str = ""
    notes: str = ""
    polygon_verified: bool = False
    polygon: Polygon = field(repr=False, default=None)

    def contains(self, lat: float, lon: float) -> bool:
        # Shapely uses (x=lon, y=lat).
        return self.polygon.contains(Point(lon, lat))

    def distance_km(self, lat: float, lon: float) -> float:
        return haversine_km(self.centroid_lat, self.centroid_lon, lat, lon)


class Registry:
    def __init__(self, data: dict):
        self.raw = data
        self.country_meta: dict[str, dict] = data.get("country_meta", {})
        self.taxonomy: dict[str, list[str]] = data.get("commodity_taxonomy", {})
        self.vessel_class_hint: dict[str, list[str]] = data.get("vessel_class_hint", {})
        self.all_commodities: set[str] = {c for group in self.taxonomy.values() for c in group}
        self.terminals: list[Terminal] = []
        self._build()
        self.hs_commodity: dict[str, str] = self._build_hs_commodity()

    @staticmethod
    def _norm_hs(code: str) -> str:
        """Comtrade-style HS code: no dots/spaces (e.g. '2836.91' -> '283691')."""
        return str(code).replace(".", "").strip()

    def _build_hs_commodity(self) -> dict[str, str]:
        """Normalized HS code -> commodity. Explicit `hs_commodity` in the
        registry wins; the rest is auto-derived from single-commodity terminals
        (an HS code seen only on copper-only berths is copper). Ambiguous codes
        that appear solely on multi-commodity terminals must be listed explicitly
        in registry.json's `hs_commodity` block."""
        mapping: dict[str, str] = {
            self._norm_hs(k): v for k, v in self.raw.get("hs_commodity", {}).items()
        }
        for t in self.terminals:                       # unambiguous witnesses
            if len(t.commodities) == 1:
                for code in t.hs_codes:
                    mapping.setdefault(self._norm_hs(code), t.commodities[0])
        return mapping

    def commodity_for_hs(self, code: str) -> str | None:
        """Resolve an HS code (any granularity) to a commodity via the registry.
        Tries exact match, then a prefix match (4-digit heading <-> 6-digit
        subheading) when it is unambiguous."""
        norm = self._norm_hs(code)
        if norm in self.hs_commodity:
            return self.hs_commodity[norm]
        hits = {v for k, v in self.hs_commodity.items() if k.startswith(norm) or norm.startswith(k)}
        return hits.pop() if len(hits) == 1 else None

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def _build(self) -> None:
        seen_ids: set[str] = set()
        for t in self.raw.get("terminals", []):
            tid = t.get("id")
            if not tid:
                raise ValueError(f"terminal missing id: {t!r}")
            if tid in seen_ids:
                raise ValueError(f"duplicate terminal id: {tid}")
            seen_ids.add(tid)

            purity = t.get("purity", "low")
            if purity not in VALID_PURITY:
                raise ValueError(f"{tid}: purity must be one of {VALID_PURITY}, got {purity!r}")

            commodities = t.get("commodities") or ([t["primary_commodity"]] if t.get("primary_commodity") else [])
            if not commodities:
                raise ValueError(f"{tid}: no commodities/primary_commodity")
            unknown = set(commodities) - self.all_commodities
            if unknown:
                log.warning("%s: commodities %s not in taxonomy (add them to commodity_taxonomy)", tid, unknown)

            lat, lon = t["centroid"]
            ring = t.get("berth_polygon") or _box_from_centroid(lat, lon)
            if len(ring) < 3:
                raise ValueError(f"{tid}: berth_polygon needs >=3 points")
            # ring is [[lat,lon],...]; Shapely wants (lon,lat)
            poly = Polygon([(pt[1], pt[0]) for pt in ring])
            if not poly.is_valid:
                raise ValueError(f"{tid}: berth_polygon is not a valid polygon")

            self.terminals.append(Terminal(
                id=tid, name=t.get("name", tid), country=t.get("country", ""),
                primary_commodity=t.get("primary_commodity", commodities[0]),
                commodities=commodities, hs_codes=t.get("hs_codes", []),
                purity=purity,
                expected_vessel_classes=t.get("expected_vessel_classes", []),
                centroid_lat=lat, centroid_lon=lon,
                anchorage_radius_km=float(t.get("anchorage_radius_km", 15)),
                operator=t.get("operator", ""), port=t.get("port", ""),
                notes=t.get("notes", ""),
                polygon_verified=bool(t.get("polygon_verified", False)),
                polygon=poly,
            ))
        if not self.terminals:
            raise ValueError("registry has no terminals")
        log.info("loaded %d terminals; %d with verified polygons",
                 len(self.terminals),
                 sum(t.polygon_verified for t in self.terminals))

    def bbox(self, margin_deg: float) -> tuple[float, float, float, float]:
        """(minLat, minLon, maxLat, maxLon) covering all terminals + margin."""
        lats, lons = [], []
        for t in self.terminals:
            for lon, lat in t.polygon.exterior.coords:
                lats.append(lat)
                lons.append(lon)
        return (min(lats) - margin_deg, min(lons) - margin_deg,
                max(lats) + margin_deg, max(lons) + margin_deg)

    def attribute_commodity(self, terminal: Terminal, vessel_class: str | None) -> str:
        """Commodity for a call. Berth tag is authoritative: single-commodity
        terminals return that commodity. For multi-commodity berths, fall back
        to vessel_class_hint (mapping coarse AIS class -> candidate commodities),
        else primary_commodity."""
        if len(terminal.commodities) == 1:
            return terminal.commodities[0]
        if vessel_class:
            # coarse AIS class -> the fine hint keys it could satisfy
            candidates = _fine_classes_for(vessel_class)
            for hint_class in candidates:
                for c in self.vessel_class_hint.get(hint_class, []):
                    if c in terminal.commodities:
                        return c
        return terminal.primary_commodity


# AIS only distinguishes coarse categories (Cargo vs Tanker). The registry's
# vessel_class_hint uses finer labels (bulk_carrier/container/general_cargo/
# gas_carrier). Bridge the two: one coarse class maps to several fine ones.
_COARSE_TO_FINE = {
    "cargo": ["bulk_carrier", "container", "general_cargo"],
    "tanker": ["tanker", "gas_carrier"],
    "gas_carrier": ["gas_carrier"],
}


def _fine_classes_for(vessel_class: str) -> list[str]:
    return _COARSE_TO_FINE.get(vessel_class, [vessel_class])
