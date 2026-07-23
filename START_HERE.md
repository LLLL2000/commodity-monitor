# START HERE — resume the Commodity Monitor

Your one-stop page to pick this project back up. Last worked on: **15 Jul 2026**.

## What this is (10 seconds)
A near-real-time monitor for seaborne **copper & lithium** out of Latin America. It
pairs free **AIS vessel positions** with a hand-curated **berth registry** — a ship at a
single-purpose terminal ≈ that commodity loading — and adds customs tonnage + prices.
It's an **activity proxy, not verified tonnage**. Coverage grows by editing
`registry.json` only (no code changes).

## What's running right now
- **The live collector runs on THIS PC**, in the background, logging real AIS to a
  database. It **auto-starts when you log in** and **auto-restarts if it crashes** —
  nothing to do but keep the PC on and awake. (Sleep/logoff pauses it; it resumes at
  next logon. Data already logged is never lost.)

### Check it's alive (optional)
```powershell
# should print {"status":"ok"}
Invoke-WebRequest http://localhost:8080/healthz -UseBasicParsing | Select -Expand Content

# how much REAL data has accumulated
cd "C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor"
python -c "import sqlite3;c=sqlite3.connect('data/collector.db');print('port-call events:',c.execute('select count(*) from events').fetchone()[0]);print('vessels seen:',c.execute('select count(*) from vessel_static').fetchone()[0])"
```
`vessels seen` climbs steadily; `port-call events` only ticks up when a ship actually
berths at one of the 10 terminals (rare on this sparse coast — builds over days/weeks).

### See the console (the map)
Just open **`web\index.html`** in a browser (double-click). It shows the seeded **demo**
(map, 10 terminals, an elevated anomaly ring, real copper price) — no server needed.
To view **live** data instead, see `docs/run-on-this-pc.md`.

## Status: done vs. next
**Done:**
- Collector + detection + web console + alert logic (tested, 21 unit tests passing).
- **Prices job** — real copper live from the World Bank Pink Sheet (no key). Lithium has
  no free source (flagged sample kept).
- **Flows job** — full UN Comtrade job done & tested; endpoint verified. Add a free
  `COMTRADE_KEY` to go live.
- **Hosting** — collector runs free on this PC (auto-start + auto-restart).

**Good next tasks (pick any):**
1. **Verify berth polygons** → set `polygon_verified: true` in `registry.json`. This is
   what **arms email alerts** (currently held for unverified berths). Highest-value manual task.
2. **Add more berths / commodities / countries** — edit `registry.json` only. Ask Claude
   and name the ports; the collector's AIS box auto-expands to cover them.
3. **Move to free cloud 24/7** — Oracle Cloud Always Free VM (survives logoff). The
   collector is already container-ready; ask Claude for `docs/deploy-oracle.md`.
4. **Turn on alerts email** — set `SMTP_*` vars in `.env` (needs #1 done first to fire).
5. Add a free `COMTRADE_KEY` (+ `SMTP_*`) to `.env` to light up real flows + emails.

## Map of the docs
- `START_HERE.md` — this file.
- `docs/run-on-this-pc.md` — manage/stop/restart the local collector.
- `docs/status.md` — detailed phase-by-phase status.
- `docs/deploy-fly.md` — cloud deploy reference (Fly; adapt for Oracle).
- `CLAUDE.md` / `README.md` — architecture, the registry-only rule, conventions.
- `docs/project-spec.md` — full requirements (source of truth).

## Handy commands
```powershell
# Stop the collector
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'collector\.collector|run_collector' -and $_.ProcessId -ne $PID } | % { Stop-Process -Id $_.ProcessId -Force }
# Start it now (without waiting for logon)
wscript.exe "C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor\scripts\run_collector_hidden.vbs"
# Watch it live
Get-Content "C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor\data\collector.live.log" -Wait -Tail 20
# Run the tests
python -m pytest collector/tests jobs/tests -q
```
