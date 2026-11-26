# NewzCrawl

A Windows 11 **portable** Usenet news client, rebuilt from the original
NewsLeecher (see `docs/REVERSE_ENGINEERING.md` for the full feature
mapping). Pure-Python core with a local web UI — no admin rights, no
install, runs from a single folder.

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

## Quick start (development / source)

```bash
python -m newzcrawl.app
```

The browser opens `http://127.0.0.1:8765/`. Edit `settings.json`
(next to the `newzcrawl` folder, or created on first run) to set your
NNTP host/port/credentials, then click **Test & Connect**.

## Portable Windows 11 app (end users — recommended)

**No Python, no installs, no admin required.** The app is built
automatically on GitHub and published as a ready-to-run zip:

1. Download **`NewzCrawl-portable-win-x64.zip`** from the
   [Releases tab](https://github.com/kooijmanknoworries/reverse/releases).
2. Unzip it anywhere.
3. Double-click **`NewzCrawl.exe`**.

The Python interpreter is frozen inside `NewzCrawl.exe` (PyInstaller),
and every helper tool ships in `tools\` — nothing to collect:

```
NewzCrawl\
  NewzCrawl.exe          (the app; Python is inside it)
  NewzCrawl.bat          (launcher)
  settings.json          (created on first run)
  downloads\             (created automatically)
  tools\
    7z\7z.exe            (7-Zip CLI)
    par2\par2.exe        (par2cmdline)
    unpack\              (Unpack - password archive extractor)
```

Edit `passwords.txt` (next to the exe, one password per line) to add
the passwords of your encrypted archives. Everything is local and
portable — copy the folder anywhere.

### Rebuilding from source (developers only)

This requires Python on the build machine (one-time):

```bat
python -m pip install --upgrade pyinstaller
build_windows.bat
```

`build_windows.bat` runs `pyinstaller NewzCrawl.spec` and assembles the
self-contained `dist\NewzCrawl\` folder described above. (GitHub's
`Build Windows portable app` workflow does this automatically on every
push to `main`.)

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
NewzCrawl.spec     PyInstaller spec (portable exe)
NewzCrawl.bat      Windows launcher
build_windows.bat  one-shot build + self-contained folder assembly
tools/             bundled 7z + par2cmdline + Unpack (see tools/README.md)
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
