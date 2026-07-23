"""Tests for the collector's read-only HTTP publisher."""
from __future__ import annotations

import threading
import urllib.request
import urllib.error

import pytest

from collector import webserver


@pytest.fixture()
def server(tmp_path):
    (tmp_path / "state.json").write_text('{"vessel_count": 3}')
    srv = webserver.make_server(tmp_path, 0)  # port 0 -> OS picks a free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers, r.read()


def test_healthz(server):
    status, _headers, body = _get(f"{server}/healthz")
    assert status == 200 and b'"status":"ok"' in body


def test_serves_json_with_cors(server):
    status, headers, body = _get(f"{server}/state.json")
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert b'"vessel_count": 3' in body


def test_missing_json_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{server}/prices.json")
    assert e.value.code == 404


def test_path_traversal_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f"{server}/../registry.json")
    assert e.value.code in (400, 404)
