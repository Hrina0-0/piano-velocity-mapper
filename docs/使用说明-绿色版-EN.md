# Piano Velocity Remapper · Standalone build

Unzip and **double-click `PianoVelocityMapper.exe`** — that's it. No Python, no
installer.

---

## One thing to do first: a virtual MIDI port

This tool forwards the remapped velocity to your DAW through a **virtual MIDI
port**. Hardware MIDI ports are exclusive, so while the tool holds the
keyboard's input your DAW has to listen to a virtual one instead.

**Already installed loopMIDI?** Nothing to do — the tool finds it and **starts it
for you**.

**Don't have it yet?** Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
(free for personal use):

1. Download and install it from the official site
2. Open loopMIDI, right-click its tray icon → *Add Port*, any name you like
3. Leave it running with the port active

Then start this tool. If it can't find a virtual port it shows a setup guide
with a link to the download page.

---

## Wiring it into your DAW

Pick the **same** port for this tool's *Output (virtual port)* and for your DAW's
MIDI input.

* **FL Studio:** `Options > MIDI settings` (F10) → tick the virtual port
* **Cubase:** `Studio > Studio Setup > MIDI Ports` → tick it → and pick the same
  one as this tool's output

> ⚠️ Disable the keyboard's **direct input** in your DAW, or every note fires
> twice.

---

## Good to know

* **Closing the window** asks whether to quit. *Keep running in background* hides
  it to the system tray and forwarding continues (the tray icon is bottom-right,
  possibly behind the `^` overflow arrow). *Quit completely* ends the process.
* The line under the **Ports** card tells you whether you're on a virtual port
  (green = good, orange = pick another one).
* **Language**: the `EN` / `中` button in the top-right switches the interface.
  Chinese by default.
* Settings live in **`config.json` next to the exe** — copy that file to move
  your settings to another machine.
* First launch unpacks the bundled dependencies to a temp folder, so it takes a
  second or two. That's normal.
* The program itself contains **no third-party software**; loopMIDI is yours to
  install. pygame (LGPL) and mido (MIT) are bundled — see
  `third_party_licenses.txt`.
