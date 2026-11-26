# Bundled external tools

NewzCrawl shells out to two small, well-known command-line tools for
the post-download stages. They are **not** checked into git (large,
Windows binaries); place them here in the portable build:

## `tools/7z/7z.exe`
7-Zip command line (https://www.7-zip.org/a/).
Used to unpack downloaded archives (7z, zip, rar via the 7z engine).

## `tools/par2/par2cmdline-x64.exe`
par2cmdline (https://parchive.sourceforge.io/).
Used to verify and repair incomplete downloads (the auto-repair watcher
drives `par2 v` / `par2 r` on each `.par2` set in `downloads/`).

Both paths are configurable in the app (Settings → `sevenzip_path` /
`par2cmdline_path`), either relative to the app folder (as above) or as
absolute paths. If a tool is missing, the affected feature degrades
gracefully and reports "tool not found" instead of crashing.
