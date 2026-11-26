"""PAR2 verification and repair.

Wraps the external ``par2cmdline`` tool (bundled under ``tools/par2`` in
the portable build) to verify and repair incomplete or corrupt binaries
after a Usenet download.

Two entry points:

* :func:`Par2.verify` / :func:`Par2.repair` — one-shot operations on a
  ``.par2`` set file.
* :class:`RepairWatcher` — a background thread that periodically scans a
  downloads directory for ``.par2`` sets and repairs any that report
  missing or corrupt members. This is the "auto-repair incomplete
  downloads" behaviour the original NewsLeecher provides (it shells out
  to its PAR2 API; here we drive the equivalent ``par2cmdline`` binary).

All commands run with a hard timeout and their stdout/stderr is captured
so the GUI can surface diagnostic text. The engine degrades gracefully
when the binary is absent (returns a clear ``not found`` status) rather
than raising, so the rest of the app keeps running.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field

# par2cmdline exit codes (subset)
EXIT_OK = 0
EXIT_FILE_MISSING = 2
EXIT_FILE_CORRUPT = 3
EXIT_ERROR = 255


@dataclass
class RepairResult:
    par2: str
    action: str            # "verified" | "repaired" | "needs-repair" | "error"
    code: int
    stdout: str = ""
    stderr: str = ""
    note: str = ""


class Par2:
    """Thin, defensive wrapper around a par2cmdline binary."""

    def __init__(self, binary: str):
        self.binary = binary

    @property
    def available(self) -> bool:
        return bool(self.binary) and os.path.exists(self.binary)

    def _run(self, *args, timeout: int = 120) -> tuple[int, str, str]:
        if not self.available:
            return EXIT_ERROR, "", "par2cmdline binary not found"
        cmd = [self.binary, *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, p.stdout or "", p.stderr or ""
        except FileNotFoundError:
            return EXIT_ERROR, "", "par2cmdline binary not found"
        except subprocess.TimeoutExpired:
            return EXIT_ERROR, "", "par2cmdline timed out"
        except OSError as e:
            return EXIT_ERROR, "", f"failed to run par2cmdline: {e}"

    def verify(self, par2_file: str, output_dir: str | None = None,
               timeout: int = 120) -> RepairResult:
        """Run ``par2 v``; classify the result."""
        args = ["v"]
        if output_dir:
            args += ["-o", output_dir]
        args.append(par2_file)
        code, out, err = self._run(*args, timeout=timeout)
        note = _classify(code, out, err)
        return RepairResult(par2=par2_file, action=_action(code),
                            code=code, stdout=out, stderr=err, note=note)

    def repair(self, par2_file: str, output_dir: str | None = None,
               timeout: int = 300) -> RepairResult:
        """Run ``par2 r``; attempts to reconstruct missing/corrupt files."""
        args = ["r"]
        if output_dir:
            args += ["-o", output_dir]
        args.append(par2_file)
        code, out, err = self._run(*args, timeout=timeout)
        note = _classify(code, out, err)
        return RepairResult(par2=par2_file, action=_action(code, repaired=True),
                            code=code, stdout=out, stderr=err, note=note)

    def create(self, files: list[str], redundancy: int = 10,
               output_dir: str | None = None,
               timeout: int = 300) -> RepairResult:
        """Create a ``.par2`` set from a list of source files."""
        args = ["c", "-r", str(redundancy)]
        if output_dir:
            args += ["-o", output_dir]
        args += files
        if not files:
            return RepairResult(par2="", action="error", code=EXIT_ERROR,
                                note="no source files")
        name = files[0]
        code, out, err = self._run(*args, timeout=timeout)
        return RepairResult(par2=name, action=_action(code), code=code,
                            stdout=out, stderr=err,
                            note=_classify(code, out, err))


def _action(code: int, repaired: bool = False) -> str:
    if code == EXIT_OK:
        return "repaired" if repaired else "verified"
    if code in (EXIT_FILE_MISSING, EXIT_FILE_CORRUPT):
        return "needs-repair"
    return "error"


def _classify(code: int, out: str, err: str) -> str:
    text = (out + "\n" + err)
    low = text.lower()
    if code == EXIT_OK:
        if "all files" in low and ("present" in low or "verified" in low):
            return "all files present and verified"
        return "ok"
    if code == EXIT_FILE_MISSING or "missing file" in low:
        return "one or more files are missing"
    if code == EXIT_FILE_CORRUPT or "corrupt" in low:
        return "corrupt file detected"
    if "not found" in low or code == EXIT_ERROR:
        return "par2cmdline not available or errored"
    return f"exit code {code}"


@dataclass
class RepairWatcher:
    """Background auto-repair of incomplete downloads.

    Scans ``watch_dir`` for ``.par2`` set files on an interval. For each,
    it verifies the set; if any member is missing or corrupt it runs a
    repair. Actions are appended to ``results`` (bounded) and an optional
    callback is invoked per action for live UI updates.

    The watcher is a daemon thread; call :meth:`stop` to terminate it.
    """
    par2: Par2
    watch_dir: str
    output_dir: str | None = None
    interval: int = 60
    max_results: int = 200
    on_action: object | None = None

    results: list[RepairResult] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="par2-repair-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _record(self, res: RepairResult) -> None:
        self.results.append(res)
        if len(self.results) > self.max_results:
            self.results = self.results[-self.max_results:]
        if self.on_action is not None:
            try:
                self.on_action(res)
            except Exception:
                pass

    def scan_once(self) -> list[RepairResult]:
        """One scan: verify every ``.par2`` in watch_dir, repair failing
        sets. Returns the actions taken this pass."""
        actions: list[RepairResult] = []
        if not os.path.isdir(self.watch_dir):
            return actions
        for name in sorted(os.listdir(self.watch_dir)):
            if self._stop.is_set():
                break
            if not name.lower().endswith(".par2"):
                continue
            path = os.path.join(self.watch_dir, name)
            v = self.par2.verify(path, self.output_dir)
            if v.code in (EXIT_FILE_MISSING, EXIT_FILE_CORRUPT):
                r = self.par2.repair(path, self.output_dir)
                self._record(r)
                actions.append(r)
            else:
                self._record(v)
                actions.append(v)
        return actions

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:  # never kill the watcher
                self._record(RepairResult(par2="", action="error",
                                          code=EXIT_ERROR, note=repr(e)))
            self._stop.wait(self.interval)


def find_par2_for(binary_path: str, search_dirs: list[str] | None = None
                  ) -> str | None:
    """Locate a ``.par2`` set that could repair ``binary_path``.

    Looks for ``<name>.par2`` / ``<name>.<ext>.par2`` in the binary's
    directory and any extra dirs. Returns the first match or ``None``.
    """
    d = os.path.dirname(binary_path) or "."
    base = os.path.basename(binary_path)
    candidates = [f"{base}.par2", f"{os.path.splitext(base)[0]}.par2"]
    for extra in search_dirs or []:
        if extra:
            for c in candidates:
                p = os.path.join(extra, c)
                if os.path.exists(p):
                    return p
    for c in candidates:
        p = os.path.join(d, c)
        if os.path.exists(p):
            return p
    return None
