@echo off
rem ============================================================
rem  NewzCrawl - build the Windows 11 portable app (source build)
rem
rem  NOTE: You normally do NOT need to run this. Ready-to-run builds
rem  (no Python required to run) are published in the GitHub Releases
rem  tab:
rem      https://github.com/kooijmanknoworries/reverse/releases
rem  - NewzCrawl-Setup-<ver>.exe  : installer (start menu, .nzb assoc.)
rem  - NewzCrawl-portable-win-x64.zip : portable folder
rem  Both open the dashboard in a native window (no browser).
rem
rem  This script is only for rebuilding from source. It requires
rem  Python on this machine (one-time):
rem      python -m pip install --upgrade pyinstaller pywebview pythonnet
rem  To also build the installer, run Inno Setup 6 on NewzCrawl.iss.
rem ============================================================
setlocal
cd /d "%~dp0"

rem ---- Python is only needed to BUILD, never to RUN ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo  Python is not installed on this machine.
    echo.
    echo  That is fine for RUNNING the app: NewzCrawl.exe already
    echo  contains its own Python. Do NOT install anything - just
    echo  download the prebuilt portable app instead:
    echo.
    echo     https://github.com/kooijmanknoworries/reverse/releases
    echo
    echo  (NewzCrawl-portable-win-x64.zip - unzip and run)
    echo.
    echo  Only continue with this build if you want to recompile
    echo  from source, in which case install Python 3.11+ first.
    echo ============================================================
    pause
    exit /b 1
)

where pyinstaller >nul 2>nul
if %errorlevel% neq 0 (
    echo PyInstaller/pywebview not found. Installing...
    python -m pip install --upgrade pyinstaller pywebview pythonnet
)

pyinstaller NewzCrawl.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo.
    echo Build FAILED.
    echo If you do not need to recompile, download the ready-made
    echo portable app from the GitHub Releases tab instead.
    pause
    exit /b 1
)

rem ---- assemble the self-contained portable folder ----
set OUT=dist\NewzCrawl
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"
copy /y dist\NewzCrawl.exe "%OUT\" >nul
copy /y NewzCrawl.bat "%OUT\" >nul
xcopy /s /e /i /y tools "%OUT%\tools" >nul
echo.
echo ============================================================
echo  Portable app ready:  %CD%\%OUT%
echo ============================================================
echo  Contents (all bundled, nothing to collect):
echo    NewzCrawl.exe          (the app)
echo    NewzCrawl.bat          (launcher)
echo    tools\7z\7z.exe        (7-Zip)
echo    tools\par2\par2.exe    (par2cmdline)
echo    tools\unpack\          (Unpack - password archive extractor)
echo.
echo  To run: copy the "%OUT%" folder anywhere, then double-click
echo  NewzCrawl.bat. Edit passwords.txt to add archive passwords.
endlocal
