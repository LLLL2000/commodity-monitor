# Running the collector on this PC (free, always-on)

The live collector runs as a hidden background process on this Windows machine —
**$0, no cloud, no card**. It auto-starts when you log in and auto-restarts if it
ever exits.

## How it's wired

- `scripts/run_collector.cmd` — runs `python -m collector.collector --live` in a
  restart loop, appending to `data/collector.live.log`. Reads `AISSTREAM_KEY` from `.env`.
- `scripts/run_collector_hidden.vbs` — launches the `.cmd` with no visible window.
- **Auto-start:** a copy of the `.vbs` lives in your Startup folder
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CommodityMonitorCollector.vbs`),
  so it starts at every logon. No admin needed.
- The collector serves live JSON + health at **http://localhost:8080**
  (`/healthz`, `/state.json`, `/flows.json`, `/prices.json`).

> **Caveat:** it runs while you're logged in and the PC is awake. Sleep/shutdown
> pauses it; it resumes on the next logon. For true 24/7 (survives logoff), an
> always-free cloud VM (Oracle Cloud Always Free) is the upgrade path — see
> `docs/deploy-fly.md` structure, but point at Oracle.

## Manage it

```powershell
# Is it up?
Invoke-WebRequest http://localhost:8080/healthz -UseBasicParsing | Select-Object -Expand Content
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Get-Process -Id $_.OwningProcess }

# Watch the log live
Get-Content "C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor\data\collector.live.log" -Wait -Tail 20

# Start it now (without waiting for next logon)
wscript.exe "C:\Users\consu\Documents\Commodity_Monitoring\commodity-monitor\scripts\run_collector_hidden.vbs"

# Stop it (kills the restart loop + the collector)
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'collector\.collector|run_collector' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Disable auto-start (remove the Startup entry)
Remove-Item "$([Environment]::GetFolderPath('Startup'))\CommodityMonitorCollector.vbs"
```

## See live data in the web console

The console defaults to reading `./data`. To point it at the running collector on
this PC, set near the top of `web/_template.html`:

```js
const DATA_BASE = "http://localhost:8080";
```

then `python web/build.py` and open the page. CORS is enabled, so it fetches the
live `state.json`/`flows.json`/`prices.json` from the collector. (Coverage is
sparse, so expect a quiet map — see `docs/status.md`.)
