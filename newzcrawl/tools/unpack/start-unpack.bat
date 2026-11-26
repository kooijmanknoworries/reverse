@echo off
rem Starts the Unpack GUI from this folder, no matter where you double-click it from.
pushd "%~dp0"
start "" "unpack.exe"
popd
