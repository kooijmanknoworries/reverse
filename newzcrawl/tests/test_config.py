"""Tests for newzcrawl.config."""

from newzcrawl import config as cfgmod
from newzcrawl.config import Config


def test_defaults_loaded(tmp_path):
    c = Config(str(tmp_path / "s.json"))
    assert c.get("port") == 119
    assert c.get("download_crc_checking_yenc") is True
    assert c.get("threads_article_decoder") == 2
    assert c.get("download_auto_repair") is False


def test_set_and_get_with_coercion(tmp_path):
    c = Config(str(tmp_path / "s.json"))
    assert c.set("port", "8080") is True
    assert c.get("port") == 8080
    assert c.set("download_auto_repair", "true") is True
    assert c.get("download_auto_repair") is True
    assert c.set("threads_article_decoder", "8") is True
    assert c.get("threads_article_decoder") == 8


def test_reject_unknown_key(tmp_path):
    c = Config(str(tmp_path / "s.json"))
    assert c.set("not_a_real_key", 1) is False
    assert c.get("not_a_real_key") is None


def test_persistence_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    c = Config(p)
    c.set("host", "news.example")
    c.set("port", 563)
    c.save()
    c2 = Config(p)
    assert c2.get("host") == "news.example"
    assert c2.get("port") == 563


def test_unknown_keys_preserved(tmp_path):
    p = str(tmp_path / "s.json")
    c = Config(p)
    c.data["future_key"] = 42
    c.save()
    c2 = Config(p)
    assert c2.get("future_key") == 42


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    p = str(tmp_path / "s.json")
    with open(p, "w") as f:
        f.write("{ not json ]")
    c = Config(p)
    assert c.get("port") == 119


def test_schema_shape(tmp_path):
    c = Config(str(tmp_path / "s.json"))
    schema = c.schema()
    keys = {s["key"] for s in schema}
    assert {"host", "port", "password", "download_auto_repair"} <= keys
    for s in schema:
        assert s["type"] in ("bool", "int", "str")
        assert isinstance(s["value"], (bool, int, str))
