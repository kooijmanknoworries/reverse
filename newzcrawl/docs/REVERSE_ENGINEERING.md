# NewsLeecher V8.06a — Reverse-Engineering Notes

These notes document the functional specification of the original
`NewsLeecher` application (shipped in this repo as `reverse/Reverse.exe`
and `nl_setup_beta.exe`), extracted from the compiled binaries by
unpacking (UPX) and analyzing PE structures, import/export tables, and
embedded strings. They serve as the feature baseline for **NewzCrawl**.

Method: `upx -d` on temp copies → PE header/section/import/export
parsing → ASCII/UTF-16 string extraction → cross-referencing with NNTP
(RFC 5538/4180), NZB and yEnc (yEnc v1.0.3) standards. A full native
disassembly (via REA/Ghidra) was not required for the functional spec;
it remains available as a follow-up for deeper analysis.

## 1. Application identity

| Item | Value |
|---|---|
| Internal module name | `NewsLeecher.exe` (renamed to `Reverse.exe` on disk) |
| Version token | `V8.06a [Multi Language]` |
| Copyright | `NewsLeecher.com 2019` |
| Language | Delphi (Embarcadero/Borland runtime) |
| Source path in binary | `C:\Delphi\Projects\NewsLeecher\u_formmain.pas` |
| Architecture | 32-bit Windows (i386), UPX-packed |
| Installer | Inno Setup 5.5 (`nl_setup_beta.exe`, "V8.0 Beta 4") |
| Bundled libs | `nlpar.dll` (PAR2, exports `PAR2API.dll`), `unrar.dll`
  (UnRAR SDK), OpenSSL 1.1 (`libssl-1_1.dll`/`libcrypto-1_1.dll`) |

## 2. Feature inventory (from UI/protocol strings)

Core Usenet client features confirmed present in the original:

1. **NNTP connection manager**
   - Account/server list; username+password (passwords lower-case enforced:
     "Usenet access password must not contain any uppercase letters")
   - Automatic (re)connect with retry delay (`connection_retry_delay`,
     default 5 min) and inactivity logoff
     (`connection_logoff_after_inactivity`, default 10 min)
   - Hanging-connection reset (`connection_reset_hanging`, 60 s)
   - Socks firewall/proxy support (`socks_firewall_*` settings)
   - Bandwidth throttling ("Max Download" speed limiter, tray speed bar)
2. **Group subscription & browsing**
   - Group subscriptions, manual "Add Group", group cleaning
   - Parallel group header updates (`group_browsing_max_simultaneous_updates`,
     default 4)
   - Custom header fetch windows (days / header count)
   - Article color states: new, downloaded, incomplete, paused, highlight
     (`article_color_*`)
3. **Download queue (NNTP)**
   - Multi-threaded article decode (`threads_article_decoder`)
   - Parallel downloads, part ordering, retry on failure
   - yEnc decode with CRC checking (`download_crc_checking_yenc`)
   - Skip existing files (`download_skip_existing_files`)
   - Uppercase filename on error (`download_uppercase_filename_on_error`)
   - Attachment-reference retention (`download_attachment_references`)
   - Download tracking window in days (`download_tracking_days`, 90)
   - XFeat compression support (`article_download_allow_xfeat_compression`)
4. **NZB**
   - NZB import (file + folder, password-protected NZBs), auto-import
     from a watched folder (`NZBAutoImport*`), NZB export, NZB.ZIP
   - Per-NZB CRC options, execute-on-import
5. **Repair & Extract**
   - PAR2 verify/repair via `nlpar.dll` (PAR2API: `InitializePARAPI`,
     `VerifyPAR2`, `RepairPAR2`, `QuickRepairPAR2`, `CreateParSet`,
     `FileBelongsToPAR2Set`, `EnumSources`, callback)
   - RAR/ZIP/7z unpacking (`unrar.dll`), RAR Password Manager with
     automatic password checking against stored list
   - "Potential Spam Detected" handling for passworded RAR files
   - Auto-cleanup of temp folder (`Temp\!NewsLeecher\`)
6. **Search**
   - Local header search with filter dropdown history
     (`search_filter_dropdown_list_entries`, `search_filter_max_pixel_width`)
   - Crosspost filter (ignore/pause on keyword match)
   - Server-side "SuperSearch" service integration (newsleecher.com)
     with bookmarks
   - "SuperLeech" service (automatic wanted-article downloading)
7. **Posting**
   - Post articles, optional footer (`posting_no_footer`),
     `X-Newsreader: NewsLeecher` client id
8. **GUI**
   - Tabs-in-titlebar, tray icon with balloon tips + speed bar,
     custom scaling (`gui_custom_scaling` 75–200%), themes,
     listview fonts/colors, launch minimized / splash screen,
     startup password (`paranoia_launch_password`),
     "Trial Limit Reached" + registration/subscription system
     ("Members Area" credentials, license registration data)
9. **Housekeeping**
   - Automatic backups (`backup_enabled`, `backup_interval_in_minutes`)
   - Article cache garbage collection
     (`article_cache_max_garbage_percent`, default 50)
   - Logging to disk with detail levels (`logging_*`)
   - Config conversion across versions (V7→V8 settings migration)

## 3. Configuration system

Original stores settings in a keyed table (INI/registry style), snake_case
keys with type/min/max/default/description metadata. 66 keys were recovered;
notable ones (key | type | default):

```
always_minimize_to_system_tray      | BOOL
launch_minimized                    | BOOL
launch_show_splashscreen            | BOOL
backup_enabled                      | BOOL
backup_interval_in_minutes          | INT  | 15
connection_retry_delay              | INT  | 5      (minutes)
connection_logoff_after_inactivity  | INT  | 10     (minutes)
connection_reset_hanging            | INT  | 60     (seconds)
group_browsing_max_simultaneous_updates | INT | 4
threads_article_decoder             | INT  | 0..4
download_crc_checking_yenc          | BOOL
download_skip_existing_files        | BOOL
download_uppercase_filename_on_error| BOOL
download_attachment_references      | INT  | 1000
download_tracking_days              | INT  | 90
article_cache_max_garbage_percent   | INT  | 50
socks_firewall_activated            | BOOL
socks_firewall_address              | STR
socks_firewall_port                 | STR
socks_firewall_username             | STR
search_filter_dropdown_list_entries | INT  | 15
gui_custom_scaling                  | INT  | 100
... (66 total; see string dump for full list)
```

NewzCrawl mirrors this convention: `settings.json` with the same
snake_case key names where applicable, so original configuration habits
and documentation remain meaningful.

## 4. Protocol details

- NNTP per RFC 5538 (NNTP), AUTHINFO USER/PASS (RFC 4643), CAPABILITIES,
  LIST (OVER/GROUP/NEWSGROUPS), XOVER, XHDR, XATTRIB, SELECT/SLAVE for
  parallel connections.
- yEnc v1.0.3: `=y`/`=y line=N` headers and `=ybegin part=N ...` /
  `=ypart begin= end=` / `=yend pcrc32= [crc2=]` multi-part framing,
  line-based base85. `=yend crc2=` covers the whole file (final part);
  `pcrc32` covers a single part. `newzcrawl/yenc.py` decodes both and
  exposes the per-part metadata (`parse_part_info`) the queue uses for
  offset reassembly, plus `encode_multipart` for generating posts.
- NZB XML: `<nzb><file><binaries>/<headers>` with base64 yEnc parts.
- PAR2: set-based redundancy (verify/repair), standard par2cmdline
  compatible layout (`.par2` + `.par2` files per set).
- HTTP(S) for server news pages / service integrations (OpenSSL 1.1).

## 5. Mapping to NewzCrawl

| Original feature | NewzCrawl implementation |
|---|---|
| NNTP client | `newzcrawl/nntp_client.py` (pure stdlib) |
| Download queue | `newzcrawl/queue.py` (thread pool, retries, throttle) |
| NZB import/export | `newzcrawl/nzb.py` |
| yEnc | `newzcrawl/yenc.py` |
| PAR2 repair | `tools/par2` (par2cmdline Windows build) |
| Unpack | `tools/7z` (7-Zip Windows build) + RAR support |
| Group browsing | web UI + `newzcrawl/groups.py` |
| Posting | `newzcrawl/nntp_client.py` (POST) |
| SuperSearch/SuperLeech | **not ported** (proprietary service,
  requires NewsLeecher account API); documented as extension point |
| Settings table | `settings.json` (same key names where possible) |
| Delphi GUI | local web UI on 127.0.0.1 (portable, no admin) |
