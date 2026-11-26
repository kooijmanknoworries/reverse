"""Tests for newzcrawl.repair.

The real ``par2cmdline`` Windows binary isn't present on Linux, so a small
fake binary (a python script) is generated in a temp dir and drives the
same exit-code / stdout contract the real tool uses. This exercises the
real Par2 wrapper, classification logic, and the RepairWatcher loop.
"""

import os
import sys
import time

import pytest

from newzcrawl import repair as rep
from newzcrawl.repair import Par2, RepairWatcher, find_par2_for

FAKE = """#!/usr/bin/env python3
import sys, os
# args: <cmd> [opts] <par2-or-file>...
args = [a for a in sys.argv[1:]]
cmd = args[0]
# find the .par2 or last argument
targets = [a for a in args if a.endswith(".par2") or os.path.exists(a)]
key = targets[0] if targets else ""
if cmd == "v":
    if "missing" in key or "missing" in " ".join(args):
        print("Par2 v0.4.1")
        print("Missing file: movie.part1.rar")
        sys.exit(2)
    if "corrupt" in key:
        print("Par2 v0.4.1")
        print("Corrupt file: movie.part2.rar")
        sys.exit(3)
    print("Par2 v0.4.1")
    print("All files present and verified")
    sys.exit(0)
if cmd == "r":
    if "missing" in key or "corrupt" in key:
        print("Par2 v0.4.1")
        print("Repaired file: movie.part1.rar")
        sys.exit(0)
    print("Par2 v0.4.1")
    print("Nothing to repair")
    sys.exit(0)
if cmd == "c":
    print("Par2 v0.4.1")
    print("Set created: movie.par2")
    sys.exit(0)
sys.exit(255)
"""


@pytest.fixture
def fake_par2(tmp_path):
    exe = tmp_path / "par2cmdline"
    exe.write_text(FAKE)
    os.chmod(exe, 0o755)
    return str(exe)


def test_available_detection(tmp_path):
    assert Par2(str(tmp_path / "nope")).available is False
    assert Par2("some/real/path/that/does/not/exist").available is False


def test_verify_missing_classified(fake_par2, tmp_path):
    p = tmp_path / "movie-missing.par2"; p.write_text("")
    par2 = Par2(fake_par2)
    r = par2.verify(str(p))
    assert par2.available is True
    assert r.code == rep.EXIT_FILE_MISSING
    assert r.action == "needs-repair"
    assert "missing" in r.note.lower()


def test_verify_corrupt_classified(fake_par2, tmp_path):
    p = tmp_path / "movie-corrupt.par2"; p.write_text("")
    r = Par2(fake_par2).verify(str(p))
    assert r.code == rep.EXIT_FILE_CORRUPT
    assert r.note.lower().startswith("corrupt")


def test_verify_ok(fake_par2, tmp_path):
    p = tmp_path / "movie-clean.par2"; p.write_text("")
    r = Par2(fake_par2).verify(str(p))
    assert r.code == 0
    assert r.action == "verified"
    assert r.note == "all files present and verified"


def test_repair_reconstructs(fake_par2, tmp_path):
    p = tmp_path / "movie-missing.par2"; p.write_text("")
    r = Par2(fake_par2).repair(str(p))
    assert r.code == 0
    assert r.action == "repaired"


def test_binary_not_found_is_graceful(tmp_path):
    p = tmp_path / "x.par2"; p.write_text("")
    par2 = Par2(str(tmp_path / "absent/par2cmdline"))
    r = par2.verify(str(p))
    assert r.action == "error"
    assert "not found" in r.stderr.lower()
    assert r.code == rep.EXIT_ERROR


def test_find_par2_for(tmp_path):
    d = tmp_path / "dl"; d.mkdir()
    (d / "movie.rar").write_text("x")
    (d / "movie.rar.par2").write_text("y")
    found = find_par2_for(str(d / "movie.rar"))
    assert found and found.endswith("movie.rar.par2")
    assert find_par2_for(str(d / "other.rar")) is None


def test_watcher_repairs_incomplete_sets(fake_par2, tmp_path):
    d = tmp_path / "downloads"; d.mkdir()
    (d / "a-missing.par2").write_text("")
    (d / "b-clean.par2").write_text("")
    w = RepairWatcher(par2=Par2(fake_par2), watch_dir=str(d),
                      output_dir=str(d), interval=999)
    actions = w.scan_once()
    by = {os.path.basename(a.par2): a for a in actions}
    assert by["a-missing.par2"].action == "repaired"
    assert by["b-clean.par2"].action == "verified"
    # results recorded and bounded
    assert len(w.results) == 2
    w.stop()


def test_watcher_background_start_stop(fake_par2, tmp_path):
    d = tmp_path / "downloads"; d.mkdir()
    w = RepairWatcher(par2=Par2(fake_par2), watch_dir=str(d), interval=0)
    w.start()
    time.sleep(0.3)
    assert w._thread is not None
    w.stop()
    assert w._thread is None
