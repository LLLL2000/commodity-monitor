"""Collector entrypoint.

    python -m collector.collector --live       # real AISStream feed (needs AISSTREAM_KEY)
    python -m collector.collector --simulate    # synthetic feed, no key needed
    python -m collector.collector --simulate --once   # run sim, write one snapshot, exit

Wires: source -> Detector -> periodic state.json snapshot + anomaly alerts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from datetime import datetime, timezone

import shutil

from . import config, webserver
from .alerts import Alerter
from .detector import Detector
from .ingest import run_live
from .registry import Registry
from .simulator import run_sim, seed_history
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("collector")
UTC = timezone.utc


def write_state(det: Detector, alerter: Alerter, reg: Registry) -> dict:
    state = det.build_state()
    config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(config.STATE_PATH)  # atomic publish
    names = {t.id: t.name for t in reg.terminals}
    for tid, m in state["terminals"].items():
        alerter.maybe_alert(tid, m, names.get(tid, tid))
    return state


async def snapshot_loop(det: Detector, alerter: Alerter, reg: Registry, stop: asyncio.Event) -> None:
    while not stop.is_set():
        det.prune_stale()
        st = write_state(det, alerter, reg)
        log.info("wrote %s (%d vessels, anomalies=%s)",
                 config.STATE_PATH, st["vessel_count"], st["anomalies"])
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.STATE_WRITE_SEC)
        except asyncio.TimeoutError:
            pass


def _seed_enrichment() -> None:
    """On a fresh volume, seed flows/prices from the image's baked seed dir so the
    HTTP publisher can serve all three JSONs (state.json is written live). Only
    fills gaps; never clobbers files a cron has already published. No-op locally."""
    seed_dir = config.ROOT / "seed"
    if not seed_dir.is_dir():
        return
    data_dir = config.STATE_PATH.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("flows.json", "prices.json"):
        dst, src = data_dir / name, seed_dir / name
        if src.is_file() and not dst.is_file():
            shutil.copyfile(src, dst)
            log.info("seeded %s from image", dst)


async def amain(args) -> None:
    reg = Registry.load(config.REGISTRY_PATH)
    store = Store(config.DB_PATH)
    det = Detector(reg, store)
    alerter = Alerter(store)
    stop = asyncio.Event()

    # Long-running modes: publish data/*.json + /healthz over HTTP.
    if config.SERVE_HTTP and not (args.simulate and args.once):
        _seed_enrichment()
        webserver.serve_in_thread(config.STATE_PATH.parent, config.HTTP_PORT)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    if args.simulate:
        if args.seed_history:
            seed_history(reg, det)
        if args.once:
            await run_sim(reg, det, stop)
            write_state(det, alerter, reg)
            log.info("one-shot simulation done; wrote %s", config.STATE_PATH)
            store.close()
            return
        source = run_sim(reg, det, stop)
    else:
        source = run_live(reg, det, stop)

    await asyncio.gather(source, snapshot_loop(det, alerter, reg, stop))
    store.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Seaborne commodity activity collector")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="use live AISStream feed")
    mode.add_argument("--simulate", action="store_true", help="use built-in simulator")
    p.add_argument("--once", action="store_true", help="(sim) run once, write one snapshot, exit")
    p.add_argument("--seed-history", dest="seed_history", action="store_true",
                   help="(sim) backfill event history so baselines are meaningful")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
