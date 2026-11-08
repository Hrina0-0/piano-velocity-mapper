# Detailed guide · Piano Velocity Remapper

The [README](../README.md) covers the basics. This page has the full reference:
every option, every knob, and the wiring instructions for specific DAWs.

---

## 1. Requirements

* Windows 10 or 11
* Python **3.10 – 3.13** (only if you use the source build; 3.14 has no pygame
  wheel yet)
* A MIDI keyboard with a USB (or Bluetooth MIDI) connection
* A **virtual MIDI port** — see the next section

---

## 2. Installing loopMIDI

Hardware MIDI ports are exclusive. While this tool holds the keyboard's input,
your DAW can only listen to a *virtual* port. On Windows the usual choice is
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) by Tobias
Erichsen (free for personal use):

1. Download and install it from the official site
2. Open loopMIDI, right-click its tray icon → *Add Port*, any name you like
3. Leave it running with the port active

If your port is not called `loopMIDI Port`, set the actual name in
`config.json` → `output_port` (or pick it in the window's *Ports* card).

### You don't have to start loopMIDI yourself

The tool does it for you (see `midi_ports.py`):

1. It checks whether the output port you configured still exists
2. If not, it looks for another virtual port on the system (Bome, VMP, MIDI Yoke…)
3. If there's still none but loopMIDI *is* installed, it **starts loopMIDI for
   you** and waits for the port to appear
4. Only if all of that fails does it show a setup guide

---

## 3. Installing and first launch

### Standalone exe (recommended for everyday use)

| File | What it does |
| --- | --- |
| `setup.cmd` | Run **once**: builds a virtualenv, installs the dependencies, creates a desktop shortcut |
| `PianoVelocityMapper.exe` | The app itself — no console window, everything in the GUI |
| `创建桌面快捷方式.cmd` | Creates the desktop shortcut (if you skipped `setup.cmd`) |

### Source

| File | What it does |
| --- | --- |
| `setup.cmd` | One-time setup: venv + dependencies + desktop shortcut |
| `app.py` | The GUI (this is what the shortcut points at) |
| `start_mapper.bat` | Headless forwarding mode + optional web console |
| `calibrate.bat` | Calibration mode — prints raw → mapped without forwarding |

Command-line options of `velocity_mapper.py`:

| Option | Meaning |
| --- | --- |
| `--config PATH` | Path to the config file (default: `config.json` next to the app) |
| `--monitor` | Calibration mode: print `raw -> mapped`, don't forward |
| `--list` | List the MIDI input/output ports and exit |
| `--verbose` | Print the velocity mapping for every note while forwarding |
| `--no-web` | Don't start the web console |

---

## 4. What the window does

### Velocity mapping

* **Max velocity** — the value your keyboard actually reaches at full force.
  Drag the slider; the app suggests a value after you've played a few notes
  (*Use suggested*).
* **Velocity curve** — *Linear* (1–max stretched evenly to 1–127, most natural),
  *Soft* (a light touch plays lighter, wide dynamic range) or *Punchy* (even the
  lightest touch sounds; detail lives in the lower range).
* **Live velocity** — shows the last note: raw → mapped, plus the recent range.

### Closing the window: quit or keep running?

Clicking ✕ (or pressing Esc) opens a small dialog:

| Choice | What happens |
| --- | --- |
| **Yes · Quit completely** | The process ends, the MIDI port is released |
| **No · Keep running in background** | The window hides, the tray icon stays, forwarding continues |
| **Don't ask again** | Remembers your choice (stored in `ui_prefs.json`) |

To undo the "remember" setting: right-click the tray icon → *Always ask when
closing*.

The tray icon (bottom-right of the taskbar, possibly behind the `^` overflow
arrow) has its own menu:

| Item | What it does |
| --- | --- |
| Show main window | Bring the window back to the front |
| Minimize to background | Hide it again |
| Always ask when closing | Restore the close prompt |
| Minimize to tray | Toggle whether the *minimize* button hides to the tray |
| Quit completely | End the process |

Clicking the tray icon with the left mouse button also brings the window back.

If the tool is already running in the background and you start it **again**, the
second launch doesn't open a second instance — it wakes the first one and shows
its window.

### Interface language

Click the `EN` / `中` button in the top-right corner to switch languages. The
choice is remembered in `ui_prefs.json`. Switching only rebuilds the interface —
the MIDI engine keeps running, so nothing breaks mid-performance.

### The web console (optional)

`start_mapper.bat` also opens a small web UI at `http://127.0.0.1:8765`, handy for
remote/second-screen tuning. The desktop window is the main interface; you
normally don't need it.

---

## 5. Configuration (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `input_port_substring` | *(auto-detected)* | Which MIDI input to read from (substring match) |
| `output_port` | `loopMIDI Port` | Which MIDI output to write to |
| `max_input_velocity` | `100` | The keyboard's real maximum velocity (1–127) |
| `curve_exponent` | `1.0` | 1.0 = linear, <1 = softer, >1 = punchier |
| `log_interval_sec` | `2.0` | How often the periodic summary is logged |

The mapping formula is `v' = 1 + 126 * ((v - 1) / (max_in - 1)) ^ exp`,
rounded and clamped to 1–127.

`ui_prefs.json` holds interface preferences (close behaviour, minimize-to-tray,
interface language). It's per-machine and not part of the distribution.

---

## 6. DAW wiring

### FL Studio

1. `Options > MIDI settings` (F10)
2. Tick the virtual port
3. Set this tool's *Output* to the same port

### Cubase

1. `Studio > Studio Setup > MIDI Ports`
2. Tick the virtual port (and untick the keyboard's direct input)
3. In the track Inspector, set the track's MIDI **Input** to the virtual port
4. Play — if you still hear double notes, the keyboard's direct input is still
   active on some track

> Don't stack Cubase's MIDI Modifiers on top — the velocity is already mapped.

---

## 7. Day-to-day

* Before playing: plug in the keyboard and make sure the virtual port exists
  (the tool starts loopMIDI for you)
* The window's top-right pill shows `Connected` / `Waiting for device` /
  `Port busy` / `Connection error` / `Stopped`
* If the keyboard is unplugged, the tool waits and reconnects on its own

---

## 8. Verification checklist

- [ ] The window shows `Connected`
- [ ] Playing a note shows the raw and mapped velocity in *Live velocity*
- [ ] The DAW receives the note, and the mapped velocity is higher than the raw one
- [ ] Sustain pedal (CC64), pitch bend and modulation pass through unchanged
- [ ] Unplugging and replugging the keyboard returns the window to `Connected`
- [ ] Starting the tool before or after the DAW both work

---

## 9. Troubleshooting

| Symptom | What to do |
| --- | --- |
| "Output port not found" | loopMIDI isn't installed / running / has no port. Install it, create a port, and the tool picks it up within 2 seconds |
| "Input port not found" | The keyboard isn't connected. Plug it in — the tool retries every 2 seconds |
| "Port busy" | Another program (often your DAW) holds the port. Close the extra tool window, or disable the keyboard's direct input in the DAW |
| "Input and output use the same port" | Pick a different *Output* (the virtual port) for the output and the keyboard's USB port for the input |
| Double notes | The keyboard's direct input is still enabled in your DAW |
| "No usable MIDI backend" | Re-run `setup.cmd`, or check that Python is 3.10–3.13 |
| "Python venv launcher is sorry to say…" | The Python that created `.venv` was uninstalled. Install 3.13 and run `setup.cmd` again |
| The window flashes and disappears | No MIDI backend. Run `.venv\Scripts\python.exe app.py` from a console to see the error |
| The web console doesn't open | Keep the tool window open; the port may have moved — read the address shown in the console |

---

## 10. Uninstalling

Delete the desktop shortcut and the program folder. To remove the MIDI port as
well, uninstall loopMIDI separately.

---

## 11. Building a distribution

```powershell
powershell -ExecutionPolicy Bypass -File make_release.ps1 -Version 1.3.3
```

This produces one archive in `release\`:

| Archive | Contents | What the recipient does |
| --- | --- | --- |
| `PianoVelocityMapper-v1.3.3.zip` | `PianoVelocityMapper.exe` (≈18 MB) + docs | Unzip and double-click — no Python needed |

The archive contains no third-party software: loopMIDI has to be installed from
the official site, and afterwards the tool starts it automatically. The source
code lives in this repository (MIT licence) — clone it or use *Code → Download
ZIP*.

| File | Purpose |
| --- | --- |
| `build_exe.py` | Build the standalone exe with PyInstaller |
| `make_release.ps1` | Assemble the archive |
| `paths.py` | Separates read-only resources from writable data (config must live next to the exe) |
| `check_ports.py` | Environment check, run at the end of `setup.cmd` |

> The standalone build is `--onefile`, so it unpacks its dependencies to a temp
> folder on first launch. The first start is a second or two slower — that's
> normal.
