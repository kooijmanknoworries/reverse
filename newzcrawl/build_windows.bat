@echo off
rem ============================================================
rem  NewzCrawl - build the Windows 11 portable exe
rem
rem  Prerequisites (one time):
rem      python -m pip install pyinstaller
rem
rem  Run this from the newzcrawl folder. Output: dist\NewzCrawl.exe
rem  Bundle it with tools\7z and tools\par2 + NewzCrawl.bat.
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

echo.
echo Build OK: dist\NewzCrawl.exe
echo.
echo To make a portable folder, copy into a folder named "NewzCrawl":
echo   NewzCrawl.exe
echo   NewzCrawl.bat
echo   tools\7z\7z.exe            ^(7-Zip command line^)
echo   tools\par2\par2cmdline-x64.exe  ^(par2cmdline^)
echo   settings.json  ^(optional - created on first run^)
endlocal
