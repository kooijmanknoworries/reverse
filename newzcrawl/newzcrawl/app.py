"""NewzCrawl application entry point.

Wires the core (config, NNTP connection factory, download queue, search,
PAR2 repair watcher) to the local web UI and runs it. Designed to run in
two modes:

* ``python -m newzcrawl.app`` — run the interactive UI (default).
* headless is also possible by constructing :class:`Core` directly, which
  is what the automated tests do.

The UI binds to 127.0.0.1 (or the configured host) so it is only
reachable on the local machine — nothing is exposed to the network and no
admin rights are required, matching the portable goal.
"""

from __future__ import annotations

import os
import sys
import webbrowser

from newzcrawl.config import Config, default_settings_path
from newzcrawl.web.server import Core, serve


def app_dir() -> str:
    """The portable app root, used to resolve bundled tools and the
    downloads dir. In a frozen build this is the executable's folder
    (so ``tools/`` and ``downloads/`` sit next to ``NewzCrawl.exe``);
    in development it is the folder containing the ``newzcrawl``
    package."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_core(settings_path: str | None = None) -> Core:
    """Construct a fully-wired :class:`Core` (does not start the UI)."""
    root = app_dir()
    settings_path = settings_path or default_settings_path()
    cfg = Config(settings_path)
    if not os.path.exists(settings_path):
        cfg.save()  # materialize a portable settings.json on first run
    core = Core()
    core.config = cfg
    core.app_dir = root

    downloads = os.path.join(root, "downloads")
    os.makedirs(downloads, exist_ok=True)
    core.downloads_dir = downloads

    # Connection factory: a fresh, authenticated client on demand.
    def factory():
        from newzcrawl.nntp_client import NNTPClient
        c = NNTPClient(cfg.get("host"), int(cfg.get("port")), timeout=60)
        c.connect()
        user, pw = cfg.get("username"), cfg.get("password")
        if user and pw:
            c.authenticate(user, pw)
        return c
    core.client_factory = factory

    # Search (local header search over the configured connection).
    from newzcrawl.search import LocalHeaderSearch
    core.search = LocalHeaderSearch(factory, window=2000)

    # PAR2 repair.
    from newzcrawl.repair import Par2
    core.repair = Par2(cfg.get("par2cmdline_path"))

    # Start the auto-repair watcher if enabled.
    if cfg.get("download_auto_repair"):
        core.start_watcher()
    return core


def run(headless: bool = False, port: int | None = None,
        settings_path: str | None = None) -> int:
    """Start the web UI and block until interrupted."""
    core = build_core(settings_path)
    cfg = core.config
    host = cfg.get("ui_host") or "127.0.0.1"
    p = port or int(cfg.get("ui_port"))

    # If the port is taken, find a free one nearby and inform the user.
    import socket
    server = None
    used = p
    for attempt in range(20):
        try:
            server = serve(core, host, used)
            break
        except OSError:
            used += 1
    if server is None:
        print(f"NewzCrawl: could not bind a free port near {p}")
        return 1

    url = f"http://{host}:{used}/"
    if headless:
        print(f"NewzCrawl web UI running at {url} (Ctrl-C to stop)")
    else:
        print(f"NewzCrawl web UI running at {url}")
        print("Press Ctrl-C in this terminal to stop.")
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nNewzCrawl: shutting down")
    finally:
        if core.watcher is not None:
            core.stop_watcher()
        server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    headless = "--headless" in argv
    settings_path = None
    for i, a in enumerate(argv):
        if a == "--settings" and i + 1 < len(argv):
            settings_path = argv[i + 1]
    return run(headless=headless, settings_path=settings_path)


if __name__ == "__main__":
    raise SystemExit(main())
