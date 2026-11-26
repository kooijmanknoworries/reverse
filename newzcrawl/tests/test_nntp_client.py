"""Integration tests for newzcrawl.nntp_client against the mock NNTP server."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from mock_nntp_server import MockNNTPServer  # noqa: E402
from newzcrawl.nntp_client import NNTPClient  # noqa: E402


@pytest.fixture
def server():
    srv = MockNNTPServer(require_auth=True, username="alice", password="s3cret")
    blob = os.urandom(300 * 1024)
    mf = srv.add_file("movie.bin", blob, group="bin.movies")
    srv.add_text("<text-1@x>", "news.misc", "Hello world", "Just some text.")
    srv.start()
    srv.blob = blob
    srv.movie = mf
    yield srv
    srv.stop()


@pytest.fixture
def client(server):
    c = NNTPClient("127.0.0.1", server.port)
    c.connect()
    c.authenticate("alice", "s3cret")
    yield c
    c.close()


def test_auth_required_before_commands(server):
    c = NNTPClient("127.0.0.1", server.port)
    c.connect()
    from newzcrawl.nntp_client import NNTPError
    with pytest.raises(NNTPError) as e:
        c.list_groups()
    assert e.value.code == 502
    c.close()


def test_auth_failure(server):
    c = NNTPClient("127.0.0.1", server.port)
    c.connect()
    from newzcrawl.nntp_client import NNTPError
    with pytest.raises(NNTPError) as e:
        c.authenticate("alice", "wrong")
    assert e.value.code == 501
    c.close()


def test_capabilities(client, server):
    caps = client.capabilities()
    assert "reading" in caps and "posting" in caps and "authinfo" in caps


def test_list_groups(client, server):
    groups = client.list_groups()
    names = [g[0] for g in groups]
    assert "bin.movies" in names and "news.misc" in names


def test_list_active(client, server):
    active = client.list_active()
    names = [a[0] for a in active]
    assert "bin.movies" in names and "news.misc" in names
    # list_groups pattern form works on the mock
    movies = client.list_groups("bin.*")
    assert [g[0] for g in movies] == ["bin.movies"]


def test_group(client, server):
    n = client.group("bin.movies")
    assert n == len(server.movie.parts)
    # unknown group => 404
    from newzcrawl.nntp_client import NNTPError
    with pytest.raises(NNTPError) as e:
        client.group("no.such")
    assert e.value.code == 404


def test_stat_and_xover(client, server):
    mid = server.movie.parts[0]
    assert client.stat(mid) > 0
    # XOVER by a message-id list (real servers key the result by article number)
    xov = client.xover(mid, server.movie.parts[-1])
    assert len(xov) == 2
    # each row's fields carry the original message-id (first overview field)
    mids_in_rows = {f[0] for f in xov.values()}
    assert mid in mids_in_rows
    # XOVER by numeric range (needs a current group) -> article-number keys
    client.group("bin.movies")
    xov2 = client.xover("1", str(len(server.movie.parts)))
    assert len(xov2) == len(server.movie.parts)
    assert set(xov2) == {str(i) for i in range(1, len(server.movie.parts) + 1)}


def test_xover_dict_named_fields(client, server):
    client.group("bin.movies")
    n = len(server.movie.parts)
    rows = client.xover_dict("1", str(n))
    assert len(rows) == n
    # field names come from the server's LIST OVERVIEW.FMT
    fmt = client.list_overview_fmt()
    row = rows["1"]
    assert set(row) == set(fmt)
    assert row["Message-ID"] == server.movie.parts[0]
    assert "Subject" in fmt and row["Subject"] != ""
    assert "Bytes" in fmt and row["Bytes"].isdigit()


def test_fetch_all_parts_reassembles(client, server):
    reasm = bytearray()
    for i, mid in enumerate(server.movie.parts):
        art = client.fetch(mid, group="bin.movies")
        assert art.has_yenc, f"part {i} not flagged yEnc"
        assert art.crc_ok, f"part {i} CRC failed"
        assert art.decoded is not None
        assert len(art.decoded) == min(65536, len(server.blob) - i * 65536)
        reasm.extend(art.decoded)
    assert bytes(reasm) == server.blob


def test_fetch_text_article(client, server):
    client.group("news.misc")
    art = client.fetch("<text-1@x>", group="news.misc")
    assert not art.has_yenc
    assert art.body == b"Just some text."
    assert art.subject == "Hello world"


def test_fetch_body_only(client, server):
    art = client.fetch(server.movie.parts[0], group="bin.movies", body_only=True)
    assert art.body.startswith(b"=y")
    # no headers, but the =y magic is detected so it still decodes
    assert art.crc_ok is True
    assert len(art.decoded) == 65536
    raw, crc_ok = client.decode_body(art.body)
    assert crc_ok and len(raw) == 65536


def test_post(client, server):
    client.post("Subject: hi\r\nFrom: me@x\r\n\r\nbody text")
    assert len(server.received_posts) == 1
    assert "Subject: hi" in server.received_posts[0]


def test_select_and_slave(client, server):
    assert client.select("bin.movies") == "bin.movies"
    assert client.current_group == "bin.movies"
    assert client.slave("news.misc") == "news.misc"


def test_missing_article_raises(client):
    from newzcrawl.nntp_client import NNTPError
    with pytest.raises(NNTPError) as e:
        client.fetch("<nope@x>", group="bin.movies")
    assert e.value.code == 420
