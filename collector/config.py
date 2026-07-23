"""Central config for the collector. Everything overridable via env vars.

Detection thresholds live here (they are behaviour, not domain data). Domain
data — commodities, terminals, geometry — lives ONLY in registry.json.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal, dependency-free .env loader: KEY=VALUE lines, '#' comments,
    optional surrounding quotes. Real environment variables always win (so
    Fly.io / CI secrets are never overridden by a committed-by-mistake file)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv(Path(os.environ.get("DOTENV_PATH", ROOT / ".env")))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# --- Paths ---------------------------------------------------------------
REGISTRY_PATH = Path(os.environ.get("REGISTRY_PATH", ROOT / "registry.json"))
STATE_PATH = Path(os.environ.get("STATE_PATH", ROOT / "data" / "state.json"))
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "collector.db"))

# --- HTTP publisher (serves data/*.json + /healthz for the platform) ------
SERVE_HTTP = os.environ.get("SERVE_HTTP", "1") != "0"
HTTP_PORT = _i("HTTP_PORT", 8080)

# --- AIS source ----------------------------------------------------------
AISSTREAM_KEY = os.environ.get("AISSTREAM_KEY", "")
AISSTREAM_URL = os.environ.get("AISSTREAM_URL", "wss://stream.aisstream.io/v0/stream")
# Optional explicit bbox override: "minLat,minLon,maxLat,maxLon".
# If unset, the collector derives a tight box from the registry extents + margin.
BBOX_OVERRIDE = os.environ.get("BBOX_OVERRIDE", "")
BBOX_MARGIN_DEG = _f("BBOX_MARGIN_DEG", 0.75)

# --- Port-call detection -------------------------------------------------
SOG_STOPPED_KN = _f("SOG_STOPPED_KN", 1.0)          # "slow" threshold, knots
MIN_ARRIVAL_MIN = _f("MIN_ARRIVAL_MIN", 20.0)       # sustained slow-inside => arrival
DEPARTURE_GRACE_MIN = _f("DEPARTURE_GRACE_MIN", 10.0)  # leave polygon this long => depart
STATE_WRITE_SEC = _i("STATE_WRITE_SEC", 45)         # snapshot cadence
STALE_POSITION_MIN = _f("STALE_POSITION_MIN", 90.0) # drop vessels not heard from

# --- Baseline / anomaly --------------------------------------------------
BASELINE_WEEKS = _i("BASELINE_WEEKS", 12)           # trailing weeks for baseline
Z_ALERT = _f("Z_ALERT", 2.0)                        # |z| threshold for anomaly
MIN_BASELINE_WEEKS = _i("MIN_BASELINE_WEEKS", 4)    # need this many weeks before alerting
# Safety gate: only fire alerts at terminals whose berth polygon has been
# human-verified (polygon_verified:true in registry.json). Alerting on
# unverified placeholder geometry produces noise. Set to "0" to relax.
REQUIRE_VERIFIED_POLYGON = os.environ.get("REQUIRE_VERIFIED_POLYGON", "1") != "0"

# --- SMTP alerts (optional) ---------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = _i("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
ALERT_TO = [e.strip() for e in os.environ.get("ALERT_TO", "").split(",") if e.strip()]
ALERT_COOLDOWN_H = _f("ALERT_COOLDOWN_H", 24.0)     # per-terminal alert dedupe window
