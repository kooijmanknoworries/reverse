@echo off
rem ============================================================
rem  NewzCrawl launcher (Windows 11, no admin required)
rem
rem  - If NewzCrawl.exe (frozen PyInstaller build) exists next to
rem    this script, run it.
rem  - Otherwise fall back to running the Python package directly
rem    with the system/embedded Python on PATH.
rem
rem  Settings are stored in settings.json next to this file
rem  (portable: edit it to configure your NNTP server).
rem ============================================================
setlocal
cd /d "%~dp0"

if exist "NewzCrawl.exe" (
    start "" "NewzCrawl.exe"
    exit /b 0
)

rem Fallback: run from source
where python >nul 2>nul
if %errorlevel%==0 (
    python -m newzcrawl.app
    exit /b %errorlevel%
)

echo NewzCrawl.exe not found and Python is not on PATH.
echo Build the portable exe first:  pyinstaller NewzCrawl.spec
pause
endlocal
