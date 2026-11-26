"""Configuration store.

NewzCrawl persists user settings in a single ``settings.json`` inside the
portable folder. The schema is a flat ``key -> value`` table whose keys
mirror the original NewsLeecher snake_case setting names where a
meaningful equivalent exists (see docs/REVERSE_ENGINEERING.md §3), so a
user's existing habits and any third-party notes remain applicable.

Only stdlib is used so the module works in the embedded-portable build
with no install step.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any

#: Keys and their (type, default, description). Type is one of "bool",
#: "int", "str". This is the single source of truth for valid settings.
DEFAULTS: dict[str, tuple[str, Any, str]] = {
    # --- connection ---
    "host": ("str", "news.example.org", "NNTP server host"),
    "port": ("int", 119, "NNTP server port"),
    "username": ("str", "", "NNTP username"),
    "password": ("str", "", "NNTP password (stored in portable settings.json)"),
    "connection_retry_delay": ("int", 5, "Reconnect retry delay (minutes)"),
    "connection_reset_hanging": ("int", 60, "Reset a hanging connection after (s)"),
    # --- downloads ---
    "threads_article_decoder": ("int", 2, "Parallel download threads (1-16)"),
    "download_crc_checking_yenc": ("bool", True, "Verify yEnc part CRC on download"),
    "download_skip_existing_files": ("bool", True, "Skip parts whose file is already complete"),
    "download_auto_repair": ("bool", False, "Watch downloads and PAR2-repair incomplete binaries"),
    "download_repair_scan_interval": ("int", 60, "Auto-repair scan interval (seconds)"),
    "download_tracking_days": ("int", 90, "Keep article records for (days)"),
    # --- groups ---
    "group_browsing_max_simultaneous_updates": ("int", 4, "Parallel group header updates"),
    # --- UI / behaviour ---
    "always_minimize_to_system_tray": ("bool", False, "Start minimized to tray"),
    "launch_minimized": ("bool", False, "Start minimized"),
    "ui_port": ("int", 8765, "Local web UI port (127.0.0.1 only)"),
    "ui_host": ("str", "127.0.0.1", "Bind address for the local web UI"),
    # --- external tools ---
    "par2cmdline_path": ("str", "tools/par2/par2cmdline-x64.exe",
                         "Path to par2cmdline (relative to app or absolute)"),
    "sevenzip_path": ("str", "tools/7z/7z.exe",
                      "Path to the 7-Zip binary (relative to app or absolute)"),
}


@dataclass
class Config:
    """In-memory settings with load/save to a JSON file.

    Unknown keys read from disk are preserved (forward compatibility),
    but only ``DEFAULTS`` keys are exposed/validated through the API.
    """

    path: str
    data: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        # seed with defaults, then overlay any previously stored values
        merged: dict[str, Any] = {}
        for key, (typ, default, _desc) in DEFAULTS.items():
            merged[key] = default
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    # keep unknown keys too (forward compat)
                    for k, v in loaded.items():
                        merged[k] = v
                    for key, (typ, _d, _desc) in DEFAULTS.items():
                        if key in loaded:
                            merged[key] = _coerce(loaded[key], typ)
            except (OSError, ValueError):
                # corrupt or unreadable file: start from defaults
                pass
        self.data = merged

    # -- accessors -----------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self.data:
                return self.data[key]
            if key in DEFAULTS:
                return DEFAULTS[key][1]
            return default

    def set(self, key: str, value: Any) -> bool:
        """Set a value, coercing to the declared type. Returns False for
        unknown keys or values that cannot be coerced."""
        with self._lock:
            if key not in DEFAULTS:
                return False
            typ = DEFAULTS[key][0]
            try:
                coerced = _coerce(value, typ)
            except (TypeError, ValueError):
                return False
            self.data[key] = coerced
            return True

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.data)

    def schema(self) -> list[dict[str, Any]]:
        """Machine-readable schema for the settings UI."""
        out = []
        for key, (typ, default, desc) in DEFAULTS.items():
            out.append({"key": key, "type": typ, "default": default,
                        "description": desc, "value": self.get(key)})
        return out

    def save(self) -> None:
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
                f.write("\n")


def _coerce(value: Any, typ: str) -> Any:
    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ == "int":
        return int(value)
    if typ == "str":
        return str(value)
    return value


def default_settings_path() -> str:
    """settings.json next to the app (portable folder layout).

    In a frozen (PyInstaller) build ``__file__`` lives inside the
    extraction dir, so the portable root is instead the executable's
    directory — that keeps settings.json editable and persistent.
    """
    import sys
    if getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.abspath(sys.executable))
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "settings.json")
