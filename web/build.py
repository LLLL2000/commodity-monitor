#!/usr/bin/env python3
"""Inject the current web/data + registry JSON into _template.html -> index.html.

The frontend fetches DATA_BASE/*.json live at runtime; this only bakes in a seed
copy so the page renders immediately when opened without a server. The seed is
read from web/data (what the static site serves) so it stays in sync with the
demo and is independent of the live collector, which owns the repo-root data/.
Re-run after changing the template or web/data.
"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"                 # web/data — the served copy
tpl = (ROOT / "_template.html").read_text()

def compact(p):
    return json.dumps(json.loads(pathlib.Path(p).read_text()), separators=(",", ":"))

subs = {
    "__SEED_STATE__": compact(DATA / "state.json"),
    "__SEED_FLOWS__": compact(DATA / "flows.json"),
    "__SEED_PRICES__": compact(DATA / "prices.json"),
    "__SEED_REGISTRY__": compact(REPO / "registry.json"),
}
for k, v in subs.items():
    assert k in tpl, f"placeholder {k} missing from template"
    tpl = tpl.replace(k, v)

(ROOT / "index.html").write_text(tpl)
print(f"wrote {ROOT/'index.html'} ({len(tpl):,} bytes)")
