"""Tests for newzcrawl.search (local header search over the mock server)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from mock_nntp_server import MockNNTPServer  # noqa: E402
from newzcrawl.nntp_client import NNTPClient  # noqa: E402
from newzcrawl.search import LocalHeaderSearch, parse_query  # noqa: E402


def test_parse_query_basic():
    p = parse_query('"The Matrix" from:anon groups:alt.binaries,x.y')
    # a quoted phrase is a single substring term
    assert p["terms"] == ["the matrix"]
    assert p["from"] == "anon"
    assert p["groups"] == ["alt.binaries", "x.y"]


def test_parse_query_no_modifiers():
    p = parse_query("hello world")
    assert p["terms"] == ["hello", "world"]
    assert p["from"] == ""
    assert p["groups"] == []


@pytest.fixture
def server():
    srv = MockNNTPServer(require_auth=True, username="alice",
                         password="s3cret")
    srv.add_text("<a1@x>", "news.test", "The Matrix Discussion", "body")
    srv.add_text("<a2@x>", "news.test", "Matrix Reloaded Talk", "body")
    srv.add_text("<a3@x>", "news.test", "Unrelated News", "body")
    srv.start()
    yield srv
    srv.stop()


def _factory(server):
    def make():
        c = NNTPClient("127.0.0.1", server.port, timeout=15)
        c.connect()
        c.authenticate("alice", "s3cret")
        return c
    return make


def test_search_finds_terms(server):
    s = LocalHeaderSearch(_factory(server), window=100)
    hits = s.search("matrix groups:news.test")
    subs = [h.subject for h in hits]
    assert "The Matrix Discussion" in subs
    assert "Matrix Reloaded Talk" in subs
    assert "Unrelated News" not in subs
    # newest first by date
    assert all(h.group == "news.test" for h in hits)
    assert all(h.message_id for h in hits)


def test_search_requires_groups(server):
    s = LocalHeaderSearch(_factory(server), window=100)
    # no group restriction and no groups: term -> nothing to scan
    assert s.search("matrix") == []


def test_search_all_terms_must_match(server):
    s = LocalHeaderSearch(_factory(server), window=100)
    hits = s.search("matrix reloaded groups:news.test")
    assert [h.subject for h in hits] == ["Matrix Reloaded Talk"]


def test_search_from_filter(server):
    s = LocalHeaderSearch(_factory(server), window=100)
    # all mock articles share the same poster, so a matching from: keeps all
    hits = s.search("matrix from:poster@newzcrawl.test groups:news.test")
    assert len(hits) == 2
    # a non-matching from: filters everything out
    assert s.search("matrix from:nobody@x groups:news.test") == []
