#!/usr/bin/env python3
"""
Generate the v1 seed `registry.json` from the hand-off brief's §9 table.

This is a *reproducible* helper, NOT part of the runtime. registry.json is the
hand-editable source of truth; regenerate only if you want to reset the seed.

IMPORTANT: berth polygons produced here are FIRST-PASS ~2 km boxes centred on
approximate centroids. They are placeholders. Before trusting high-purity
anomaly alerts, redraw each berth_polygon tightly around the actual loading
berths using the World Port Index / a marine chart / satellite imagery.
"""
import json
import math
from pathlib import Path

# Half-extent of the generated placeholder berth box, in km (=> ~2 km box).
BOX_HALF_KM = 1.0


def km_box(lat: float, lon: float, half_km: float = BOX_HALF_KM):
    """Return a closed rectangular ring [[lat,lon],...] ~ (2*half_km) on a side."""
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat)))
    return [
        [round(lat - dlat, 5), round(lon - dlon, 5)],
        [round(lat + dlat, 5), round(lon - dlon, 5)],
        [round(lat + dlat, 5), round(lon + dlon, 5)],
        [round(lat - dlat, 5), round(lon + dlon, 5)],
    ]


# The canonical taxonomy + vessel-class fallback from §2 of the brief.
commodity_taxonomy = {
    "energy": ["crude", "products", "lng", "lpg", "ammonia", "coal"],
    "metals": ["copper", "iron_ore", "bauxite", "alumina", "lithium", "zinc"],
    "ags": ["grain", "soybeans", "corn", "sugar", "palm_oil"],
}

vessel_class_hint = {
    "tanker": ["crude", "products", "palm_oil"],
    "gas_carrier": ["lng", "lpg", "ammonia"],
    "bulk_carrier": ["iron_ore", "coal", "bauxite", "alumina", "grain",
                     "soybeans", "corn", "copper", "zinc"],
    "container": ["lithium", "products"],
}

# v1 seed (§9). expected_vessel_classes & anchorage_radius_km chosen per terminal.
# purity/commodities/hs_codes come straight from the brief's table.
seed = [
    dict(id="coloso", name="Puerto Coloso", port="Antofagasta", country="CL",
         operator="Escondida (BHP)", commodities=["copper"], hs_codes=["2603"],
         purity="high", centroid=[-23.75, -70.47], radius=15,
         classes=["bulk_carrier", "general_cargo"],
         notes="Single-mine slurry-pipeline terminal; a bulk carrier here ≈ Escondida copper concentrate."),
    dict(id="patache", name="Punta Patache / Patillos", port="Iquique", country="CL",
         operator="Collahuasi", commodities=["copper"], hs_codes=["2603"],
         purity="high", centroid=[-20.80, -70.16], radius=15,
         classes=["bulk_carrier", "general_cargo"],
         notes="Dedicated Collahuasi concentrate terminal; high-confidence copper signal."),
    dict(id="mejillones", name="Mejillones (Angamos / TGN)", port="Mejillones", country="CL",
         operator="Codelco / multi", commodities=["copper", "lithium"],
         hs_codes=["2603", "7403", "2836.91"], purity="medium",
         centroid=[-23.10, -70.45], radius=15,
         classes=["bulk_carrier", "general_cargo", "container"],
         notes="Multi-operator complex; copper cathode/concentrate + some lithium. Tag at berth level to lift purity."),
    dict(id="antofagasta", name="Antofagasta (ATI)", port="Antofagasta", country="CL",
         operator="Codelco / SQM", commodities=["copper", "lithium"],
         hs_codes=["7403", "2603", "2836.91", "2825.20"], purity="medium",
         centroid=[-23.64, -70.40], radius=12,
         classes=["general_cargo", "container", "bulk_carrier"],
         notes="City port handling Codelco copper + SQM lithium chemicals (largely containerised). Mixed signal."),
    dict(id="ventanas", name="Ventanas (Quintero)", port="Quintero", country="CL",
         operator="Codelco refinery", commodities=["copper"], hs_codes=["7403"],
         purity="medium", centroid=[-32.75, -71.48], radius=12,
         classes=["general_cargo", "container", "bulk_carrier"],
         notes="Codelco refinery outlet; refined cathodes (7403), some break-bulk/container."),
    dict(id="sanantonio", name="San Antonio", port="San Antonio", country="CL",
         operator="Andina / El Teniente (multi)", commodities=["copper"],
         hs_codes=["7403", "2603"], purity="low", centroid=[-33.59, -71.61], radius=12,
         classes=["container", "general_cargo", "bulk_carrier"],
         notes="Large multi-commodity gateway. NOISY for copper unless tagged at berth level."),
    dict(id="matarani", name="Matarani (TISUR)", port="Matarani", country="PE",
         operator="Cerro Verde, Las Bambas", commodities=["copper"], hs_codes=["2603"],
         purity="high", centroid=[-17.00, -72.10], radius=15,
         classes=["bulk_carrier", "general_cargo"],
         notes="Concentrate outlet for Cerro Verde & Las Bambas; strong copper signal."),
    dict(id="puntalobitos", name="Punta Lobitos (Huarmey)", port="Huarmey", country="PE",
         operator="Antamina", commodities=["copper"], hs_codes=["2603"],
         purity="high", centroid=[-10.08, -78.16], radius=15,
         classes=["bulk_carrier", "general_cargo"],
         notes="Antamina slurry-pipeline terminal; dedicated copper (and zinc) concentrate."),
    dict(id="ilo", name="Ilo", port="Ilo", country="PE",
         operator="Southern Copper", commodities=["copper"], hs_codes=["7403", "2603"],
         purity="medium", centroid=[-17.64, -71.34], radius=12,
         classes=["bulk_carrier", "general_cargo", "container"],
         notes="Southern Copper smelter/refinery outlet plus concentrate; mixed cathode/concentrate."),
    dict(id="callao", name="Callao", port="Callao", country="PE",
         operator="DP World / APM (multi)", commodities=["copper"], hs_codes=["7403", "2603"],
         purity="low", centroid=[-12.05, -77.15], radius=12,
         classes=["container", "general_cargo", "bulk_carrier"],
         notes="Peru's main multi-commodity port. NOISY for copper unless tagged at berth level."),
]

terminals = []
for t in seed:
    lat, lon = t["centroid"]
    terminals.append({
        "id": t["id"],
        "name": t["name"],
        "port": t["port"],
        "country": t["country"],
        "operator": t["operator"],
        "primary_commodity": t["commodities"][0],
        "commodities": t["commodities"],
        "hs_codes": t["hs_codes"],
        "purity": t["purity"],
        "expected_vessel_classes": t["classes"],
        "centroid": [lat, lon],
        "berth_polygon": km_box(lat, lon),
        "anchorage_radius_km": t["radius"],
        "notes": t["notes"],
        "polygon_verified": False,   # flip to true once redrawn against a marine chart
        "source": "public",
    })

# Country metadata for enrichment jobs (Comtrade reporterCode). Adding a new
# producing country = add its ISO2 -> reporter here + terminals above. No code change.
country_meta = {
    "CL": {"name": "Chile", "comtrade_reporter": "152"},
    "PE": {"name": "Peru", "comtrade_reporter": "604"},
    "AR": {"name": "Argentina", "comtrade_reporter": "032"},
}

registry = {
    "_readme": (
        "Berth registry = the heart of the system. Add coverage by adding terminals "
        "(and, if needed, taxonomy strings) here ONLY. Never hardcode commodities/terminals "
        "in code. berth_polygon are FIRST-PASS ~2 km boxes and MUST be verified/redrawn "
        "(set polygon_verified:true) before trusting high-purity alerts."
    ),
    "version": 1,
    "country_meta": country_meta,
    "commodity_taxonomy": commodity_taxonomy,
    "vessel_class_hint": vessel_class_hint,
    "terminals": terminals,
}

out = Path(__file__).resolve().parent.parent / "registry.json"
out.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {out} with {len(terminals)} terminals")
