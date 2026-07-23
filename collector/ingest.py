"""Live AIS ingestion from AISStream.io over WebSocket.

Holds the persistent connection, subscribes with the API key (SERVER-SIDE ONLY),
parses the message envelope, and routes PositionReport / ShipStaticData into the
detector. Reconnects with capped exponential backoff.

Envelope shape (per AISStream docs):
    { "MessageType": "PositionReport",
      "MetaData": {"MMSI":..., "ShipName":..., "time_utc":...},
      "Message": {"PositionReport": {...}} }
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import websockets

from . import config
from .detector import Detector
from .registry import Registry

log = logging.getLogger("ingest")
UTC = timezone.utc


def _bbox_corners(reg: Registry) -> list:
    if config.BBOX_OVERRIDE:
        a, b, c, d = (float(x) for x in config.BBOX_OVERRIDE.split(","))
        min_lat, min_lon, max_lat, max_lon = a, b, c, d
    else:
        min_lat, min_lon, max_lat, max_lon = reg.bbox(config.BBOX_MARGIN_DEG)
    # AISStream box = [[lat,lon],[lat,lon]] two opposite corners.
    return [[[min_lat, min_lon], [max_lat, max_lon]]]


def _parse_time(meta: dict) -> datetime:
    raw = meta.get("time_utc") or meta.get("timestamp")
    if raw:
        try:
            # AISStream sends Go-style stamps like "2026-07-22 19:37:18.123456789 +0000 UTC".
            # Drop the trailing " +0000 UTC" and clamp fractional seconds to microseconds.
            cleaned = raw.replace("Z", "+00:00").split(" +")[0].strip()
            if "." in cleaned:
                head, frac = cleaned.split(".", 1)
                cleaned = f"{head}.{frac[:6]}"
            dt = datetime.fromisoformat(cleaned)
            # The offset was stripped above, so the parse is naive: treat it as UTC.
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(UTC)


async def run_live(reg: Registry, det: Detector, stop: asyncio.Event) -> None:
    if not config.AISSTREAM_KEY:
        raise RuntimeError("AISSTREAM_KEY not set; use --simulate or provide a key.")
    sub = {
        "APIKey": config.AISSTREAM_KEY,
        "BoundingBoxes": _bbox_corners(reg),
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    backoff = 1.0
    while not stop.is_set():
        try:
            log.info("connecting to %s; bbox=%s", config.AISSTREAM_URL, sub["BoundingBoxes"])
            async with websockets.connect(config.AISSTREAM_URL, ping_interval=20,
                                          max_queue=None) as ws:
                await ws.send(json.dumps(sub))
                backoff = 1.0
                log.info("subscribed; streaming AIS")
                async for raw in ws:
                    if stop.is_set():
                        break
                    _dispatch(det, raw)
        except Exception as e:
            if stop.is_set():
                break
            log.warning("AIS connection error: %s; reconnecting in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _dispatch(det: Detector, raw: str | bytes) -> None:
    try:
        env = json.loads(raw)
    except Exception:
        return
    mtype = env.get("MessageType")
    meta = env.get("MetaData", {}) or {}
    body = (env.get("Message", {}) or {}).get(mtype, {}) or {}
    mmsi = meta.get("MMSI") or body.get("UserID")
    if mmsi is None:
        return
    mmsi = int(mmsi)

    if mtype == "ShipStaticData":
        dim = body.get("Dimension", {}) or {}
        length = None
        if dim:
            a, b = dim.get("A"), dim.get("B")
            if a is not None and b is not None:
                length = float(a) + float(b)
        det.on_static(mmsi, meta.get("ShipName"), body.get("Type"), length)
    elif mtype == "PositionReport":
        lat = body.get("Latitude")
        lon = body.get("Longitude")
        if lat is None or lon is None:
            return
        det.on_position(
            mmsi, float(lat), float(lon),
            sog=body.get("Sog"), cog=body.get("Cog"),
            nav=body.get("NavigationalStatus"), ts=_parse_time(meta),
        )
