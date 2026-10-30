# Piano Velocity Remapper · Source build

Full source, for people who want to read it, change it, or build it themselves.

## Quick start

1. Install Python **3.10 – 3.13** (tick *Add Python to PATH*; avoid 3.14 —
   pygame has no 3.14 wheel yet)
2. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) and
   create a port (the app can start loopMIDI for you once it's installed)
3. Double-click **`setup.cmd`** — run it once. It creates the virtualenv, installs
   the dependencies and puts a shortcut on your desktop
4. Double-click the desktop shortcut to start. The **Piano Velocity Mapper**
   shortcut opens the GUI with no console window

`setup.cmd` also prints an environment check at the end: the MIDI ports it found
and whether a virtual output port is available.

## Run it without the shortcut

```powershell
.venv\Scripts\python.exe app.py          # GUI
.venv\Scripts\python.exe start_mapper.bat # headless forwarding + web console
.venv\Scripts\python.exe calibrate.bat    # calibration: prints raw -> mapped
```

## Build the standalone exe

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python build_exe.py
```

The result is `dist/PianoVelocityMapper.exe` (≈18 MB, single file, no install).

## What's in here

| File | What it does |
| --- | --- |
| `app.py` | Entry point |
| `gui.py` | The GUI (hand-drawn with tkinter) |
| `tray.py` | System tray icon (pure ctypes, no third-party dependency) |
| `engine.py` | Background MIDI thread |
| `velocity_mapper.py` | Core mapping logic + port lookup |
| `midi_ports.py` | Detects virtual ports, finds loopMIDI, starts it |
| `i18n.py` | Interface language (Chinese / English) |
| `paths.py` | Separates read-only resources from writable data (needed when packaging) |
| `aa.py` | Anti-aliased drawing |
| `webui.py` / `webui.html` | Optional web console |
| `check_ports.py` | Environment check, run at the end of `setup.cmd` |
| `build_exe.py` | Build the standalone exe |
| `make_release.ps1` | Assemble both distribution archives |

More detail: [`README.md`](README.md) (Chinese) · [`README_EN.md`](README_EN.md)
(English) · [`docs/DETAILS_EN.md`](docs/DETAILS_EN.md) (detailed guide).
