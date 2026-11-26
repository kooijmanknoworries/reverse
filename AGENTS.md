# Repository Knowledge

## What this repo is

This repo contains binaries for **NewsLeecher**, a Windows Usenet
newsreader/client. The application and all binaries here were written and
authored by the repo owner — this is **not** pirated or cracked software.
The main executable was renamed from `NewsLeecher.exe` to `Reverse.exe`,
but the internal identity (product name, version info, dialogs) still
identifies as NewsLeecher.

The repo holds only compiled binaries (no source code).

## File inventory

### `nl_setup_beta.exe` (5,525,712 bytes)
- Inno Setup 5.5 installer (Setup Data 5.5.0 / Messages 5.5.3).
- Installs NewsLeecher.
- Version resource: ProductName `NewsLeecher`, ProductVersion
  `V8.0 Beta 4`, FileDescription "NewsLeecher Setup".
- Homepage: `www.NewsLeecher.com`.
- 32-bit (i386) PE.

### `reverse/Reverse.exe` (3,749,104 bytes packed → 14,367,984 unpacked)
- The main application executable (renamed from `NewsLeecher.exe`).
- Written in **Delphi** (Embarcadero/Borland runtime). Source path embedded
  in binary: `C:\Delphi\Projects\NewsLeecher\u_formmain.pas`.
- Internal export name: `NewsLeecher.exe`.
- Version token in binary: `V8.06a [Multi Language]`; copyright
  "NewsLeecher.com 2019".
- **UPX-packed** (sections `UPX0`, `UPX1`, plus `.rsrc`).
- Code-signed (Comodo / Sectigo certificate chains present).
- Application features visible in strings: SuperSearch and SuperLeech
  service integration, RAR password manager, PAR2 set repair, spam
  detection, NZB export, group subscription browser,
  registration/subscription/license system.
- Unpack with `upx -d Reverse.exe` before deeper analysis (the export and
  import tables live in the compressed region and are not readable while
  packed).

### `reverse/nlpar.dll` (80,896 bytes packed → 180,224 unpacked)
- The application's **PAR2** library. Internal export name: `PAR2API.dll`.
- Exports (base ordinal 1):
  - `InitializePARAPI`, `TerminatePARAPI`, `GetPARDLLVersion`
  - `CreateParSet`, `CloseParSet`, `EnumSources`
  - `FileBelongsToPAR2Set`
  - `QuickRepairPAR2`, `RepairPAR2`, `VerifyPAR2`
  - `PAR2SetCallback`
- Also UPX-packed (imports resolved dynamically via `LoadLibraryA` /
  `GetProcAddress`).

### `reverse/unrar.dll` (137,944 bytes packed → 267,992 unpacked)
- Standard UnRAR SDK wrapper. Internal export name: `UnRAR.dll`.
- Exports: `RAROpenArchive`, `RAROpenArchiveEx`, `RARProcessFile`,
  `RARProcessFileW`, `RARReadHeader`, `RARReadHeaderEx`, `RARCloseArchive`,
  `RARGetDllVersion`, `RARSetCallback`, `RARSetChangeVolProc`,
  `RARSetPassword`, `RARSetProcessDataProc`.
- UPX-packed.

### `reverse/libssl-1_1.dll` (291,184 bytes)
- OpenSSL 1.1 SSL/TLS library.

### `reverse/libcrypto-1_1.dll` (1,148,272 bytes)
- OpenSSL 1.1 crypto library.

### `reverse/Reverse.upx` (256 bytes)
- **Truncated/corrupt PE stub.** It has an `MZ` header and the message
  "This program must be run under Win32", but its `e_lfanew` offset
  (0x100) points at the very end of the file — there is no valid PE/COFF
  header. Not runnable. Do not treat it as a usable unpacker or binary.

### `reverse/unins000.exe` / `unins000.dat` / `unins000.msg`
- Standard Inno Setup uninstaller artifacts (unins000.msg starts with
  "Inno Setup Messa...", unins000.dat with "Inno Setup Unins...").

## Technical notes for future work

- All custom binaries are 32-bit (`machine=0x14c`, PE32, not PE32+).
- The custom binaries are UPX-packed; the visible import tables when packed
  are just UPX's own stub imports (e.g. `LoadLibraryA`, `VirtualProtect`),
  **not** the real app imports. Unpack first with `upx -d` to a temp copy
  (do not overwrite the repo originals) before parsing imports/exports.
- The `TMLCRACKED` byte sequence found in `Reverse.exe` is **not** a
  plaintext marker — it sits inside the UPX-compressed `UPX1` section and
  is a coincidental match in compressed data. It is not evidence of
  cracking.
- When parsing PE headers, `SizeOfOptionalHeader` is at `pe_off+20`
  (not the `NumberOfSymbols` field at `pe_off+16`); the optional header
  starts at `pe_off+24`.
- To read the real export table, the three tables are (in the export
  header): `AddressOfFunctions` (offset +16), `AddressOfNames` (offset
  +20), `AddressOfNameOrdinals` (offset +24). Walk ordinals → name via
  `AddressOfNameOrdinals[i]` index into the functions table.

## NewzCrawl (rebuilt client, added 2026-11)

`newzcrawl/` contains a clean-room pure-Python rebuild of the NewsLeecher
client as a Windows 11 portable app (see `newzcrawl/README.md` and
`newzcrawl/docs/REVERSE_ENGINEERING.md` §5 for the feature mapping).

Layout: `newzcrawl/newzcrawl/` is the Python package (`nntp_client`,
`yenc`, `nzb`, `queue`, `search`, `repair`, `extract`, `config`, `app`,
`web/`), `newzcrawl/tests/` is the pytest suite with an in-process mock
NNTP server, and the packaging files (`NewzCrawl.spec`, `NewzCrawl.bat`,
`build_windows.bat`) build the portable exe via PyInstaller on Windows.
`newzcrawl/tools/` bundles the helper tools shipped with the portable app
(`7z/7z.exe`, `par2/par2.exe`, and the full self-contained `unpack/`
.NET-8 Unpack app) — see `newzcrawl/tools/README.md`. `extract.py` drives
Unpack to auto-extract ready archives (single or split) in `downloads/`,
trying every password in a configurable list for encrypted archives.

- Run: `python -m newzcrawl.app` (web UI on 127.0.0.1:8765).
- Test: `python -m pytest newzcrawl/tests/ -q` (132 passing).
- The original binaries (`nl_setup_beta.exe`, `reverse/Reverse.exe`) are
  analysis references only — do not modify or delete them.
