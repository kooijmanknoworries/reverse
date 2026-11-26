# NewzCrawl

A Windows 11 Usenet news client, rebuilt from the original NewsLeecher
(see `docs/REVERSE_ENGINEERING.md` for the full feature mapping). Pure-Python
core behind a **native desktop window** (or the web UI) — no admin rights, no
Python to install. Available as a **portable folder** or a per-user
**installer**.

## What it does

- **NNTP client** — connect/auth (AUTHINFO USER/PASS), `CAPABILITIES`,
  `LIST`, `GROUP`, `XOVER`, `XHDR`, `STAT`, `SELECT`/`SLAVE` parallel
  connections. Interoperability validated against a real NewsXS server.
- **Download queue** — parallel part fetching (thread pool), per-part
  yEnc CRC verification, retry + auto-reconnect, offset-based reassembly
  with byte-coverage **hole detection**, and skip-existing.
- **yEnc 1.0.3** — single- and multi-part encode/decode, per-part
  metadata (`part`/`parts`/size/offsets/`pcrc32`/`crc2`) for reassembly.
- **NZB** — import (parse) and export (create) `.nzb` files.
- **Local header search** — case-insensitive subject search over recent
  headers on the connected server, with `from:` and `groups:` filters.
- **PAR2 repair** — verify/repair incomplete binaries via
  `par2cmdline`, plus an **auto-repair watcher** that periodically scans
  `downloads/` and repairs any incomplete PAR2 sets.
- **Auto extraction** — watches `downloads/` and, when an archive is ready
  (complete), extracts it using the bundled **Unpack** app
  (https://github.com/kooijmanknoworries/unpack). For **password-protected**
  archives it tries every password in a configurable **password list** and
  unpacks on the first match. Archived downloads are extracted as soon as
  they finish.
- **Local web UI** — a single-page dashboard (connection, NZB download,
  group browsing, search, repair, **extract**, settings) served on
  `127.0.0.1` only.
- **Native desktop window** — the packaged app shows the dashboard in its
  own window (pywebview over the built-in **Edge WebView2** runtime), so
  there is no browser tab to open. The window is fully resizable, remembers
  nothing, and the whole UI is the same dashboard you get in a browser.
  `--browser` / `--headless` fall back to the web UI when you need it.
- **`.nzb` file association** — the installer registers `.nzb` files, so
  double-clicking an NZB (or *Open with NewzCrawl*) loads it straight into
  the NZB tab.

## Quick start (development / source)

```bash
python -m newzcrawl.app
```

The browser opens `http://127.0.0.1:8765/`. Edit `settings.json`
(next to the `newzcrawl` folder, or created on first run) to set your
NNTP host/port/credentials, then click **Test & Connect**.

## Windows 11 app (end users — recommended)

**No Python, no installs, no admin required.** The app is built
automatically on GitHub and published in the
[Releases tab](https://github.com/kooijmanknoworries/reverse/releases).
It opens the dashboard in its **own native window** (the built-in Edge
WebView2 runtime) — you never open a browser.

### Option A — Installer (recommended)

1. Download **`NewzCrawl-Setup-<version>.exe`** and run it.
2. It installs to your user's `Programs` folder (no admin needed) and
   creates a **Start-menu** entry and an optional **desktop** shortcut.
3. It registers `.nzb` files, so **double-clicking an NZB opens it in
   NewzCrawl**.
4. Start NewzCrawl from the Start menu.

Settings, downloads and `passwords.txt` are stored in
`%APPDATA%\NewzCrawl` (so they survive upgrades and uninstall).

### Option B — Portable (no install)

1. Download **`NewzCrawl-portable-win-x64.zip`** and unzip it anywhere.
2. Double-click **`NewzCrawl.exe`**.

Everything is bundled in the folder — the Python interpreter is frozen
inside `NewzCrawl.exe` (PyInstaller) and every helper tool ships in
`tools\`:

```
NewzCrawl\
  NewzCrawl.exe          (the app; Python is inside it)
  NewzCrawl.bat          (launcher, passes .nzb / flags through)
  settings.json          (created on first run, next to the exe)
  downloads\             (created automatically, next to the exe)
  passwords.txt          (add encrypted-archive passwords here)
  tools\
    7z\7z.exe            (7-Zip CLI)
    par2\par2.exe        (par2cmdline)
    unpack\              (Unpack - password archive extractor)
```

In the portable flavour, `settings.json`, `downloads\` and `passwords.txt`
live **next to the exe**, so the whole app is one folder you can copy
anywhere (USB stick, another PC).

### Command line

`NewzCrawl.exe` accepts a few flags (also via `NewzCrawl.bat`):

| flag            | meaning                                          |
|-----------------|--------------------------------------------------|
| *(none)*        | open the desktop window (default for the exe)    |
| `--browser`     | open the dashboard in your web browser instead   |
| `--headless`    | run the server only (no window, no browser)      |
| `--port 9000`   | bind the local web UI to a specific port         |
| `path\to.x.nzb`| load this NZB into the NZB tab on startup        |

### Rebuilding from source (developers only)

This requires Python on the build machine (one-time):

```bat
python -m pip install --upgrade pyinstaller pywebview pythonnet
build_windows.bat
```

`build_windows.bat` runs `pyinstaller NewzCrawl.spec` (which now bundles
the desktop-window backend) and assembles the self-contained
`dist\NewzCrawl\` folder described above. To also build the installer,
run Inno Setup on `NewzCrawl.iss`. (GitHub's `Build Windows portable app`
workflow does both automatically on every push to `main`.)

## Tests

```bash
python -m pytest tests/ -q
```

The suite includes an in-process **mock NNTP server**
(`tests/mock_nntp_server.py`) that speaks the real protocol (including
multi-part yEnc 1.0.3 posts), so the download/search/repair paths are
tested end-to-end without a live server.

## Project layout

```
newzcrawl/
  nntp_client.py   NNTP protocol client (pure stdlib)
  yenc.py          yEnc 1.0.3 codec + multi-part metadata
  nzb.py           NZB parse/create
  queue.py         parallel download queue (reassembly, holes, retry)
  search.py        local header search + provider interface
  repair.py        PAR2 verify/repair + auto-repair watcher
  extract.py       auto extraction + Unpack driver + password list
  config.py        settings.json store
  app.py           entry point + wiring
  web/
    server.py      local HTTP server + JSON API
    static/        single-page web UI
NewzCrawl.spec     PyInstaller spec (frozen exe, incl. desktop window)
NewzCrawl.iss      Inno Setup script (installer + .nzb association)
NewzCrawl.bat      Windows launcher
build_windows.bat  one-shot build + self-contained folder assembly
assets/icon.ico    app icon (embedded in the exe + installer)
tools/             bundled 7z + par2cmdline + Unpack (see tools/README.md)
tools/gen_icon.py  regenerates assets/icon.ico (pure stdlib)
tests/             pytest suite (mock server + end-to-end)
docs/REVERSE_ENGINEERING.md   original NewsLeecher analysis + mapping
```

## Feature parity with the original

See `docs/REVERSE_ENGINEERING.md` §5. Notable: the **SuperSearch** cloud
index and **SuperLeech** auto-download are licensed *server-side*
services of the original and are not client code; this build provides a
local header search and a pluggable `SearchProvider` interface instead.
Everything that lives in the client (NNTP, download, yEnc, NZB, PAR2,
group browsing, settings) is rebuilt.
