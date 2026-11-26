"""Integration tests for newzcrawl.queue (download queue engine).

Runs a real DownloadQueue against the in-process mock NNTP server over a
TCP socket, using NZB files built from the mock's multipart yEnc posts.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from mock_nntp_server import MockNNTPServer  # noqa: E402
from newzcrawl import nzb  # noqa: E402
from newzcrawl.nntp_client import NNTPClient  # noqa: E402
from newzcrawl.queue import DownloadQueue  # noqa: E402


@pytest.fixture
def server():
    srv = MockNNTPServer(require_auth=True, username="alice",
                         password="s3cret")
    srv.movie = srv.add_file("movie.bin", os.urandom(300 * 1024),
                             group="bin.movies", part_size=64 * 1024)
    srv.start()
    yield srv
    srv.stop()


def _factory(server):
    def make():
        c = NNTPClient("127.0.0.1", server.port)
        c.connect()
        c.authenticate("alice", "s3cret")
        return c
    return make


def _nzb_from_mockfile(mf, name="movie.bin", part_size=64 * 1024):
    item = nzb.NZBItem(name=name, groups=["bin.movies"])
    total = len(mf.data)
    for i, mid in enumerate(mf.parts, 1):
        sz = min(part_size, total - (i - 1) * part_size)
        item.add_segment(nzb.NZBSegment(mid, bytes=sz, index=i))
    return nzb.NZB(name=name, items=[item])


def test_full_reassembly_round_trip(server, tmp_path):
    """A complete NZB reassembles to the exact original bytes."""
    q = DownloadQueue(_factory(server), tmp_path, max_workers=4)
    try:
        n = _nzb_from_mockfile(server.movie)
        results = q.download(n)
    finally:
        q.close()
    assert len(results) == 1
    res = results[0]
    assert res.ok and res.complete, res.error
    with open(res.output_path, "rb") as f:
        assert f.read() == server.movie.data


def test_missing_part_leaves_hole_and_reports(server, tmp_path):
    """Drop one segment from the NZB: the file is still written with the
    remaining parts in place, and the missing part number is reported."""
    n = _nzb_from_mockfile(server.movie)
    n.items[0].segments.pop(1)  # remove the 2nd part
    q = DownloadQueue(_factory(server), tmp_path, max_workers=4)
    try:
        res = q.download_item(n.items[0])
    finally:
        q.close()
    assert res.ok, res.error
    assert not res.complete
    # the removed part's byte range is the single uncovered hole
    assert res.holes == [(64 * 1024, 2 * 64 * 1024)]
    with open(res.output_path, "rb") as f:
        data = f.read()
    assert len(data) == len(server.movie.data)
    # the removed part's byte range must be zero-filled
    assert data[64 * 1024:64 * 1024 + 100] == bytes(100)
    # a surviving part's bytes are intact
    assert data[:100] == server.movie.data[:100]
    assert data[2 * 64 * 1024:2 * 64 * 1024 + 100] == \
        server.movie.data[2 * 64 * 1024:2 * 64 * 1024 + 100]


def test_corrupt_part_detected(server, tmp_path):
    """A part whose yEnc CRC fails is counted corrupt, not silently kept."""
    n = _nzb_from_mockfile(server.movie)
    # corrupt the stored body of the first part so its pcrc32 fails:
    # flip a base85 data character (not a directive line)
    mid = server.movie.parts[0]
    art = server.articles[mid]
    lines = art.body.split(b"\r\n")
    for i, ln in enumerate(lines):
        if ln and not ln.startswith(b"="):
            c = lines[i][5:6]
            lines[i] = lines[i][:5] + (b"0" if c != b"0" else b"1") + lines[i][6:]
            break
    art.body = b"\r\n".join(lines)
    q = DownloadQueue(_factory(server), tmp_path, max_workers=4)
    try:
        res = q.download_item(n.items[0])
    finally:
        q.close()
    assert res.ok
    assert not res.complete
    assert res.corrupt >= 1


def test_skip_existing(server, tmp_path):
    """With skip_existing, a complete file already on disk is not refetched."""
    q = DownloadQueue(_factory(server), tmp_path, max_workers=2,
                      skip_existing=True)
    try:
        n = _nzb_from_mockfile(server.movie)
        r1 = q.download_item(n.items[0])
        assert r1.ok and r1.complete
        # run again; the second pass should short-circuit
        r2 = q.download_item(n.items[0])
        assert r2.ok and r2.complete
        assert "already downloaded" in r2.error
    finally:
        q.close()


def test_unavailable_article_reported(server, tmp_path):
    """A segment whose message-id is not on the server is marked missing,
    not retried forever, and the rest of the item still downloads."""
    n = _nzb_from_mockfile(server.movie)
    # replace one segment with a bogus id
    n.items[0].segments[0] = nzb.NZBSegment(
        "ghost@nowhere", bytes=64 * 1024, index=1)
    q = DownloadQueue(_factory(server), tmp_path, max_workers=2,
                      max_retries=1)
    try:
        res = q.download_item(n.items[0])
    finally:
        q.close()
    assert res.ok
    assert not res.complete
    assert 1 in res.missing_parts
    assert res.missing >= 1
