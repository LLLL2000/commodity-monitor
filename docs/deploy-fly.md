# Deploy the collector to Fly.io (always-on, 24/7)

The collector holds one persistent AIS WebSocket, writes `state.json`, logs port-call
events to SQLite (so baselines survive restarts), and serves `data/*.json` + `/healthz`
over HTTP. This runbook stands it up on Fly.io in region `scl` (Santiago).

Everything here runs from the **repo root** (`commodity-monitor/`). Steps that need your
account/billing are marked **(you)** — I can't run those.

## 0. One-time prerequisites **(you)**

1. Create a Fly.io account: https://fly.io/app/sign-up (needs a card; the collector fits
   the low-cost shared-cpu tier).
2. Install flyctl:
   - Windows (PowerShell): `iwr https://fly.io/install.ps1 -useb | iex`
   - then restart the shell so `fly` is on PATH.
3. Log in: `fly auth login`

## 1. Create the app + persistent volume **(you)**

```powershell
# from repo root
fly apps create commodity-monitor-collector      # or edit `app` in collector/fly.toml
fly volumes create cm_data --region scl --size 1  # 1 GB; matches [mounts] in fly.toml
```

## 2. Set the AIS key as a secret **(you)**

The key is server-side only — set it as a Fly secret, never bake it into the image.

```powershell
fly secrets set AISSTREAM_KEY=582f2d6d731d1dd9e7992daa1ab1238f60041478
```

(Optional now or later: `fly secrets set SMTP_HOST=... SMTP_USER=... SMTP_PASS=... ALERT_TO=...`
to turn on anomaly emails. Without them, anomalies are logged only.)

## 3. Deploy **(you)**

```powershell
fly deploy --config collector/fly.toml
```

The image bakes `registry.json`, the collector, and a `seed/` copy of the sample
flows/prices (so the HTTP publisher can serve all three JSONs on a fresh volume;
`state.json` is written live). `fly.toml` runs a single always-on machine and never
autostops it.

## 4. Verify it's live

```powershell
fly logs                                   # look for: "subscribed; streaming AIS"
curl https://commodity-monitor-collector.fly.dev/healthz    # {"status":"ok"}
curl https://commodity-monitor-collector.fly.dev/state.json # live snapshot JSON
```

> **Coverage note:** AISStream's free tier is sparse on the Chile/Peru Pacific coast, and
> the map only shows vessels within ~15 km of the 10 terminals — so `vessel_count` is
> often 0 in any given minute. That's expected. Running 24/7 is exactly how the
> sparse-but-real port calls accumulate into the SQLite log and (eventually) fire alerts.

## 5. Point the web console at it (optional)

In `web/_template.html` set:

```js
const DATA_BASE = "https://commodity-monitor-collector.fly.dev";
```

then `python web/build.py` and deploy `web/` to any static host. The publisher sends
`Access-Control-Allow-Origin: *`, so cross-origin polling works. `flows.json` /
`prices.json` served this way come from the last deploy's baked seed; to keep them fresh,
either redeploy after the cron commits, or host `web/` + `data/` together and keep
`DATA_BASE` pointed at the static copy for those two.

## Operating notes

- **Restarts are safe:** the SQLite event log on the `cm_data` volume rebuilds the 12-week
  baselines, so history isn't lost.
- **Config knobs** (Fly `[env]` or `fly secrets`): `HTTP_PORT`, `SERVE_HTTP=0` to disable
  the publisher, `BBOX_OVERRIDE`, and the detection thresholds in `collector/config.py`.
- **Arming alerts** still requires verifying berth polygons (`polygon_verified: true`) —
  independent of deploy.
