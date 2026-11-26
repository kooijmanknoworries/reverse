# Bundled external tools

NewzCrawl is **fully self-contained**: the helper tools below are checked
into the repo and copied into the portable build, so there is nothing to
collect by hand. They live here in the source tree and under the app's
`tools/` folder at runtime.

## `tools/7z/7z.exe`
7-Zip command line (7-Zip 26.05, x64; https://www.7-zip.org/a/).

## `tools/par2/par2.exe`
par2cmdline (0.4, x86 — runs on Windows 11 x64 via WoW64;
https://sourceforge.net/projects/parchive/).
Used to verify and repair incomplete downloads (the auto-repair watcher
drives `par2 v` / `par2 r` on each `.par2` set in `downloads/`).

## `tools/unpack/`
The **Unpack** app (self-contained .NET 8, Windows 10/11 x64;
https://github.com/kooijmanknoworries/unpack) — a portable archive
password extractor. NewzCrawl drives its CLI:

```
unpack.exe <archive> <password-list.txt>
```

It tries each password in the list with the bundled 7-Zip engine and, on
the first match, extracts to `unpack_<name>/` next to the archive. NewzCrawl
watches `downloads/` and calls this automatically for ready archives (see
`newzcrawl/extract.py`). Add the passwords of your encrypted archives to
`passwords.txt` next to the app (or point `password_list_path` at your own
list).

All tool paths are configurable in the app (Settings → `sevenzip_path`,
`par2cmdline_path`, `unpack_path`), either relative to the app folder (as
above) or as absolute paths. If a tool is missing, the affected feature
degrades gracefully and reports "tool not found" instead of crashing.
