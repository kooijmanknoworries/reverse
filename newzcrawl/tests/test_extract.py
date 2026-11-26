"""Tests for the extraction / Unpack-monitor module.

The real ``unpack.exe`` is a Windows binary, so these tests drive the
:class:`Extractor` with a small *fake* unpack script (shell) that mimics
the documented behaviour: given ``<archive> <password-list>`` it creates an
``unpack_<name>/`` directory next to the archive and exits 0. This exercises
the directory-diff success detection, password-list materialisation and the
watcher's readiness/stability logic.
"""

import os
import stat
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from newzcrawl.extract import (  # noqa: E402
    ARCHIVE_EXTS,
    ExtractWatcher,
    Extractor,
    is_archive,
    _split_key,
)


def make_fake_unpack(tmp_path, name="fake_unpack.sh"):
    """Create an executable script that mimics Unpack: makes
    unpack_<archive-name>/ next to the archive."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        ARCH="$1"
        PW="$2"
        base=$(basename "$ARCH")
        dir=$(dirname "$ARCH")
        out="$dir/unpack_$base"
        mkdir -p "$out"
        echo extracted > "$out/marker.txt"
        # record the password list we were given, for assertions
        if [ -n "$PW" ]; then cat "$PW" > "$dir/.pw_given"; fi
        exit 0
    """))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


# -- unit: archive detection -------------------------------------------
def test_is_archive():
    assert is_archive("movie.7z")
    assert is_archive("movie.zip")
    assert is_archive("movie.rar")
    assert is_archive("movie.iso")
    assert is_archive("movie.ZIP")
    assert not is_archive("movie.bin")
    assert not is_archive("movie.par2")


def test_split_key_variants():
    assert _split_key("movie.part01.rar") == ("movie", 1, ".rar")
    assert _split_key("movie.part02.rar") == ("movie", 2, ".rar")
    assert _split_key("movie.001") == ("movie", 1, ".part")
    assert _split_key("movie.rar") is None
    assert _split_key("movie.bin") is None


# -- password list ------------------------------------------------------
def test_read_passwords_comments_blanks_dedup(tmp_path):
    plist = tmp_path / "pws.txt"
    plist.write_text("alpha\n\n# comment line\nbeta\nalpha\n  spaced  \n")
    ex = Extractor(str(tmp_path / "none"), password_list_path=str(plist),
                   app_dir=str(tmp_path))
    assert ex.read_passwords() == ["alpha", "beta", "spaced"]


def test_read_passwords_single_first(tmp_path):
    plist = tmp_path / "list.txt"
    plist.write_text("from-list\n")
    single = tmp_path / "single.txt"
    single.write_text("known-pw\nignored\n")
    ex = Extractor(str(tmp_path / "none"), password_list_path=str(plist),
                   password_file_path=str(single), app_dir=str(tmp_path))
    pws = ex.read_passwords()
    assert pws[0] == "known-pw"
    assert "from-list" in pws


def test_read_passwords_missing_files(tmp_path):
    ex = Extractor(str(tmp_path / "none"),
                   password_list_path=str(tmp_path / "absent.txt"),
                   app_dir=str(tmp_path))
    assert ex.read_passwords() == []


# -- one-shot extraction with the fake unpack ---------------------------
def test_extract_creates_output_dir(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    arch = dl / "movie.zip"
    arch.write_bytes(b"zipdata")
    ex = Extractor(fake, app_dir=str(tmp_path))
    r = ex.extract(str(arch))
    assert r.ok and r.action == "extracted"
    out = dl / "unpack_movie.zip"
    assert out.is_dir()
    assert r.output_dir == str(out)


def test_extract_passes_password_list(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    arch = dl / "secret.rar"
    arch.write_bytes(b"rardata")
    plist = tmp_path / "pws.txt"
    plist.write_text("pw1\npw2\n")
    ex = Extractor(fake, password_list_path=str(plist), app_dir=str(tmp_path))
    r = ex.extract(str(arch))
    assert r.ok
    given = (dl / ".pw_given").read_text().splitlines()
    assert given == ["pw1", "pw2"]


def test_extract_missing_binary_graceful(tmp_path):
    ex = Extractor(str(tmp_path / "absent/unpack.exe"), app_dir=str(tmp_path))
    arch = tmp_path / "a.zip"
    arch.write_bytes(b"x")
    r = ex.extract(str(arch))
    assert not r.ok and r.action == "error"
    assert "not found" in r.note


def test_extract_missing_archive(tmp_path):
    fake = make_fake_unpack(tmp_path)
    ex = Extractor(fake, app_dir=str(tmp_path))
    r = ex.extract(str(tmp_path / "nope.zip"))
    assert not r.ok and "not found" in r.note


# -- watcher readiness ---------------------------------------------------
def test_watcher_waits_for_stable_size(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    ex = Extractor(fake, app_dir=str(tmp_path))
    w = ExtractWatcher(ex, str(dl), interval=999)
    arch = dl / "movie.zip"
    arch.write_bytes(b"1234")
    # first sighting: not ready (size not yet stable)
    assert w.scan_once() == []
    # still downloading: size changes -> not ready
    arch.write_bytes(b"12345678")
    assert w.scan_once() == []
    # now stable across two scans -> extracted
    assert len(w.scan_once()) == 1
    assert (dl / "unpack_movie.zip").is_dir()


def test_watcher_does_not_re_extract(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    ex = Extractor(fake, app_dir=str(tmp_path))
    w = ExtractWatcher(ex, str(dl), interval=999)
    arch = dl / "movie.zip"
    arch.write_bytes(b"1234")
    w.scan_once()          # sighting 1
    w.scan_once()          # sighting 2 -> extracted
    assert (dl / "unpack_movie.zip").is_dir()
    # further scans must not attempt it again
    before = len(w.results)
    assert w.scan_once() == []
    assert len(w.results) == before


def test_watcher_split_archive_gaps(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    ex = Extractor(fake, app_dir=str(tmp_path))
    w = ExtractWatcher(ex, str(dl), interval=999)
    # gap: part 1 and 3 present, part 2 missing -> never ready
    (dl / "movie.part01.rar").write_bytes(b"a")
    (dl / "movie.part03.rar").write_bytes(b"a")
    for _ in range(3):
        assert w.scan_once() == []
    # fill the gap -> the complete set is now considered
    (dl / "movie.part02.rar").write_bytes(b"a")
    w.scan_once()   # sighting 1 of the complete set
    assert w.scan_once()          # sighting 2 -> extracted
    # the set (represented by part 1) should have been extracted
    assert (dl / "unpack_movie.part01.rar").is_dir()


def test_watcher_ignores_non_archives(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    ex = Extractor(fake, app_dir=str(tmp_path))
    w = ExtractWatcher(ex, str(dl), interval=999)
    (dl / "notes.txt").write_bytes(b"hi")
    (dl / "movie.bin").write_bytes(b"raw")
    assert w.scan_once() == []
    assert w.scan_once() == []
    assert not w.results


def test_watcher_start_stop(tmp_path):
    fake = make_fake_unpack(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    ex = Extractor(fake, app_dir=str(tmp_path))
    w = ExtractWatcher(ex, str(dl), interval=1)
    w.start()
    assert w._thread is not None and w._thread.is_alive()
    w.stop()
    assert w._thread is None
