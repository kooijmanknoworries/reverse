"""Archive extraction and download monitoring.

Watches the ``downloads/`` folder and, when an archive is ready (complete),
extracts it — including password-protected archives — by driving the bundled
**Unpack** app (the self-contained .NET tool from
``kooijmanknoworries/unpack``), which tries every password in a configurable
password list and, on the first match, unpacks the archive.

Design goals:
* **No manual collection** — the Unpack folder, 7-Zip and par2cmdline are
  bundled under ``tools/``; this module locates them relative to the app.
* **Robust success detection** — we snapshot the archive's directory before
  and after the run and treat any new top-level directory as the extraction
  output (Unpack writes ``unpack_<name>/`` next to the archive), so we do not
  depend on a specific exit code.
* **Password list support** — a one-per-line ``.txt`` list (``#`` comments,
  blanks ignored), plus an optional single known password that is tried first.
* **Graceful degradation** — if the extractor binary is missing, operations
  report a clear error rather than raising, so the rest of the app keeps
  running (mirrors the repair module).

Two entry points:
* :func:`Extractor.extract` — one-shot extraction of a single archive.
* :class:`ExtractWatcher` — a background thread that periodically scans
  ``downloads/`` and extracts ready, not-yet-extracted archives.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

#: Extensions treated as extractable archives (lowercase).
ARCHIVE_EXTS = (
    ".zip", ".7z", ".rar", ".iso", ".tar", ".gz", ".bz2", ".xz",
    ".arj", ".cab", ".lzh", ".lzma", ".zst",
)

@dataclass
class ExtractResult:
    archive: str
    ok: bool
    action: str              # "extracted" | "skipped" | "error"
    output_dir: str = ""
    note: str = ""
    stdout: str = ""
    stderr: str = ""


class Extractor:
    """One-shot archive extraction via the bundled Unpack app."""

    def __init__(self, unpack_exe: str, password_list_path: str = "",
                 password_file_path: str = "", app_dir: str = ".",
                 timeout: int = 900):
        self.unpack_exe = unpack_exe
        self.password_list_path = password_list_path
        self.password_file_path = password_file_path
        self.app_dir = app_dir
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.unpack_exe) and os.path.exists(self.unpack_exe)

    def _resolve(self, path: str) -> str:
        if not path or os.path.isabs(path):
            return path
        return os.path.join(self.app_dir, path)

    # -- password handling -------------------------------------------
    def read_passwords(self) -> list[str]:
        """Return the ordered, de-duplicated password list.

        The optional single known password (``password_file_path``) is
        tried first, followed by the lines of the password list file.
        Lines that are blank or start with ``#`` are ignored.
        """
        seen: set[str] = set()
        out: list[str] = []

        def add(pw: str):
            pw = pw.strip()
            if pw and pw not in seen:
                seen.add(pw)
                out.append(pw)

        single = self._resolve(self.password_file_path)
        if single and os.path.exists(single):
            with open(single, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        add(line)
                        break  # single known password: take the first
        plist = self._resolve(self.password_list_path)
        if plist and os.path.exists(plist):
            with open(plist, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    add(line)
        return out

    #: Placeholder password used when the user has configured none.
    #: Unpack's CLI refuses an empty list (exit 2), so we always hand it at
    #: least one entry; 7z ignores the password for unencrypted archives.
    _NO_PASSWORD_PLACEHOLDER = "-"

    def _write_password_list(self) -> str:
        """Materialise the combined password list to a temp file for the
        Unpack CLI (which requires a non-empty list). When the user has
        set no passwords, a single placeholder line is used: 7z ignores
        the password for unencrypted archives, so extraction still works."""
        pws = self.read_passwords() or [self._NO_PASSWORD_PLACEHOLDER]
        fd, path = tempfile.mkstemp(prefix="newzcrawl_pws_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(pws) + "\n")
        return path

    # -- extraction ----------------------------------------------------
    def extract(self, archive: str) -> ExtractResult:
        archive = os.path.abspath(archive)
        if not self.available:
            return ExtractResult(archive, False, "error",
                                 note="unpack.exe not found")
        if not os.path.exists(archive):
            return ExtractResult(archive, False, "error",
                                 note="archive not found")

        parent = os.path.dirname(archive)
        before = set(os.listdir(parent))

        pwlist = self._write_password_list()
        try:
            cmd = [self.unpack_exe, archive, pwlist]
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout)
            out, err = p.stdout or "", p.stderr or ""
            code = p.returncode
        except subprocess.TimeoutExpired:
            return ExtractResult(archive, False, "error",
                                 note="unpack timed out")
        except OSError as e:
            return ExtractResult(archive, False, "error", note=f"{e!r}")
        finally:
            if pwlist:
                try:
                    os.unlink(pwlist)
                except OSError:
                    pass

        # Detect the extraction output by diffing the directory listing.
        after = set(os.listdir(parent))
        new_dirs = [d for d in (after - before)
                    if os.path.isdir(os.path.join(parent, d))]
        base = os.path.basename(archive)
        # Unpack names the output "unpack_<name>"; prefer that if present.
        output = ""
        for d in new_dirs:
            if d.lower().startswith("unpack_") or d.lower().startswith(
                    os.path.splitext(base)[0].lower()):
                output = os.path.join(parent, d)
                break
        if not output and len(new_dirs) == 1:
            output = os.path.join(parent, new_dirs[0])

        if code == 0 or output:
            note = f"extracted to {output}" if output else "ok (no new dir detected)"
            return ExtractResult(archive, True, "extracted",
                                 output_dir=output, note=note,
                                 stdout=out[-2000:], stderr=err[-1000:])
        # Non-zero and nothing new: likely no password matched or corrupt.
        return ExtractResult(archive, False, "error",
                             note=_classify_failure(out, err, code),
                             stdout=out[-2000:], stderr=err[-1000:])


def _classify_failure(out: str, err: str, code: int) -> str:
    """Map Unpack's documented exit codes to user-facing notes.

    ``0`` = password found, ``1`` = no match, ``2`` = bad arguments
    (missing archive / list), ``3`` = 7-Zip engine missing,
    ``130`` = stopped.
    """
    if code == 3:
        return ("7-Zip engine (7z.exe) not found next to unpack.exe - "
                "the tools folder may be incomplete")
    if code == 2:
        return "unpack rejected its arguments (archive or password list missing)"
    if code == 130:
        return "stopped"
    text = (out + "\n" + err).lower()
    if code == 1 or "no matching password" in text:
        return "no matching password found (add more to passwords.txt)"
    if "missing volume" in text or "next volume" in text:
        return "some archive volumes are missing"
    if "crc" in text or "corrupt" in text:
        return "archive read error (possibly incomplete/corrupt)"
    return f"unpack exited {code}"


# ---------------------------------------------------------------------------
# completeness / readiness
# ---------------------------------------------------------------------------

def is_archive(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in ARCHIVE_EXTS


def _split_key(name: str):
    """Return ``(base, part_number, ext)`` if ``name`` looks like a split
    archive part, else ``None``. Handles ``name.partNN.ext``,
    ``name.NNN.ext`` and extensionless ``name.NNN`` (2-3 digit part
    numbers, the common Usenet/RAR split convention)."""
    base, ext = os.path.splitext(name)
    ext = ext.lower()
    m = re.match(r"^(?P<base>.+)\.part(?P<n>\d+)$", base, re.I)
    if m:
        return m.group("base"), int(m.group("n")), ext
    m = re.match(r"^(?P<base>.+)\.(?P<n>\d{2,3})$", base, re.I)
    if m:
        return m.group("base"), int(m.group("n")), ext
    # extensionless part: splitext treats the ".NNN" suffix as the extension
    if re.fullmatch(r"\.\d{2,3}", ext):
        m = re.match(r"^(?P<base>.+)\.(?P<n>\d{2,3})$", name, re.I)
        if m:
            return m.group("base"), int(m.group("n")), ".part"
    return None


@dataclass
class _State:
    sizes: dict = field(default_factory=dict)     # path -> (size, scans)
    split_seen: dict = field(default_factory=dict)  # key -> stable size sig
    attempted: set = field(default_factory=set)   # path -> tried before
    extracted: set = field(default_factory=set)   # path -> has output


class ExtractWatcher:
    """Background monitor that extracts ready archives in a watched folder.

    "Ready" means:
    * single archive: non-empty and its size is stable across two scans
      (so we don't grab a still-downloading file);
    * split archive: all consecutive parts ``1..N`` are present and non-empty.

    Ready archives are extracted (once); a path is never re-attempted in the
    same session unless its output is removed.
    """

    def __init__(self, extractor: Extractor, watch_dir: str,
                 interval: int = 30, on_event=None,
                 on_complete=None):
        self.extractor = extractor
        self.watch_dir = watch_dir
        self.interval = interval
        self.on_event = on_event
        self.on_complete = on_complete  # called with ItemResult-like path
        self._state = _State()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.results: list[ExtractResult] = []

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="extract-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _record(self, res: ExtractResult) -> None:
        self.results.append(res)
        if len(self.results) > 200:
            self.results = self.results[-200:]
        if self.on_event is not None:
            try:
                self.on_event(res)
            except Exception:
                pass

    # -- readiness -----------------------------------------------------
    def _ready_single(self, path: str) -> bool:
        st = self._state.sizes.get(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size <= 0:
            return False
        if st is None or st[0] != size:
            self._state.sizes[path] = (size, 1)
            return False  # first sighting or size changed -> not stable yet
        scans = st[1] + 1
        self._state.sizes[path] = (size, scans)
        return scans >= 2

    def _split_complete(self, parts: dict) -> bool:
        """True if the set has consecutive parts 1..N, all non-empty.
        ``parts`` maps part_number -> path."""
        nums = sorted(parts)
        if not nums or nums[0] != 1:
            return False
        if nums != list(range(1, len(nums) + 1)):
            return False  # gap in part sequence
        for path in parts.values():
            try:
                if os.path.getsize(path) <= 0:
                    return False
            except OSError:
                return False
        return True

    def _split_stable(self, key, parts: dict) -> bool:
        """True when the whole set's sizes have been unchanged across two
        consecutive scans (so a still-downloading set is not grabbed)."""
        try:
            sig = tuple(sorted(os.path.getsize(p) for p in parts))
        except OSError:
            return False
        prev = self._state.split_seen.get(key)
        self._state.split_seen[key] = sig
        return prev == sig and len(sig) > 0

    def _group_entries(self, names: list[str]):
        """Split directory entries into ``(singles, splits)`` where
        ``singles`` are whole-archive paths and ``splits`` maps a
        (base, ext) key to ``{part_number: path}`` for split archives, so a
        whole set is handled together (represented by its lowest part)."""
        singles: list[str] = []
        splits: dict[tuple, dict] = {}
        for name in names:
            if name.startswith(".") or name.startswith("_"):
                continue
            if not is_archive(name):
                continue
            sk = _split_key(name)
            if sk:
                base, n, ext = sk
                key = (os.path.normcase(base), ext)
                splits.setdefault(key, {})[n] = os.path.join(self.watch_dir,
                                                             name)
            else:
                singles.append(os.path.join(self.watch_dir, name))
        return singles, splits

    def scan_once(self) -> list[ExtractResult]:
        actions: list[ExtractResult] = []
        if not os.path.isdir(self.watch_dir):
            return actions
        try:
            names = os.listdir(self.watch_dir)
        except OSError:
            return actions
        singles, splits = self._group_entries(names)

        candidates: list[tuple[str, str]] = []  # (primary_path, label)
        for path in singles:
            if path in self._state.extracted or path in self._state.attempted:
                continue
            if self._ready_single(path):
                candidates.append((path, os.path.basename(path)))
        for key, parts in splits.items():
            if any(p in self._state.extracted or p in self._state.attempted
                   for p in parts.values()):
                continue
            if self._split_complete(parts) and self._split_stable(key, parts):
                # represent the set by the lowest-numbered part
                primary = parts[min(parts)]
                candidates.append((primary, os.path.basename(primary)))

        for path, label in candidates:
            if self._stop.is_set():
                break
            res = self.extractor.extract(path)
            # mark the primary and any related parts as handled
            for p in self._related_parts(path):
                self._state.extracted.add(p)
                self._state.attempted.add(p)
            self._record(res)
            actions.append(res)
        return actions

    def _related_parts(self, primary: str) -> list[str]:
        """All part paths of the split set that ``primary`` belongs to
        (just ``[primary]`` for a single archive)."""
        sk = _split_key(os.path.basename(primary))
        if not sk:
            return [primary]
        base, _n, ext = sk
        key = (os.path.normcase(base), ext)
        out = [primary]
        for name in os.listdir(os.path.dirname(primary)):
            k2 = _split_key(name)
            if k2 and (os.path.normcase(k2[0]), k2[2]) == key:
                p = os.path.join(os.path.dirname(primary), name)
                if p not in out:
                    out.append(p)
        return out

    def try_now(self, path: str) -> ExtractResult:
        """Extract a specific archive immediately (manual button)."""
        path = os.path.abspath(path)
        res = self.extractor.extract(path)
        self._state.attempted.add(path)
        if res.ok:
            self._state.extracted.add(path)
        self._record(res)
        return res

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:  # never kill the watcher
                self._record(ExtractResult("", False, "error", note=repr(e)))
            self._stop.wait(self.interval)
