"""Tests for newzcrawl.config."""

import os
import sys

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


# ---------------------------------------------------------------------------
# data location: portable vs installed
# ---------------------------------------------------------------------------

def test_dev_run_is_portable(tmp_path, monkeypatch):
    """In development (not frozen) data always sits next to the program."""
    assert cfgmod._frozen() is False
    assert cfgmod.is_portable() is True
    assert cfgmod.data_dir() == cfgmod.app_root()
    assert cfgmod.default_settings_path() == os.path.join(
        cfgmod.data_dir(), "settings.json")


def test_frozen_in_program_files_is_installed(tmp_path, monkeypatch):
    """Per-machine install under Program Files -> data in %APPDATA%."""
    monkeypatch.setattr(cfgmod, "_frozen", lambda: True)
    monkeypatch.setattr(cfgmod, "app_root",
                        lambda: str(tmp_path / "Program Files" / "NewzCrawl"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert cfgmod.is_portable() is False
    assert cfgmod.data_dir() == os.path.join(str(tmp_path / "Roaming"),
                                             "NewzCrawl")


def test_frozen_in_user_programs_is_installed(tmp_path, monkeypatch):
    """Per-user install in LocalAppData\\Programs -> data in %APPDATA%."""
    monkeypatch.setattr(cfgmod, "_frozen", lambda: True)
    la = str(tmp_path / "Local")
    monkeypatch.setattr(cfgmod, "app_root",
                        lambda: os.path.join(la, "Programs", "NewzCrawl"))
    monkeypatch.setenv("LOCALAPPDATA", la)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert cfgmod.is_portable() is False
    assert cfgmod.data_dir() == os.path.join(str(tmp_path / "Roaming"),
                                             "NewzCrawl")


def test_frozen_in_plain_folder_is_portable(tmp_path, monkeypatch):
    """Portable zip in an ordinary folder (e.g. Desktop) -> data next to it."""
    monkeypatch.setattr(cfgmod, "_frozen", lambda: True)
    monkeypatch.setattr(cfgmod, "app_root",
                        lambda: str(tmp_path / "Desktop" / "NC"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert cfgmod.is_portable() is True
    assert cfgmod.data_dir() == str(tmp_path / "Desktop" / "NC")


def test_portable_flag_in_status():
    """api_status exposes data_dir + whether it is portable."""
    import newzcrawl.web.server as web
    core = web.Core()
    core.config = _FakeConfig()
    core.app_dir = "/opt/nc"
    core.data_dir = "/home/user/ncdata"
    st = web.api_status(core, [], b"")
    assert st["data_dir"] == "/home/user/ncdata"
    assert st["portable"] is False
    core.data_dir = "/opt/nc"
    st = web.api_status(core, [], b"")
    assert st["portable"] is True


class _FakeConfig:
    def get(self, k, d=None):
        return d
    def schema(self):
        return []
