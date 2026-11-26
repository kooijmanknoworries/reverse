"""Local web UI: a small, dependency-free HTTP server.

Serves the single-page dashboard (``static/index.html``) and a JSON
``/api`` used by it. Everything binds to ``127.0.0.1`` (or the configured
``ui_host``) so the interface is reachable only on the local machine —
no exposure to the network, no admin rights required.

The server holds a reference to a ``Core`` (see newzcrawl/app.py) that
owns the config, connection factory, queue, search and repair watcher.
Each request handler translates between JSON and the Core.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Core:
    """Shared application state exposed to the HTTP handlers.

    This is intentionally a plain container the HTTP server can call; the
    actual construction of the config / queue / search / watcher is done
    in newzcrawl/app.py and injected here. Keeping the server decoupled
    from construction makes it easy to test with a stub Core.
    """

    def __init__(self):
        self.config = None                 # newzcrawl.config.Config
        self.client_factory = None         # callable -> connected NNTPClient
        self.queue = None                  # newzcrawl.queue.DownloadQueue
        self.downloads_dir = None
        self.app_dir = None                # portable app root (for tools/)
        self.search = None                 # newzcrawl.search.LocalHeaderSearch
        self.repair = None                 # newzcrawl.repair.Par2
        self.watcher = None                # newzcrawl.repair.RepairWatcher | None
        self.connected = False
        self.last_error = ""
        self._lock = threading.Lock()

    # -- helpers used by the API handlers ------------------------------
    def connection_factory(self):
        return self.client_factory()

    def _resolve_tool(self, key: str) -> str:
        """Resolve a tool path (relative to app_dir or absolute)."""
        val = self.config.get(key)
        if os.path.isabs(val):
            return val
        return os.path.join(self.app_dir or ".", val)

    def start_watcher(self) -> None:
        if self.watcher is None:
            from newzcrawl.repair import RepairWatcher
            self.watcher = RepairWatcher(
                par2=self.repair, watch_dir=self.downloads_dir,
                output_dir=self.downloads_dir,
                interval=int(self.config.get("download_repair_scan_interval")),
            )
        self.watcher.start()

    def stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = "NewzCrawl/0.1"

    # -- plumbing ------------------------------------------------------
    @property
    def core(self) -> Core:
        return self.server.core  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _safe_path(self, path: str) -> str | None:
        # resolve inside STATIC_DIR, refuse traversal
        base = os.path.realpath(STATIC_DIR)
        target = os.path.realpath(os.path.join(STATIC_DIR, path))
        if not target.startswith(base + os.sep) and target != base:
            return None
        return target

    # -- routing -------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path.startswith("/api/"):
            return self._api(path, parse_qs(url.query), b"")
        self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path
        body = self._read_body()
        if path.startswith("/api/"):
            return self._api(path, parse_qs(url.query), body)
        self.send_error(405)

    def _serve_static(self, rel: str):
        target = self._safe_path(rel)
        if target is None or not os.path.isfile(target):
            return self.send_error(404)
        ext = os.path.splitext(target)[1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- API -----------------------------------------------------------
    def _api(self, path: str, query: dict, body: bytes):
        try:
            handler = _ROUTER.get(path)
            if handler is None:
                return self._send_json({"error": "unknown endpoint"}, 404)
            result = handler(self.core, query, body)
            return self._send_json(result)
        except _ApiError as e:
            return self._send_json({"error": str(e)}, e.code)
        except Exception as e:  # never 500 the browser
            traceback.print_exc()
            return self._send_json(
                {"error": f"internal error: {e!r}"}, 500)


class _ApiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def _j(body: bytes) -> dict | list | None:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# API handlers (module-level so the router table is simple)
# ---------------------------------------------------------------------------

def api_status(core: Core, query, body):
    c = core.config
    return {
        "app": "NewzCrawl",
        "version": _version(),
        "connected": core.connected,
        "last_error": core.last_error,
        "host": c.get("host"), "port": c.get("port"),
        "ui_port": c.get("ui_port"),
        "downloads_dir": core.downloads_dir,
        "auto_repair": c.get("download_auto_repair"),
        "repair_running": bool(core.watcher and core.watcher._thread),
        "par2_available": bool(core.repair and core.repair.available),
        "search": "local-header",
    }


def api_settings_get(core: Core, query, body):
    return {"schema": core.config.schema()}


def api_settings_set(core: Core, query, body):
    payload = _j(body) or {}
    values = payload.get("values", payload)
    applied, rejected = {}, []
    for k, v in values.items():
        if core.config.set(k, v):
            applied[k] = core.config.get(k)
        else:
            rejected.append(k)
    if "password" in values and values["password"] == "":
        pass  # allow clearing
    # persist if the user asked (or always, for convenience)
    if "save" not in payload or payload["save"]:
        core.config.save()
    # apply side-effects
    if core.config.get("download_auto_repair") and core.watcher is None:
        core.start_watcher()
    elif not core.config.get("download_auto_repair") and core.watcher:
        core.stop_watcher()
    return {"applied": applied, "rejected": rejected}


def api_connect(core: Core, query, body):
    c = core.config
    from newzcrawl.nntp_client import NNTPClient, NNTPError
    try:
        client = NNTPClient(c.get("host"), int(c.get("port")), timeout=20)
        client.connect()
        user, pw = c.get("username"), c.get("password")
        if user and pw:
            client.authenticate(user, pw)
        caps = client.capabilities()
        client.close()
        core.connected = True
        core.last_error = ""
        return {"ok": True, "capabilities": caps}
    except NNTPError as e:
        core.connected = False
        core.last_error = f"NNTP {e.code}: {e.message}"
        return {"ok": False, "error": core.last_error}
    except OSError as e:
        core.connected = False
        core.last_error = f"connection error: {e}"
        return {"ok": False, "error": core.last_error}


def api_groups(core: Core, query, body):
    pat = (query.get("filter") or [""])[0].strip()
    if pat and not any(c in pat for c in "*?"):
        pat += "*"  # treat a bare prefix as a wildcard search
    client = core.connection_factory()
    try:
        if pat:
            groups = client.list_groups(pat)
        else:
            groups = client.list_active()
        return {"groups": groups[:500], "total": len(groups)}
    finally:
        try:
            client.close()
        except Exception:
            pass


def api_headers(core: Core, query, body):
    group = (query.get("group") or [""])[0]
    if not group:
        raise _ApiError("group required")
    window = int((query.get("window") or ["100"])[0])
    client = core.connection_factory()
    try:
        last = client.group(group)  # sets current group; returns article count
        lo = max(1, last - window + 1)
        rows = client.xover_dict(str(lo), str(last))
        out = []
        for num, f in rows.items():
            out.append({
                "num": int(num),
                "subject": f.get("Subject", ""),
                "from": f.get("From", ""),
                "date": f.get("Date", ""),
                "mid": f.get("Message-ID", ""),
                "bytes": int(f.get("Bytes", 0) or 0),
            })
        return {"group": group, "last": last, "articles": out}
    finally:
        try:
            client.close()
        except Exception:
            pass


def api_download(core: Core, query, body):
    """Enqueue a download. Body is raw NZB XML (or JSON with 'nzb')."""
    payload = _j(body)
    if payload and isinstance(payload, dict) and payload.get("nzb"):
        raw = payload["nzb"].encode("utf-8")
    else:
        raw = body
    if not raw or not raw.lstrip().startswith(b"<"):
        raise _ApiError("NZB XML required in body")
    from newzcrawl import nzb
    try:
        nzb_obj = nzb.parse_nzb(raw)
    except Exception as e:
        raise _ApiError(f"invalid NZB: {e}")
    with core._lock:
        core.queue = core.queue or _new_queue(core)
        results = core.queue.download(nzb_obj)
    return {
        "items": [
            {"name": r.item.name, "ok": r.ok, "complete": r.complete,
             "path": r.output_path, "size": r.size,
             "good": r.good, "corrupt": r.corrupt, "missing": r.missing,
             "holes": [list(h) for h in r.holes], "error": r.error}
            for r in results
        ]
    }


def api_search(core: Core, query, body):
    payload = _j(body) or {}
    q = payload.get("query", (query.get("q") or [""])[0])
    groups = payload.get("groups") or (query.get("groups") or [""])
    groups = [g for g in groups if g] or None
    limit = int(payload.get("limit", 100))
    hits = core.search.search(q, groups=groups, limit=limit)
    return {"query": q, "hits": [
        {"group": h.group, "num": h.article_number, "mid": h.message_id,
         "subject": h.subject, "from": h.from_, "date": h.date,
         "size": h.size, "score": h.score} for h in hits
    ]}


def api_repair_scan(core: Core, query, body):
    if not core.repair or not core.repair.available:
        return {"ok": False, "error": "par2cmdline not found", "actions": []}
    actions = core.watcher.scan_once() if core.watcher else \
        _one_shot_scan(core)
    return {"ok": True, "actions": _results(actions)}


def api_repair_status(core: Core, query, body):
    w = core.watcher
    return {
        "running": bool(w and w._thread),
        "interval": core.config.get("download_repair_scan_interval"),
        "results": _results(getattr(w, "results", [])[-50:]),
    }


def api_repair_toggle(core: Core, query, body):
    payload = _j(body) or {}
    enable = payload.get("enabled", core.config.get("download_auto_repair"))
    core.config.set("download_auto_repair", bool(enable))
    core.config.save()
    if enable:
        core.start_watcher()
    else:
        core.stop_watcher()
    return {"running": bool(core.watcher and core.watcher._thread)}


def _one_shot_scan(core):
    import os
    actions = []
    d = core.downloads_dir
    if not os.path.isdir(d):
        return actions
    for name in sorted(os.listdir(d)):
        if name.lower().endswith(".par2"):
            p = os.path.join(d, name)
            v = core.repair.verify(p, core.downloads_dir)
            if v.code in (2, 3):
                actions.append(core.repair.repair(p, core.downloads_dir))
            else:
                actions.append(v)
    return actions


def _results(actions):
    return [{"par2": os.path.basename(a.par2), "action": a.action,
             "code": a.code, "note": a.note} for a in actions]


def _new_queue(core):
    from newzcrawl.queue import DownloadQueue
    return DownloadQueue(
        core.connection_factory, core.downloads_dir,
        max_workers=core.config.get("threads_article_decoder"),
        max_retries=2,
        verify_crc=core.config.get("download_crc_checking_yenc"),
        skip_existing=core.config.get("download_skip_existing_files"),
    )


def _version():
    try:
        from newzcrawl import __version__
        return __version__
    except Exception:
        return "0.0.0"


_ROUTER = {
    "/api/status": api_status,
    "/api/settings": api_settings_get,
    "/api/settings/set": api_settings_set,
    "/api/connect": api_connect,
    "/api/groups": api_groups,
    "/api/headers": api_headers,
    "/api/download": api_download,
    "/api/search": api_search,
    "/api/repair/scan": api_repair_scan,
    "/api/repair/status": api_repair_status,
    "/api/repair/toggle": api_repair_toggle,
}


def serve(core: Core, host: str, port: int) -> ThreadingHTTPServer:
    """Create the server bound to (host, port) and attach ``core``.
    Call ``server.serve_forever()`` to run it."""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.core = core  # type: ignore[attr-defined]
    return srv
