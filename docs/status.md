# Seaborne Commodity Activity Monitor — Status & Next Steps
*Status as of 15 July 2026*

## What it is

A near-real-time monitor for seaborne **copper and lithium** activity out of Latin America. It pairs free **AIS vessel positions** with a hand-curated **berth registry**: because a ship that berths at a single-purpose terminal is almost certainly loading that terminal's commodity, port calls become a live activity signal. Crucially, it reports **activity, not verified tonnage** — that caveat is stated throughout the interface — and pairs the live signal with **customs data** (for tonnage) and **reference prices**. Coverage grows by adding rows to one registry file; no code changes are needed to add a terminal, commodity, or producing country.

The v1 registry covers 10 terminals across Chile and Peru (copper, plus lithium hooks).

## Where we are

Everything needed to run the system end-to-end is built. The collector and the web console are **done and tested**; the customs/price enrichment jobs are **scaffolded and waiting on API keys**; alerting logic is **done and waiting on mail credentials**.

| Phase | Piece | Status |
|---|---|---|
| 0 | Berth registry — 10 terminals (CL + PE) | ✅ Done |
| 1 | Collector: AIS → port-call detection → `state.json` | ✅ Done, 6 unit tests passing |
| 1 | Web console — Leaflet map, commodity filter, click-through panels, price rail, flows, methodology | ✅ Done, validated |
| 2 | Customs flows — UN Comtrade → `flows.json` | 🟢 Complete & tested; endpoint verified — needs `COMTRADE_KEY` (free tier) to go live |
| 3 | Prices — LME / World Bank / GFEX → `prices.json` | 🟢 Copper **live** (World Bank Pink Sheet, no key, tested); lithium sample (no free source), LME stocks pending |
| 4 | Anomaly email alerts (z-score) | ✅ Logic done & tested; needs SMTP credentials |
| 5 | Methodology tab + deploy | ✅ Methodology & legend in UI; **collector live on this PC** ($0, hidden background task, auto-start at logon + auto-restart, serves :8080) — see `docs/run-on-this-pc.md`. Cloud option (Oracle Always Free) still open |

A built-in **simulator** runs the whole pipeline with **no API key**, so you can see it working today (commands at the end). Sample customs/price data ships so the console renders fully in the meantime.

## What's baked in (three choices you can revisit)

These were reasonable calls I made where the spec left room; all are documented in the README and easy to change.

1. **Live state is in memory.** The SQLite event log is what survives restarts and rebuilds the 12-week baselines — so a redeploy doesn't lose history.
2. **Coordinates are approximate.** Each terminal uses the spec's rough centroid with a generated ~2 km berth box, all flagged `polygon_verified: false`. (See task B — this is the main thing to firm up.)
3. **The AIS bounding box is derived** from the registry's geographic extent plus a margin, and is env-overridable.

---

## What you need to do next

### A. Provide access — only you can do these

I can't create accounts, hold secrets, or stand up servers, so these five are yours:

1. **AISStream.io API key** — free signup at aisstream.io. *This is the single biggest unlock: it turns the map from sample data to live vessels.* Set it as `AISSTREAM_KEY` in the collector's environment. The key stays server-side; the frontend never touches AIS.
2. **An always-on host for the collector** — the live layer must run 24/7 with no sleep. A Fly.io config is included (region `scl`, persistent volume); from your own account it's a `fly deploy`. Any always-on VM works equally well.
3. **UN Comtrade API key** — free; needed for real customs tonnage. Set as `COMTRADE_KEY`. (I'll finish the query wiring once it exists — see C.)
4. **SMTP credentials** — host / port / user / password for the mailbox alerts should send from, as the `SMTP_*` vars. Without them, anomalies are logged but not emailed.
5. **A static host for the console** — the `web/` folder plus its `data/` JSON deploys to any static host (Netlify, Cloudflare Pages, S3, GitHub Pages). Point the collector and jobs to write their JSON where the site serves it (or set `DATA_BASE` in the page).

### B. Verify the berth registry — the one manual, high-value task

All 10 terminals currently carry **approximate coordinates** and `polygon_verified: false`. For each one: open it against a marine chart or the **World Port Index**, tighten the berth box to the actual quay, confirm the centroid, and set `polygon_verified: true`.

**Why it matters:** email alerts are deliberately **held for unverified berths**, so until this is done the map will *flag* elevated terminals (a dashed magenta ring) but won't *alert*. This can't be pulled reliably from free data — it needs a human with a chart. Budget roughly an hour for all ten.

### C. What I'll pick up on your say-so

Just tell me which and I'll build it:

- **Comtrade flows — done** ✅ endpoint verified (`comtradeapi.un.org/data/v1/get/C/M/HS`, key
  via `Ocp-Apim-Subscription-Key`), aggregation + trend + registry-driven HS→commodity attribution
  complete and unit-tested. Drop `COMTRADE_KEY` (free tier, A.3) in and the monthly cron replaces
  the sample `flows.json` — no code changes needed.
- **Prices — copper is now live** ✅ World Bank Pink Sheet is wired (public XLSX, no key), so
  `prices.json` carries real monthly copper. Still open: LME on-warrant stock levels (need a
  registered feed) and a licensed lithium price (no clean free real-time source — the sample
  lithium entry is preserved and flagged "indicative").
- **Walk through the deploy** — Fly.io for the collector, a static host for the console, and the included GitHub Actions crons for the monthly/weekday enrichment jobs.

---

## See it working today (no keys required)

```bash
pip install -r collector/requirements.txt
python -m pytest collector/tests -q                        # 6 passing
python -m collector.collector --simulate --once --seed-history
cd web && python -m http.server 8000                       # open http://localhost:8000
```

The console boots from a bundled snapshot, so opening `web/index.html` directly also shows a working map — Puerto Coloso will display an elevated spike (z ≈ 3.7) that exercises the anomaly maths. One honest caveat: I validated the console with headless checks (JS syntax, element wiring, and a scripted render pass) but couldn't render it in a real browser in my environment, so give it a quick look when you first serve it.

## The critical path, in one line

Get the **AISStream key + an always-on host** (live map) → **verify the ten berth polygons** (trustworthy alerts) → drop in the **Comtrade and SMTP keys and deploy**.
