@echo off
rem ===============================================================
rem  Piano Velocity Mapper - one-time setup
rem
rem  Double-click this file ONCE. It installs the dependencies and
rem  creates a desktop shortcut. After that you only ever need the
rem  shortcut: no black console window, everything in one window.
rem
rem  Safe to re-run: a broken environment is detected and rebuilt.
rem
rem  NOTE: this file is deliberately pure ASCII. cmd.exe parses a
rem  .bat with the console codepage, so non-ASCII text here can break
rem  line parsing on some systems.
rem ===============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ===============================================================
echo   Piano Velocity Mapper - Setup
echo ===============================================================
echo.

rem ---------- 0. find a usable Python (3.10 - 3.13) ----------
rem  pygame has no wheel for 3.14 yet, so 3.13 is the ceiling.
set "PY="
call :try_py python
if not defined PY call :try_py python3
if not defined PY call :try_py py -3.13
if not defined PY call :try_py py -3.12
if not defined PY call :try_py py -3.11
if not defined PY call :try_py py -3.10
if not defined PY call :try_py py
for %%D in ("%LOCALAPPDATA%\Programs\Python\Python313" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%LOCALAPPDATA%\Programs\Python\Python310" "%ProgramFiles%\Python313" "%ProgramFiles%\Python312" "%ProgramFiles%\Python311" "%ProgramFiles%\Python310") do if not defined PY call :try_py "%%~D\python.exe"

if not defined PY goto :no_python
for /f "delims=" %%V in ('%PY% -c "import sys; print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%V"
echo [0/5] Using Python %PYVER%  (%PY%)
echo.

rem ---------- 1. check / rebuild the virtual environment ----------
rem  A venv is only a pointer to some Python install. If that Python
rem  is uninstalled, the venv turns into dead files and the shortcut
rem  fails with "Python venv launcher is sorry to say ... did not
rem  find executable". So check the pointer before trusting it.
if exist ".venv\pyvenv.cfg" (
    set "VHOME="
    for /f "tokens=2 delims==" %%H in ('findstr /b /c:"home" ".venv\pyvenv.cfg" 2^>nul') do set "VHOME=%%H"
    set "VBAD="
    if not defined VHOME set "VBAD=1"
    if defined VHOME if not exist "!VHOME!\python.exe" set "VBAD=1"
    if defined VBAD (
        echo [1/5] Existing environment is broken ^(its Python is gone^) - rebuilding...
        rmdir /s /q ".venv" 2>nul
    ) else (
        ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>nul
        if errorlevel 1 (
            echo [1/5] Existing environment uses an unsupported Python - rebuilding...
            rmdir /s /q ".venv" 2>nul
        ) else (
            echo [1/5] Existing environment looks fine.
        )
    )
)

if exist ".venv\pyvenv.cfg" if exist ".venv\Scripts\python.exe" goto :deps
rd /s /q ".venv" >nul 2>nul

echo [1/5] Creating virtual environment...
%PY% -m venv .venv
if errorlevel 1 goto :fail
if not exist ".venv\Scripts\python.exe" goto :fail

rem ---------- 2. dependencies ----------
:deps
echo [2/5] Installing dependencies (mido / pygame)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :no_wheel

rem ---------- 3. desktop shortcut ----------
echo [3/5] Creating desktop shortcut...
".venv\Scripts\python.exe" install_shortcut.py
if errorlevel 1 echo       (shortcut failed - you can still start by double-clicking app.py)

rem ---------- 4. self check ----------
echo [4/5] Checking everything imports...
".venv\Scripts\python.exe" -c "import tkinter, mido, pygame.midi, velocity_mapper, gui, engine" >nul 2>nul
if errorlevel 1 goto :selftest_failed

rem ---------- 5. MIDI port check ----------
echo [5/5] Checking MIDI ports...
".venv\Scripts\python.exe" check_ports.py

echo.
echo ===============================================================
echo   Setup finished.
echo.
echo   A desktop shortcut has been created.
echo   Double-click it to start - no console window.
echo.
echo   Before playing: plug in the piano, then make sure a VIRTUAL MIDI
echo   port exists (loopMIDI is the usual choice - the app can start it
echo   for you if it is installed). Pick that same port as the app's
echo   OUTPUT and as your DAW's MIDI input.
echo ===============================================================
echo.
ping -n 7 127.0.0.1 >nul
exit /b 0

rem ---------------------------------------------------------------
:try_py
rem  probe a candidate interpreter; version must be 3.10 - 3.13
rem  uses %* (not %~1) because a candidate may be "py -3.13"
%* -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0

:no_python
echo.
echo ERROR: No usable Python found (need 3.10 - 3.13).
echo.
echo   * Not installed: download 3.13.x from
echo     https://www.python.org/downloads/ and tick "Add Python to PATH".
echo   * You have Python 3.14: install 3.13 as well. pygame has no
echo     3.14 wheel yet, so dependencies cannot be installed on 3.14.
echo   * Installed but not on PATH: re-run the Python installer and
echo     tick "Add Python to PATH".
echo.
pause
exit /b 1

:no_wheel
echo.
echo ERROR: Dependency install failed. Most likely the Python is too
echo new - pygame has no wheel for 3.14. Install Python 3.13 and run
echo this file again.
echo.
pause
exit /b 1

:selftest_failed
echo.
echo WARNING: Self check failed. Please report the output of:
echo   .venv\Scripts\python.exe -c "import tkinter, mido, pygame.midi, gui"
echo.
pause
exit /b 1

:fail
echo.
echo ERROR: Setup failed. Check your network connection and run this
echo file again.
echo.
pause
exit /b 1