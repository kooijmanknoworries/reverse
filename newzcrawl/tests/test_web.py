"""End-to-end tests for the web API against the mock NNTP server."""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from mock_nntp_server import MockNNTPServer  # noqa: E402
from newzcrawl.config import Config  # noqa: E402
from newzcrawl.nntp_client import NNTPClient  # noqa: E402
from newzcrawl.repair import Par2  # noqa: E402
from newzcrawl.search import LocalHeaderSearch  # noqa: E402
from newzcrawl.web import server as web  # noqa: E402


@pytest.fixture
def server():
    srv = MockNNTPServer(require_auth=True, username="alice",
                         password="s3cret")
    srv.movie = srv.add_file("demo.bin", os.urandom(200 * 1024),
                             group="bin.demo", part_size=64 * 1024)
    srv.add_text("<t1@x>", "news.demo", "The Matrix Discussion", "body")
    srv.add_text("<t2@x>", "news.demo", "Matrix Reloaded Talk", "body")
    srv.add_text("<t3@x>", "news.demo", "Unrelated News", "body")
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def app(tmp_path, server):
    cfg = Config(str(tmp_path / "settings.json"))
    cfg.set("host", "127.0.0.1")
    cfg.set("port", server.port)
    cfg.set("username", "alice")
    cfg.set("password", "s3cret")
    cfg.save()
    core = web.Core()
    core.config = cfg
    core.app_dir = str(tmp_path)
    core.downloads_dir = str(tmp_path / "downloads")
    os.makedirs(core.downloads_dir, exist_ok=True)

    def factory():
        c = NNTPClient("127.0.0.1", server.port, timeout=15)
        c.connect()
        c.authenticate("alice", "s3cret")
        return c

    core.client_factory = factory
    core.search = LocalHeaderSearch(factory, window=500)
    core.repair = Par2(str(tmp_path / "absent/par2cmdline"))

    srv = web.serve(core, "127.0.0.1", 0)  # ephemeral port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    time.sleep(0.2)
    yield base, core, cfg
    srv.shutdown()
    srv.server_close()


def _get(base, path, ok=True):
    req = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if ok:
            raise
        return json.loads(e.read())


def _post(base, path, payload, ct="application/json"):
    data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": ct}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def test_serves_index(app):
    base, _, _ = app
    with urllib.request.urlopen(base + "/", timeout=15) as r:
        html = r.read().decode()
    assert "<title>NewzCrawl</title>" in html
    assert "btnDownload" in html


def test_api_status(app):
    base, _, _ = app
    s = _get(base, "/api/status")
    assert s["app"] == "NewzCrawl"
    assert s["search"] == "local-header"
    assert s["host"] == "127.0.0.1"
    assert s["port"] == 0 or isinstance(s["port"], int)


def test_api_connect(app):
    base, _, _ = app
    r = _post(base, "/api/connect", "{}")
    assert r["ok"] is True
    assert "reading" in r["capabilities"]


def test_api_groups_filter(app):
    base, _, _ = app
    r = _get(base, "/api/groups?filter=bin")
    names = [g[0] for g in r["groups"]]
    assert "bin.demo" in names
    r2 = _get(base, "/api/groups")
    assert any("news.demo" == g[0] for g in r2["groups"])


def test_api_headers(app):
    base, _, _ = app
    r = _get(base, "/api/headers?group=news.demo&window=50")
    assert r["last"] >= 3
    subjects = [a["subject"] for a in r["articles"]]
    assert "The Matrix Discussion" in subjects
    for a in r["articles"]:
        assert a["num"] > 0
        assert a["bytes"] >= 0


def test_api_search(app):
    base, _, _ = app
    r = _post(base, "/api/search", {"query": "matrix groups:news.demo"})
    subs = [h["subject"] for h in r["hits"]]
    assert "The Matrix Discussion" in subs
    assert "Matrix Reloaded Talk" in subs
    assert "Unrelated News" not in subs


def test_api_download_reassembles(app, server):
    base, _, _ = app
    # build an NZB from the mock multipart file
    from newzcrawl import nzb
    item = nzb.NZBItem(name="demo.bin", groups=["bin.demo"])
    total = len(server.movie.data)
    for i, mid in enumerate(server.movie.parts, 1):
        item.add_segment(nzb.NZBSegment(mid,
                                        bytes=min(64 * 1024, total - (i - 1) * 64 * 1024),
                                        index=i))
    xml = nzb.create_nzb("demo.nzb", [item])
    r = _post(base, "/api/download", xml.decode(), ct="application/xml")
    it = r["items"][0]
    assert it["ok"] and it["complete"]
    with open(it["path"], "rb") as f:
        assert f.read() == server.movie.data


def test_api_download_rejects_bad_body(app):
    base, _, _ = app
    r = _post(base, "/api/download", "this is not xml", ct="application/xml")
    assert "error" in r


def test_api_settings(app):
    base, _, cfg = app
    r = _get(base, "/api/settings")
    keys = {s["key"] for s in r["schema"]}
    assert "download_auto_repair" in keys
    r = _post(base, "/api/settings/set", {"values": {"port": 1234}})
    assert r["applied"] == {"port": 1234}
    assert cfg.get("port") == 1234
    r = _post(base, "/api/settings/set", {"values": {"bogus": 1}})
    assert r["rejected"] == ["bogus"]


def test_api_repair_missing_binary(app):
    base, _, _ = app
    r = _post(base, "/api/repair/scan", "{}")
    assert r["ok"] is False
    assert "par2cmdline" in r["error"]


def test_api_unknown_endpoint(app):
    base, _, _ = app
    r = _get(base, "/api/nope", ok=False)
    assert "error" in r


def test_static_traversal_blocked(app):
    base, _, _ = app
    try:
        urllib.request.urlopen(base + "/static/../server.py", timeout=10)
        raised = False
    except (urllib.error.HTTPError, urllib.error.URLError):
        raised = True
    assert raised
