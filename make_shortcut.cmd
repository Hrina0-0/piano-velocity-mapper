@echo off
rem ===============================================================
rem  Create a desktop shortcut for PianoVelocityMapper.exe
rem  The real work is in make_shortcut.ps1 - this .cmd stays pure
rem  ASCII, because cmd.exe parses .bat files with the console
rem  code page and would mangle any non-ASCII text in it.
rem ===============================================================
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"

echo.
pause
