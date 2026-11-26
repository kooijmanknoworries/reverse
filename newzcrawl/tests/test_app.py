"""Tests for newzcrawl.app: stdout guard, CLI parsing, .nzb detection."""

import io
import os
import sys

import pytest

from newzcrawl import app as appmod


# ---------------------------------------------------------------------------
# _ensure_stdout (frozen windowed build: sys.stdout is None)
# ---------------------------------------------------------------------------

def test_ensure_stdout_is_noop_when_streams_exist(monkeypatch):
    """A real console (dev mode) leaves the streams untouched."""
    old_out, old_err = sys.stdout, sys.stderr
    monkeypatch.setattr(sys, "stdout", old_out)
    monkeypatch.setattr(sys, "stderr", old_err)
    appmod._ensure_stdout()
    assert sys.stdout is old_out
    assert sys.stderr is old_err


def test_ensure_stdout_points_none_streams_at_log(tmp_path, monkeypatch):
    """In a frozen windowed build (stdout=None) output must go to a file,
    and print() must not raise."""
    import newzcrawl.app as a
    a.data_dir = lambda: str(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        a._ensure_stdout()
        assert sys.stdout is not None
        assert sys.stderr is sys.stdout
        print("hello log")  # must not raise AttributeError
        path = tmp_path / "newzcrawl.log"
        assert path.exists()
        assert "hello log" in path.read_text(encoding="utf-8")
    finally:
        # restore real streams (they are None in this test process now)
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


def test_ensure_stdout_falls_back_to_memory(tmp_path, monkeypatch):
    """If the log file cannot be opened, discard output rather than crash."""
    import newzcrawl.app as a
    monkeypatch.setattr(a, "data_dir", lambda: str(tmp_path / "no" / "dir"))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        a._ensure_stdout()
        assert sys.stdout is not None
        print("must not raise")  # goes to the StringIO, no exception
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__


# ---------------------------------------------------------------------------
# CLI parsing (main / run argument handling)
# ---------------------------------------------------------------------------

def test_main_parses_flags(monkeypatch, capsys):
    """main() should parse --headless/--port/--settings and call run()."""
    seen = {}

    def fake_run(mode=None, port=None, settings_path=None):
        seen.update(mode=mode, port=port, settings_path=settings_path)
        return 0

    monkeypatch.setattr(appmod, "run", fake_run)
    rc = appmod.main(["--headless", "--port", "9999",
                      "--settings", "/tmp/s.json"])
    assert rc == 0
    assert seen == {"mode": "headless", "port": 9999,
                    "settings_path": "/tmp/s.json"}


def test_main_browser_mode(monkeypatch):
    seen = {}

    def fake_run(mode=None, port=None, settings_path=None):
        seen.update(mode=mode)
        return 0

    monkeypatch.setattr(appmod, "run", fake_run)
    appmod.main(["--browser"])
    assert seen["mode"] == "browser"


def test_main_no_mode_is_default(monkeypatch):
    seen = {}

    def fake_run(mode=None, port=None, settings_path=None):
        seen.update(mode=mode)
        return 0

    monkeypatch.setattr(appmod, "run", fake_run)
    appmod.main([])
    assert seen["mode"] is None  # run() picks desktop/browser by platform


# ---------------------------------------------------------------------------
# _pending_nzb
# ---------------------------------------------------------------------------

def test_pending_nzb_returns_path_when_given(tmp_path, monkeypatch):
    nzb = tmp_path / "movie.nzb"
    nzb.write_text("<nzb/>", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(nzb)])
    assert appmod._pending_nzb() == str(nzb)


def test_pending_nzb_ignores_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path / "missing.nzb")])
    assert appmod._pending_nzb() is None


def test_pending_nzb_none_when_no_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prog"])
    assert appmod._pending_nzb() is None
