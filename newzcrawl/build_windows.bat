@echo off
rem ============================================================
rem  NewzCrawl - build the Windows 11 portable app
rem
rem  Prerequisites (one time):
rem      python -m pip install pyinstaller
rem
rem  Run this from the newzcrawl folder. It builds NewzCrawl.exe and
rem  assembles a fully self-contained "dist\NewzCrawl\" folder that
rem  already contains every helper tool (7-Zip, par2cmdline, Unpack) -
rem  so there is NOTHING to collect by hand. Copy that folder anywhere
rem  and double-click NewzCrawl.bat.
rem ============================================================
setlocal
cd /d "%~dp0"

where pyinstaller >nul 2>nul
if %errorlevel% neq 0 (
    echo PyInstaller not found. Installing...
    python -m pip install --upgrade pyinstaller
)

pyinstaller NewzCrawl.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo Build FAILED.
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
