@echo off
rem ============================================================
rem  NewzCrawl launcher (Windows 11, no admin required)
rem
rem  - If NewzCrawl.exe (frozen PyInstaller build) exists next to
rem    this script, run it. The exe opens the dashboard in a native
rem    window (Edge WebView2) - no browser needed.
rem  - Otherwise fall back to running the Python package directly
rem    with the system/embedded Python on PATH (browser mode).
rem
rem  Any extra arguments are passed through, e.g.
rem      NewzCrawl.bat  --headless
rem      NewzCrawl.bat  "C:\news\some.nzb"
rem
rem  Portable: settings.json + downloads/ live next to this file.
rem  Installed: they live in %APPDATA%\NewzCrawl.
rem ============================================================
setlocal
cd /d "%~dp0"

if exist "NewzCrawl.exe" (
    start "" "NewzCrawl.exe" %*
    exit /b 0
)

rem Fallback: run from source
where python >nul 2>nul
if %errorlevel%==0 (
    python -m newzcrawl %*
    exit /b %errorlevel%
)

echo NewzCrawl.exe not found and Python is not on PATH.
echo Build the portable exe first:  pyinstaller NewzCrawl.spec
pause
endlocal
