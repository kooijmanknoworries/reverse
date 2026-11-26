"""NewzCrawl application entry point.

Wires the core (config, NNTP connection factory, download queue, search,
PAR2 repair watcher, extraction watcher) to the dashboard UI and runs it.

Interface modes (pick with a flag, or let it decide):

* **desktop** — a native window (Edge WebView2) showing the dashboard,
  no browser involved. This is the default for the installed / packaged
  app (``NewzCrawl.exe``).
* **browser** — start the local web UI and open it in the default browser
  (``--browser``; useful for troubleshooting).
* **headless** — run the server without opening anything (``--headless``).

The UI binds to 127.0.0.1 (or the configured host) so it is only
reachable on the local machine — nothing is exposed to the network and no
admin rights are required, matching the portable goal.

A ``.nzb`` file can be given as the first non-flag argument (this is what
Windows uses when you double-click a .nzb with the "Open with NewzCrawl"
association); the file is loaded into the NZB tab.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

from newzcrawl.config import (Config, app_root, data_dir, default_settings_path)
from newzcrawl.web.server import Core, serve


def _ensure_stdout() -> None:
    """Make ``print()`` safe in a frozen windowed build.

    A ``console=False`` PyInstaller app on Windows has ``sys.stdout`` and
    ``sys.stderr`` set to ``None`` (no console to attach to), so any
    ``print()`` would raise ``AttributeError`` and crash the app on
    startup. Point the streams at a log file in the user-data directory so
    the app keeps working and the user has a ``newzcrawl.log`` to debug
    with. In development (a real console exists) this is a no-op.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    target = None
    try:
        target = open(os.path.join(data_dir(), "newzcrawl.log"),
                      "a", encoding="utf-8", buffering=1)
    except Exception:
        target = None
    if target is None:  # last resort: discard output rather than crash
        import io
        target = io.StringIO()
    if sys.stdout is None:
        sys.stdout = target
    if sys.stderr is None:
        sys.stderr = target


def _pending_nzb() -> str | None:
    """Path of a .nzb passed on the command line, if any."""
    for a in sys.argv[1:]:
        if a.lower().endswith(".nzb") and os.path.exists(a):
            return os.path.abspath(a)
    return None


def build_core(settings_path: str | None = None) -> Core:
    """Construct a fully-wired :class:`Core` (does not start the UI)."""
    root = app_root()
    data = data_dir()
    os.makedirs(data, exist_ok=True)
    settings_path = settings_path or default_settings_path()
    cfg = Config(settings_path)
    if not os.path.exists(settings_path):
        cfg.save()  # materialize settings.json on first run
    core = Core()
    core.config = cfg
    core.app_dir = root            # tools/ lives with the program
    core.data_dir = data           # settings/downloads live with the user

    downloads = os.path.join(data, "downloads")
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

    # Extraction / unpacking (drives the bundled Unpack app).
    core.build_extract_watcher()
    if cfg.get("extract_auto_monitor"):
        core.start_extract_watcher()

    # Seed a starter password list in the data dir so the user can edit it
    # without creating the file by hand.
    pl = cfg.get("password_list_path")
    if pl and not os.path.isabs(pl) and \
            not os.path.exists(os.path.join(data, pl)):
        with open(os.path.join(data, pl), "w", encoding="utf-8") as f:
            f.write("# One password per line; lines starting with # are "
                    "ignored.\n# Add the passwords of your encrypted "
                    "archives here.\n")
    return core


def _start_server(core: Core, port: int | None = None):
    """Bind the local server (trying nearby ports if busy) and run it in
    a daemon thread. Returns ``(server, actual_port)`` or ``(None, None)``."""
    host = core.config.get("ui_host") or "127.0.0.1"
    p = port or int(core.config.get("ui_port"))
    server = None
    used = p
    for _ in range(20):
        try:
            server = serve(core, host, used)
            break
        except OSError:
            used += 1
    if server is None:
        return None, None
    t = threading.Thread(target=server.serve_forever,
                         name="http-server", daemon=True)
    t.start()
    return server, used


def _shutdown(core: Core, server) -> None:
    if core.watcher is not None:
        core.stop_watcher()
    if core.extract_watcher is not None:
        core.stop_extract_watcher()
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def _choose_mode(flag: str | None) -> str:
    """Resolve the interface mode: an explicit flag wins, otherwise a
    frozen (packaged) build defaults to the native desktop window and
    development / console runs use the browser."""
    if flag in ("desktop", "browser", "headless"):
        return flag
    import sys as _s
    if getattr(_s, "frozen", False):
        return "desktop"
    return "browser"


def _browser_fallback(url: str, server) -> int:
    """Open the web UI in the default browser and block until Ctrl-C.
    Used when the native desktop window cannot be created."""
    print(f"NewzCrawl web UI running at {url} (Ctrl-C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _desktop_run(core: Core, server, port: int, nzb_path: str | None) -> int:
    """Show the dashboard in a native window (no browser).

    Runs the pywebview GUI event loop (``webview.start``) which blocks on
    the main thread until the user closes the window. The web server keeps
    running on its daemon thread, so all API calls keep working while the
    window is open. If the desktop window cannot be created (e.g. no
    WebView2 runtime), it falls back to the browser instead of dying.
    """
    url = f"http://127.0.0.1:{port}/"
    try:
        from webview import create_window
        import webview
    except ImportError as e:
        print("NewzCrawl: desktop window is not available "
              f"({e!r}); falling back to the browser.")
        return _browser_fallback(url, server)

    try:
        window = create_window(
            "NewzCrawl",
            url,
            width=1240,
            height=840,
            min_size=(900, 620),
            background_color="#0b0e14",
            text_select=True,
            resizable=True,
            fullscreen=False,
        )

        if nzb_path:
            # Let the page finish loading, then pre-load the .nzb into the
            # NZB tab (double-click / "Open with" support). The file is
            # read here (Python side) and handed to the page via
            # evaluate_js, so the UI never opens an arbitrary path itself.
            def _load_nzb():
                import json
                import time
                time.sleep(1.5)  # give the dashboard a moment to load
                try:
                    with open(nzb_path, "r", encoding="utf-8",
                              errors="replace") as f:
                        xml = f.read()
                    payload = json.dumps({"name": os.path.basename(nzb_path),
                                          "xml": xml})
                    window.evaluate_js(
                        f"window.__nzbLoaded && window.__nzbLoaded({payload})")
                except Exception as e:
                    print(f"NewzCrawl: could not preload {nzb_path}: {e!r}")
            threading.Thread(target=_load_nzb, daemon=True).start()

        # Block on the GUI event loop until the window is closed.
        webview.start()
    except Exception as e:
        print(f"NewzCrawl: desktop window failed ({e!r}); "
              "falling back to the browser.")
        return _browser_fallback(url, server)
    return 0


def run(mode: str | None = None, port: int | None = None,
        settings_path: str | None = None) -> int:
    """Start NewzCrawl in the requested interface mode and block until
    the user quits (window closed, or Ctrl-C for browser/headless)."""
    _ensure_stdout()
    core = build_core(settings_path)
    server, used = _start_server(core, port)
    if server is None:
        print(f"NewzCrawl: could not bind a free port near "
              f"{port or core.config.get('ui_port')}")
        return 1

    mode = _choose_mode(mode)
    url = f"http://{core.config.get('ui_host') or '127.0.0.1'}:{used}/"
    print(f"NewzCrawl web UI running at {url} (mode: {mode})")

    nzb_path = _pending_nzb()
    if nzb_path:
        print(f"NewzCrawl: loading {nzb_path}")

    try:
        if mode == "headless":
            print("NewzCrawl: headless (Ctrl-C to stop)")
            server.serve_forever()
        elif mode == "browser":
            print("Press Ctrl-C in this terminal to stop.")
            try:
                webbrowser.open(url)
            except Exception:
                pass
            server.serve_forever()
        else:  # desktop
            return _desktop_run(core, server, used, nzb_path)
    except KeyboardInterrupt:
        print("\nNewzCrawl: shutting down")
    finally:
        _shutdown(core, server)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = None
    settings_path = None
    port = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--headless":
            mode = "headless"
        elif a == "--browser":
            mode = "browser"
        elif a == "--desktop":
            mode = "desktop"
        elif a == "--settings" and i + 1 < len(argv):
            settings_path = argv[i + 1]
            i += 1
        elif a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 1
        # .nzb arguments are picked up via _pending_nzb()
        i += 1
    return run(mode=mode, port=port, settings_path=settings_path)


if __name__ == "__main__":
    raise SystemExit(main())
