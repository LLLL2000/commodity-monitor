# CLAUDE.md

Guidance for working on this repo with Claude Code. Read this first, then `README.md`
for depth and `docs/project-spec.md` for the full requirements (the source of truth).

## What this is

A near-real-time monitor for seaborne **copper and lithium** activity out of Latin America.
It pairs free **AIS vessel positions** with a hand-curated **berth registry**: a ship that
berths at a single-purpose terminal is almost certainly loading that terminal's commodity,
so port calls become a live activity signal. It reports an **activity proxy, NOT verified
tonnage** — keep that framing visible in any UI or output — and pairs the live signal with
customs data (tonnage) and reference prices.

## The one rule that governs the design

**Commodity/region coverage grows by editing `registry.json` ONLY.** Never hardcode a
terminal, commodity, country, or HS code anywhere in the Python or the frontend. If adding
coverage ever seems to require a code change, the abstraction is wrong — fix the abstraction,
not the data.

- `collector/registry.py` is the **only** module that reads `registry.json`. Everything else
  (detector, jobs, frontend) gets its domain knowledge from there.
- `registry.json` also carries `commodity_taxonomy`, `country_meta` (ISO2 → Comtrade reporter
  code), `hs_commodity` (HS code → commodity, for customs attribution), and per-terminal
  geometry + `polygon_verified` flags.
- `scripts/gen_registry.py` is a **one-off reproducible generator** for the v1 seed, not a
  runtime dependency. Prefer editing `registry.json` directly for new terminals.

## Architecture / data flow

```
AIS (AISStream.io WS)  ─┐
                        ├─► collector/  ── writes ─►  data/state.json   (near-real-time, ~45s)
berth registry.json  ──┘                                   │
                                                           ▼
UN Comtrade  ── jobs/flows_job.py ── writes ─► data/flows.json   (monthly cron)
LME/WorldBank/GFEX ── jobs/prices_job.py ─────► data/prices.json (weekday cron)
                                                           │
web/index.html (static, Leaflet) ── polls ────────────────┘  data/*.json every 20s
```

The three JSON contracts are defined in **spec §6** (`docs/project-spec.md`). Don't change a
contract shape without updating both the producer (collector/jobs) and the consumer (`web/`).

## Layout

```
registry.json            # THE HEART — edit to add coverage
collector/               # always-on service
  registry.py            # sole reader of registry.json; Shapely geometry; commodity attribution
  ais.py                 # AIS ship-type code -> coarse vessel class
  detector.py            # port-call state machine, rolling metrics, activity index + z-score -> state.json
  store.py               # SQLite event log (survives restarts, rebuilds baselines) + alert dedupe
  ingest.py              # live AISStream.io WebSocket client (server-side key)
  simulator.py           # synthetic AIS feed + history seeding — runs with NO key
  collector.py           # entrypoint: source -> detector -> snapshot + alerts
  alerts.py              # SMTP anomaly email (logs-only if SMTP unset), per-terminal cooldown
  config.py              # env-driven thresholds + paths (behaviour only; no domain data)
  tests/test_detector.py # 6 unit tests for the detection engine
  Dockerfile, fly.toml
jobs/                    # cron enrichment (flows_job.py, prices_job.py) — see "Pending"
web/                     # static console; index.html is generated from _template.html by build.py
data/                    # generated/sample JSON (state, flows, prices)
docs/                    # project-spec.md (source of truth) + status.md
.github/workflows/       # crons for the enrichment jobs
```

## Setup & commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # collector + jobs + pytest

# tests
python -m pytest collector/tests -q        # 6 passing

# run the whole pipeline with NO API key (writes data/state.json)
python -m collector.collector --simulate --once --seed-history
#   --seed-history backfills ~14 weeks so activity_index/z are meaningful and nudges
#   Puerto Coloso into a z ~ 3.7 spike. Drop --once for the streaming demo (~45s snapshots).

# run live (needs a key; see .env.example)
AISSTREAM_KEY=... python -m collector.collector --live

# frontend: rebuild the self-contained page after editing the template, then serve
cd web && python build.py && python -m http.server 8000   # http://localhost:8000
```

**Frontend build model:** edit `web/_template.html`, then run `web/build.py` — it injects the
current `data/*.json` + `registry.json` into a self-contained `web/index.html` (so the page
also renders offline from a baked-in seed). Don't hand-edit `web/index.html`; it's generated.
The page polls `web/data/*.json` at runtime; point the collector/jobs there, or set `DATA_BASE`
at the top of the template.

Secrets: copy `.env.example` → `.env` and fill in. Never commit `.env`. The AIS key is
**server-side only** — the frontend never touches AIS.

## Conventions & gotchas

- **Registry-only coverage** (restated because it's the whole point). New country also needs a
  `country_meta` entry; new commodity may need a `commodity_taxonomy` string.
- Alerts are **deliberately gated on `polygon_verified: true`** (see `detector.py` +
  `REQUIRE_VERIFIED_POLYGON` in `config.py`). All v1 polygons are approximate placeholders
  (`polygon_verified: false`), so alerts are held until a berth is chart-verified. The frontend
  still *flags* elevated terminals (|z| ≥ 2) with a dashed ring; a solid ring means armed.
- Geometry order: `registry.json` polygons are `[lat, lon]`; Shapely gets `(lon, lat)`.
- Baselines exclude the current incomplete week; windows are relative to real `now`.
- Tune detection via env (`SOG_STOPPED_KN`, `MIN_ARRIVAL_MIN`, `Z_ALERT`, …) — see `config.py`.

## Status: done vs. pending

**Done & tested:** registry (10 terminals, CL+PE) · collector (detection, metrics, z-score,
state.json) · simulator · web console · alert logic · methodology UI.

**Pending (good next tasks):**
1. **Verify berth polygons** against marine charts / World Port Index and set
   `polygon_verified: true` (arms alerts). Manual; can't be scraped reliably.
2. **`jobs/flows_job.py`** — **DONE & tested.** Endpoint verified
   (`comtradeapi.un.org/data/v1/get/C/M/HS`, key header `Ocp-Apim-Subscription-Key`). Pulls the
   trend window in one multi-period call per country, emits the full contract (`flows[]` +
   `trend` + `hs_codes` + `lag_note`), defaults the period to *current month − 2*, and attributes
   each HS code to a commodity via the registry (`hs_commodity` + `reg.commodity_for_hs`) so
   lithium subheadings on dual-commodity copper berths are no longer misreported as copper. Set
   `COMTRADE_KEY` (free tier) to replace the sample `flows.json`.
3. **`jobs/prices_job.py`** — `fetch_copper()` is **LIVE**: it parses the World Bank Pink Sheet
   (public XLSX, no key, `openpyxl`) and writes real monthly copper into `prices.json` (unit-tested
   in `jobs/tests/`, network-free). Remaining: LME on-warrant stocks (`lme_stocks`, needs a
   registered feed) and a licensed lithium source — `fetch_lithium()` deliberately returns `None`
   so the flagged sample lithium entry is preserved rather than faked.
4. **Deploy** — collector to an always-on host (Fly.io config included, region `scl`),
   `web/` + JSON to a static host, jobs on the included GitHub Actions crons; wire real SMTP.
   The collector now ships a built-in read-only HTTP publisher (`collector/webserver.py`,
   `SERVE_HTTP`/`HTTP_PORT`) serving `data/*.json` + `/healthz`; `fly.toml` uses it as the
   `[http_service]` health check, and the frontend can poll it by setting `DATA_BASE` to the
   Fly URL. See `docs/deploy-fly.md` for the step-by-step runbook.

When you finish a pending item, update this section and the `README.md` build-status table.
```
