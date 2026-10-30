@echo off
rem ===============================================================
rem  Piano Velocity Mapper - console launcher (forwarding mode)
rem
rem  The desktop shortcut is the normal way to use this tool. This
rem  script is the fallback: it shows the raw log in a console and
rem  passes every argument through (e.g. --monitor, --verbose).
rem
rem  ASCII-only on purpose: non-ASCII text in a .bat depends on the
rem  console codepage and can break parsing on other machines.
rem ===============================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run
if exist ".venv\Scripts\pythonw.exe" if not exist ".venv\Scripts\python.exe" goto :venv_broken

echo No virtual environment yet - running setup.cmd first...
echo.
call "%~dp0setup.cmd"
if errorlevel 1 exit /b 1
if exist ".venv\Scripts\python.exe" goto :run

:venv_broken
echo.
echo The virtual environment is broken (its Python was probably
echo uninstalled). Running setup.cmd to rebuild it...
echo.
call "%~dp0setup.cmd"
if errorlevel 1 exit /b 1

:run
echo.
echo Starting piano velocity mapper (Ctrl+C to stop)...
echo.
".venv\Scripts\python.exe" -u velocity_mapper.py %*
echo.
pause
