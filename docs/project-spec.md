# Seaborne Commodity Activity Monitor — Project Specification (Hand-off Brief)

> This is a self-contained build brief for an AI coding assistant. Build it **greenfield** (from scratch). No prior context is assumed.

---

## 0. One-paragraph summary

Build a web tool that tracks **seaborne commodity export/import activity** in near-real-time by combining free **AIS vessel data** with a hand-curated **berth registry**. The key idea: AIS provides vessel positions and coarse vessel class for free, globally; the *berth a vessel calls at* supplies the commodity. So a bulk carrier docking at a single-purpose copper-concentrate terminal ≈ a copper export. The tool must be **commodity-agnostic**: coverage grows by adding rows to the berth registry, never by changing code. It ships as **v1 = Latin American copper + lithium**, architected to extend to energy, agricultural, and other bulk commodities. It reports an **activity proxy, not verified cargo tonnage** — this framing must be explicit in the UI.

---

## 1. Goals & non-goals

**Goals**
- Live map of vessels near tagged export/import terminals, plus per-terminal activity metrics (arrivals, departures, dwell time, waiting queue, activity index vs. baseline).
- A **berth registry** as a first-class, editable data file — the core reusable asset.
- Dynamic filtering/colouring by commodity.
- A lagged **structural-flow** layer from customs data (export volumes by HS code) for validation/context.
- A **price/inventory** rail for context.
- **Anomaly alerts** (email) when activity at a high-confidence terminal deviates from its baseline.

**Non-goals**
- Verified cargo tonnage or ship-level cargo identification (that requires paid data — Kpler/Vortexa/MarineTraffic). Do **not** claim it.
- Predictive/ML price forecasting (out of scope for v1).

---

## 2. Core data model — the berth registry

A single editable file (`registry.json`) is the heart of the system. Adding coverage = adding entries here. **Do not hardcode commodities or terminals anywhere else in the codebase.**

```json
{
  "commodity_taxonomy": {
    "energy":  ["crude", "products", "lng", "lpg", "ammonia", "coal"],
    "metals":  ["copper", "iron_ore", "bauxite", "alumina", "lithium", "zinc"],
    "ags":     ["grain", "soybeans", "corn", "sugar", "palm_oil"]
  },
  "vessel_class_hint": {
    "tanker":       ["crude", "products", "palm_oil"],
    "gas_carrier":  ["lng", "lpg", "ammonia"],
    "bulk_carrier": ["iron_ore", "coal", "bauxite", "alumina", "grain", "soybeans", "corn", "copper", "zinc"],
    "container":    ["lithium", "products"]
  },
  "terminals": [
    {
      "id": "coloso",
      "name": "Puerto Coloso",
      "port": "Antofagasta",
      "country": "CL",
      "operator": "Escondida (BHP)",
      "primary_commodity": "copper",
      "commodities": ["copper"],
      "hs_codes": ["2603"],
      "purity": "high",
      "expected_vessel_classes": ["bulk_carrier", "general_cargo"],
      "centroid": [-23.75, -70.47],
      "berth_polygon": [[-23.76,-70.49],[-23.74,-70.49],[-23.74,-70.46],[-23.76,-70.46]],
      "anchorage_radius_km": 15,
      "notes": "Single-mine slurry-pipeline terminal; a bulk carrier here ≈ Escondida copper.",
      "source": "public"
    }
  ]
}
```

Field rules:
- `berth_polygon` is a tight box around the loading berths (~1–3 km), **not** the whole harbour — this keeps port-call detection precise.
- `purity` ∈ {`high`, `medium`, `low`}: confidence that a vessel call = the tagged commodity. `high` = single-source/single-commodity terminals; `low` = general multi-commodity ports (treat as noisy, or tag individual berths).
- `commodities` is a list (a terminal may handle several). The **berth tag is authoritative**; `vessel_class_hint` is only a fallback for untagged areas.
- Adding a new commodity or region must require **only** new `terminals` entries (and possibly new taxonomy strings), never code changes.

---

## 3. Data sources (all free)

### 3.1 Live vessel data — AISStream.io (WebSocket)
- Endpoint: `wss://stream.aisstream.io/v0/stream`. Requires a **free API key** (register via GitHub on aisstream.io). **The key must stay server-side** — never ship it to the browser.
- Subscribe by sending JSON on connect:
  ```json
  {
    "APIKey": "<KEY>",
    "BoundingBoxes": [[[-35.0, -78.0], [-8.0, -70.0]]],
    "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
  }
  ```
  (`BoundingBoxes` = list of boxes; each box = `[[lat,lon],[lat,lon]]` for two opposite corners. Use one tight box per region to stay well under limits — a global subscription is ~300 msg/s and needs a bigger collector.)
- Message envelope: `{ "MessageType": "...", "MetaData": {...}, "Message": { "<MessageType>": {...} } }`.
  - `PositionReport` → `Latitude`, `Longitude`, `Sog` (speed over ground, knots), `Cog`, `TrueHeading`, `NavigationalStatus`, `UserID` (= MMSI).
  - `ShipStaticData` → `Type` (AIS ship-type code), `Dimension`, `Destination`, `MaximumStaticDraught`, `ImoNumber`. `MetaData` carries `MMSI`, `ShipName`, `time_utc`.
- AIS ship-type codes (coarse): 70–79 = cargo, 80–89 = tanker; gas carriers appear as tanker subtypes; 30 = fishing, etc. Map `Type` → `vessel_class` and cache per MMSI (static data arrives less often than positions).

### 3.2 Structural flows — customs (lagged 1–3 months)
- **UN Comtrade API** (free; obtain a subscription key at comtradeapi.un.org — check current docs for the exact path). Query monthly exports by `reporterCode` (Chile 152, Peru 604, Argentina 032), `flowCode=X` (exports), `cmdCode` (HS code), optional `partnerCode` (destination). Returns tonnage + value.
- Optional faster national sources: **Cochilco** (Chilean Copper Commission — copper production/exports/prices), Chile **Aduanas / Banco Central**; Peru **SUNAT / BCRP / MINEM**.

### 3.3 Prices & inventories
- Copper: **LME** copper (daily official/settlement — note real-time is paid), **World Bank "Pink Sheet"** (monthly CSV/Excel from the World Bank Commodity Markets page), **IMF Primary Commodity Prices** (monthly), plus **LME warehouse stocks** (tightness signal).
- Lithium (weak free coverage — flag this): **GFEX** (Guangzhou) lithium-carbonate futures; Trading Economics indicative. Reference prices (Fastmarkets, Benchmark Mineral Intelligence) are paid.

### 3.4 Optional — weather
- **Open-Meteo Marine API** (`https://marine-api.open-meteo.com/v1/marine`, free, no key) for wave height/wind at terminal centroids → disruption flags.

---

## 4. Architecture

```
AISStream WS ──► collector (always-on service) ──► state.json      (live vessels + per-terminal activity)
Comtrade/Cochilco ─► flows_job (daily/weekly cron) ─► flows.json
LME/WorldBank/GFEX ─► prices_job (daily cron)      ─► prices.json
                                    │
              static frontend (Leaflet) ◄── polls the three JSON files every 30–60 s
```

- **Collector**: holds the persistent AIS WebSocket, runs port-call detection against the registry, maintains rolling per-terminal state, writes `state.json`. Host on an always-on free/cheap tier (Fly.io / Render / Railway). Working state can live in memory or SQLite/Redis; it *publishes* JSON snapshots.
- **Enrichment jobs**: `flows_job` and `prices_job` run on scheduled CI (e.g., GitHub Actions cron) and commit/publish `flows.json` and `prices.json`.
- **Frontend**: static site (host on Vercel/Netlify/GitHub Pages). It **never** connects to AIS directly — it polls the published JSON files. No secrets client-side.
- **Alerts**: an SMTP module in the collector emails when an anomaly fires (config via env vars / secrets).

---

## 5. Port-call detection & metrics

**Per (vessel, terminal) state machine**, evaluated on each position update:
1. Point-in-polygon test of vessel position against `berth_polygon` (use a geometry lib, e.g. Shapely).
2. `OUTSIDE → INSIDE` with `Sog < ~1.0` sustained for ≥ N minutes (e.g. 20) ⇒ **ARRIVAL**.
3. `INSIDE → OUTSIDE` ⇒ **DEPARTURE**; record `dwell = departure − arrival`.
4. **Queue**: vessels within `anchorage_radius_km` of the centroid, slow/anchored (`NavigationalStatus` at anchor or `Sog < 1`), not currently at a berth.

**Vessel classification**: from `ShipStaticData.Type` + `Dimension`, mapped to `vessel_class`; cache per MMSI. Attribute a call's commodity from the terminal's tag (authoritative); use `vessel_class_hint` only where a berth handles multiple commodities.

**Per-terminal metrics** (rolling): `at_berth` (now), `arrivals_7d`, `arrivals_30d`, `departures_7d`, `departures_30d`, `median_dwell_h`, `queue`.

**Activity index**: normalise current activity against the terminal's own trailing baseline, e.g. `activity_index = departures_7d / median(weekly departures over trailing 12 weeks)`, plus a z-score. **Anomaly** = `|z| > 2` at a `purity: high` terminal ⇒ fire alert (spikes/drops often correspond to strikes, maintenance, weather).

**Regional aggregates**: group terminal metrics by `primary_commodity` and `country`; compare the departures proxy to customs tonnage; show destination split from Comtrade `partnerCode`.

---

## 6. JSON contracts

`state.json` (written by collector, ~30–60 s):
```json
{
  "updated": "2026-07-13T12:00:00Z",
  "vessels": [
    {"mmsi": 123456789, "lat": -23.75, "lon": -70.47, "class": "bulk_carrier", "sog": 0.1, "near": "coloso"}
  ],
  "terminals": {
    "coloso": {"commodity": "copper", "at_berth": 1, "arrivals_7d": 4, "departures_7d": 3,
               "median_dwell_h": 28, "queue": 2, "activity_index": 1.15, "z": 0.4, "anomaly": false}
  }
}
```
`flows.json` (monthly): per country+commodity export tonnage/value and destination split, with `period` and `source`.
`prices.json` (daily): per commodity `price`, `unit`, `change`, `asof`, plus copper `lme_stocks`.

---

## 7. Frontend spec

- **Leaflet** map, CartoDB "light_all" basemap.
- **Terminal markers**: coloured by `activity_index` (or by commodity when the commodity filter is off); anomalies visually flagged. Click → side panel with metrics, a dwell/departures **sparkline**, operator, commodity, and the latest customs export figure for that country/commodity.
- **Live vessel dots**: rendered from `state.json` on a poll; coloured by `class`. Use canvas rendering for performance.
- **Commodity filter control**: multi-select driven by the registry taxonomy; filters both terminals and vessels.
- **Price rail** (sidebar) from `prices.json`; **flows chart** (destination split, volume trend) from `flows.json`.
- **Legend** + a **Methodology tab** stating the caveats in §8.
- Keep everything data-driven from the three JSON files + `registry.json`.

---

## 8. Caveats to surface in the UI (methodology tab)

- **Activity is a proxy, not verified tonnage.** Concentrate/cathode and dedicated-tanker/gas flows have a strong live signal; containerised commodities (e.g. lithium chemicals) do not — those rely on customs data.
- **Customs data lags 1–3 months**; national sources lead it for the home market.
- **AIS coverage is uneven** (good near coasts/receivers, gaps offshore; transponders can be off).
- **Berth purity matters**: trust high-purity single-source terminals; treat general multi-commodity ports as noisy unless tagged at berth level.
- **Lithium prices** have no clean free real-time source; the reference indices are paid.

---

## 9. v1 seed data — LatAm copper + lithium

Seed `registry.json` with these. **Coordinates are approximate — the builder must verify against the World Port Index or a marine gazetteer** and draw tight berth polygons.

| id | name | country | operator / mine | commodity | HS | purity | ≈ lat, lon |
|---|---|---|---|---|---|---|---|
| coloso | Puerto Coloso | CL | Escondida (BHP) | copper | 2603 | high | −23.75, −70.47 |
| patache | Punta Patache/Patillos | CL | Collahuasi | copper | 2603 | high | −20.80, −70.16 |
| mejillones | Mejillones (Angamos/TGN) | CL | Codelco / multi | copper, lithium | 2603, 7403, 2836.91 | medium | −23.10, −70.45 |
| antofagasta | Antofagasta (ATI) | CL | Codelco / SQM | copper, lithium | 7403, 2603, 2836.91, 2825.20 | medium | −23.64, −70.40 |
| ventanas | Ventanas (Quintero) | CL | Codelco refinery | copper | 7403 | medium | −32.75, −71.48 |
| sanantonio | San Antonio | CL | Andina/El Teniente | copper | 7403, 2603 | low | −33.59, −71.61 |
| matarani | Matarani (TISUR) | PE | Cerro Verde, Las Bambas | copper | 2603 | high | −17.00, −72.10 |
| puntalobitos | Punta Lobitos (Huarmey) | PE | Antamina | copper | 2603 | high | −10.08, −78.16 |
| ilo | Ilo | PE | Southern Copper | copper | 7403, 2603 | medium | −17.64, −71.34 |
| callao | Callao | PE | DP World / APM (multi) | copper | 7403, 2603 | low | −12.05, −77.15 |

HS codes reference: copper ores/concentrates **2603**; copper anodes/unrefined **7402**; refined copper unwrought (cathodes) **7403** (cathodes 7403.11); lithium carbonate **2836.91**; lithium oxide/hydroxide **2825.20**.

---

## 10. Suggested tech stack

- **Collector & jobs**: Python (`websockets`, `shapely` for point-in-polygon, `requests`). SQLite or Redis for working state.
- **Frontend**: static — Leaflet + vanilla JS (or a light framework); canvas vessel rendering.
- **Hosting**: collector on Fly.io/Render/Railway (always-on); frontend + JSON on Vercel/Netlify/GitHub Pages; enrichment on GitHub Actions cron.
- **Secrets** (env vars / CI secrets): `AISSTREAM_KEY`, `COMTRADE_KEY`, SMTP creds for alerts.

---

## 11. Phased build plan

- **Phase 0 — Registry (½ day):** implement `registry.json` schema + the v1 seed terminals with tight berth polygons.
- **Phase 1 — Live layer (1–2 days):** collector connects to AISStream over the LatAm bbox, runs port-call detection, writes `state.json`; frontend renders live vessels + terminal activity colouring + commodity filter.
- **Phase 2 — Flows (1 day):** Comtrade job → `flows.json`; add structural-flow layer + destination chart.
- **Phase 3 — Prices/inventory (1 day):** LME/World Bank/GFEX + LME stocks → `prices.json`; price rail.
- **Phase 4 — Alerts (½ day):** baseline/z-score anomaly detection at high-purity terminals → email.
- **Phase 5 — Polish:** methodology tab, README, deploy.

**Design invariant to preserve throughout:** commodity coverage expands by editing `registry.json` only. If adding a new commodity or country ever requires touching code, the abstraction is wrong — fix the abstraction, not the data.
