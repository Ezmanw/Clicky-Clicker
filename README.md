# Clicky Clicker

A Wayland-native input remapper and visual macro editor for Linux, built with
GTK 4 and libadwaita.

Remap any key or mouse button, build macros by picking actions from menus rather
than by writing a scripting language, and have them apply across your whole
desktop — including in applications that never see the original keypress.

Think of it as the macro half of something like Corsair iCUE, rewritten for
Linux, working with any hardware, and designed for Wayland rather than bolted
onto X11.

```
Trigger:     Mouse Button 4

Actions:     Press E
             Wait 1 ms
             Release E
             Wait 1 ms
             Click Left Mouse Button
             Wait 20 ms
             Move pointer to X 500, Y 300

Playback:    Repeat while held
Repeat gap:  5 ms
```

Every line of that is built from dropdowns, spin boxes and pickers. There is no
macro syntax to learn.

---

## Contents

- [Features](#features)
- [Supported desktops](#supported-desktops)
- [How it works](#how-it-works)
- [Dependencies](#dependencies)
- [Building and installing](#building-and-installing)
- [First-run setup](#first-run-setup-permissions)
- [Usage](#usage)
- [Macro examples](#macro-examples)
- [Known Wayland limitations](#known-wayland-limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Project layout](#project-layout)
- [Licence](#licence)

---

## Features

**Visual macro editor**
Each step is a row you can expand and edit in place. Add, delete, duplicate,
reorder (by dragging or with arrows), insert between existing steps, disable a
step without deleting it, and test the whole macro from the editor.

**Action types**

| Category | Actions |
| --- | --- |
| Keyboard | Press, Release, Tap (press + hold + release), Key Combination |
| Mouse | Click, Press, Release — including side and extended buttons |
| Pointer | Move to an absolute position, Move by an offset, Click at a position |
| Other | Scroll (vertical and horizontal), Wait (millisecond precision) |

**Playback modes** — run once, repeat a set number of times, repeat forever,
repeat while held, or toggle on and off, with a configurable gap between
repeats that is separate from any `Wait` inside the macro.

**Trigger behaviours** — on press, on release, while held, toggle, or one-shot.
A binding can override the macro's own setting, so the same macro can be held on
one button and toggled on another.

**Input remapping** — map any key or button to any other, including to
combinations (`Caps Lock → Escape`, `Mouse Button 5 → Ctrl+C`), or disable an
input entirely.

**Presets** — every macro is a self-contained JSON file, so a saved macro *is* a
preset. Create, rename, duplicate, delete, import and export. Seven examples are
installed on first run.

**Recording** — capture a sequence of keys and clicks with their real timing,
then edit the result in the normal editor.

**Runs in the background** — a systemd user service applies your mappings when
the window is closed, and can start automatically at login.

**Emergency stop** — <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Esc</kbd> (configurable)
stops every running macro and releases anything held down. It works even when
the window is closed and is never intercepted by a mapping.

**Native and accessible** — standard libadwaita widgets throughout, system icon
theme only, no bundled artwork, no custom theming. Light/dark/system via
`AdwStyleManager`. Full keyboard navigation and accessible names on controls.

---

## Supported desktops

Input is read and injected through the Linux kernel, below the display server,
so this works the same regardless of the compositor.

| Desktop | Status |
| --- | --- |
| GNOME (Wayland) | Supported, primary target |
| COSMIC | Supported, primary target |
| KDE Plasma (Wayland) | Expected to work; not regularly tested |
| Sway, Hyprland, wlroots | Expected to work; not regularly tested |
| X11 | Works, but this is not what it is designed for |

The application requires libadwaita 1.5 or newer (GTK 4.12+), which is what
Ubuntu 24.04, Fedora 40 and their derivatives ship.

---

## How it works

Wayland deliberately prevents an ordinary application from reading the global
input stream or moving the pointer. That is a security property of the protocol,
not a gap waiting for a workaround, and no portal exists for it.

So Clicky Clicker does not try to be a Wayland client that spies on input.
It works one level lower:

```
  physical device                                   your applications
        │                                                   ▲
        ▼                                                   │
  /dev/input/event*  ──►  daemon  ──►  macro engine  ──►  /dev/uinput
     (read events)      (matches       (plays the       (virtual devices
                        bindings)       actions)         the compositor
                                                          treats as real
                                                          hardware)
```

- **Reading** uses `evdev`. This sees every key and button regardless of which
  window has focus, which is what makes global hotkeys possible at all.
- **Injecting** uses `uinput` to create virtual keyboard, mouse and absolute
  pointer devices. The compositor treats them as ordinary hardware, so injected
  input works in every application, including ones that ignore synthetic X11
  events.
- **Suppressing** an input (so a game never sees the original `Mouse Button 4`)
  takes an exclusive grab on the device and re-emits everything *except* the
  suppressed codes through a companion virtual device. Without that
  re-emission, grabbing a mouse to hide one button would also swallow pointer
  motion.

The consequence is that this needs device permissions rather than compositor
cooperation — see [First-run setup](#first-run-setup-permissions).

The interface and the daemon are separate processes talking over a Unix socket
in `$XDG_RUNTIME_DIR`, so mappings keep working with the window closed.

---

## Dependencies

### Runtime

- Python 3.10+
- GTK 4.12+
- libadwaita 1.5+
- PyGObject
- python-evdev 1.4+
- systemd (for login autostart; a `.desktop` autostart fallback is used without it)

### Build

- Meson 0.62+ and Ninja
- `pkg-config`
- GTK 4 and libadwaita development files (for the version check)
- `desktop-file-utils`, `appstream-util`, `glib-compile-schemas` (optional, enable validation tests)

### Debian, Ubuntu, Pop!_OS

```bash
sudo apt install -y meson ninja-build pkg-config \
  libgtk-4-dev libadwaita-1-dev gir1.2-gtk-4.0 gir1.2-adw-1 \
  python3-gi python3-gi-cairo python3-evdev \
  desktop-file-utils appstream-util
```

### Fedora

```bash
sudo dnf install -y meson ninja-build pkgconf-pkg-config \
  gtk4-devel libadwaita-devel python3-gobject python3-evdev \
  desktop-file-utils libappstream-glib
```

### Arch

```bash
sudo pacman -S --needed meson ninja pkgconf gtk4 libadwaita \
  python-gobject python-evdev desktop-file-utils appstream-glib
```

---

## Building and installing

### Release build

```bash
meson setup _build --prefix=/usr --buildtype=release
ninja -C _build
sudo ninja -C _build install
```

### Debug build

```bash
meson setup _build --prefix=/usr --buildtype=debug
ninja -C _build
meson test -C _build --print-errorlogs
```

### Running from a source checkout

No install required, though the window size and colour scheme are not remembered
until the GSettings schema is installed:

```bash
PYTHONPATH=src python3 -m clickyclicker.application.application   # interface
PYTHONPATH=src python3 -m clickyclicker.daemon.daemon -v          # daemon
```

### Build options

| Option | Default | Effect |
| --- | --- | --- |
| `systemduserunitdir` | auto | Where to install the systemd user unit |
| `install-udev-rules` | `false` | Install the `/dev/uinput` udev rule (needs a root, system-wide install) |

### Uninstall

```bash
sudo ninja -C _build uninstall
rm -rf ~/.config/clicky-clicker ~/.local/state/clicky-clicker
```

---

## First-run setup (permissions)

Two kernel devices are involved, and neither is accessible to a normal user by
default. **Open the Status page in the application** — it checks both, tells you
exactly what is wrong, and gives you the command to fix it with a copy button.

### 1. Reading input devices

```bash
sudo usermod -aG input $USER
```

Then **log out and back in** for the new group to take effect. To test without
logging out: `sg input -c 'clicky-clicker-daemon --check'`.

### 2. Writing to `/dev/uinput`

Many systems (recent Pop!_OS and Fedora among them) already grant the logged-in
user access through a logind ACL. Check first:

```bash
getfacl /dev/uinput
```

If your user is not listed, install the bundled rule:

```bash
sudo cp data/99-clicky-clicker-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

If `/dev/uinput` does not exist at all, load the module and make it persistent:

```bash
sudo modprobe uinput
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
```

### 3. Start the service

```bash
systemctl --user enable --now clicky-clicker-daemon.service
```

Or just turn on **Start Automatically at Login** in Preferences, which does the
same thing.

### Verify everything

```bash
clicky-clicker-daemon --check
```

> **A note on what this permission means.** Membership of the `input` group lets
> any program you run read every keystroke you type, including passwords. That
> is inherent to how global hotkeys work on Linux — every tool in this category
> needs it. It is a real trade-off and worth making deliberately.

---

## Usage

### Making your first macro

1. **Macros → New Macro.**
2. **Add Action**, and pick a step. It appears as a row reading like a sentence,
   for example `1. Tap E for 20 ms`.
3. Expand the row to change the key, the timing, or the action type itself.
4. Reorder by dragging, or with the arrows on each row.
5. **Test** in the header runs it once, whatever its playback mode, so a
   "repeat forever" macro is safe to try.

### Choosing how it repeats

On the **Playback** tab, set the mode and the repeat gap. The page shows the
resulting behaviour as a sentence, so you never have to work out how the trigger
and playback settings combine:

> Holding Button 4 (Side) repeats the macro until it is released with 5 ms
> between repeats.

Playback and trigger interact by a single rule: **the trigger decides when a run
starts and stops; the playback mode decides how many passes it makes.** The
`Repeat while held` and `Toggle` playback modes are bounded by the trigger
rather than by a counter, so they pin the trigger setting — the interface greys
it out and says so.

### Assigning it to an input

1. **Mappings → New Mapping.**
2. **Change** next to Trigger, then **Press an Input** and press the key or
   button you want. Or browse the categorised list.
3. Choose **Run a macro** and pick it.
4. **Hide the Original Input** decides whether other applications still see the
   original press. Leave it on for gaming, off if you want the key to keep
   working normally as well.

Macros and mappings are separate, so one macro can be bound to several inputs,
each with its own trigger behaviour.

### Recording

The **record** button in the editor captures keys and clicks with their real
timing. Recording happens **inside the recorder window** — see
[limitations](#known-wayland-limitations). The captured steps land in the normal
editor for trimming and retiming.

### Stopping a runaway macro

Any of these work:

- <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Esc</kbd> — the global emergency stop,
  which works with the window closed and cannot be intercepted by a mapping
- The **Stop** button in the sidebar, visible whenever anything is running
- <kbd>Ctrl</kbd>+<kbd>.</kbd> or <kbd>Esc</kbd> in the window
- `systemctl --user stop clicky-clicker-daemon.service`

A macro that stops for any reason releases every key it was holding, so an
interrupted macro cannot leave a key stuck down.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| <kbd>Ctrl</kbd>+<kbd>N</kbd> | New macro |
| <kbd>Ctrl</kbd>+<kbd>,</kbd> | Preferences |
| <kbd>Ctrl</kbd>+<kbd>.</kbd> / <kbd>Esc</kbd> | Stop all macros |
| <kbd>Ctrl</kbd>+<kbd>?</kbd> | Keyboard shortcuts |
| <kbd>Ctrl</kbd>+<kbd>Q</kbd> | Quit (the daemon keeps running) |

---

## Macro examples

Seven presets are installed on first run:

| Preset | What it does |
| --- | --- |
| **Autoclicker** | Left-clicks repeatedly while the trigger is held |
| **Toggle Autoclicker** | Press once to start clicking, again to stop |
| **Rapid Fire E** | Taps <kbd>E</kbd> as fast as is practical while held |
| **Building Combo** | Tap <kbd>E</kbd>, click, reposition the pointer; repeats while held |
| **Click at Two Points** | Alternates clicks between two fixed screen positions |
| **Copy and Paste** | Sends <kbd>Ctrl</kbd>+<kbd>C</kbd> then <kbd>Ctrl</kbd>+<kbd>V</kbd> |
| **Scroll Down Slowly** | Turns the wheel one detent at a time, toggled on and off |

### The preset file format

Presets are plain JSON — readable, diffable, and easy to share:

```json
{
  "version": 1,
  "id": "3f2a9c1e8b7d4a6f",
  "name": "Rapid Fire E",
  "description": "Taps the E key as fast as is practical while the trigger is held.",
  "playback": { "mode": "while_held", "repeat_count": 5, "gap_ms": 5 },
  "trigger": { "mode": "on_press" },
  "actions": [
    { "type": "key_press",   "params": { "key": "KEY_E" } },
    { "type": "wait",        "params": { "duration_ms": 1 } },
    { "type": "key_release", "params": { "key": "KEY_E" } },
    { "type": "wait",        "params": { "duration_ms": 1 } }
  ]
}
```

Keys are stored as kernel symbols (`KEY_E`, `BTN_SIDE`) rather than numeric
codes, so presets stay readable and stay valid across kernel versions.

### Where things live

| Path | Contents |
| --- | --- |
| `~/.config/clicky-clicker/macros/*.json` | Your macros, one file each |
| `~/.config/clicky-clicker/bindings.json` | Input assignments |
| `~/.config/clicky-clicker/settings.json` | Behavioural settings, shared with the daemon |
| `~/.local/state/clicky-clicker/daemon.log` | Daemon log when not run under systemd |
| `$XDG_RUNTIME_DIR/clicky-clicker/daemon.sock` | Control socket (mode 0600) |

---

## Known Wayland limitations

Being straight about these, because several are inherent to the protocol and no
amount of work will remove them.

### Absolute pointer positioning depends on the compositor

Wayland has no pointer-warp API. `Move pointer to X, Y` works by creating a
virtual **absolute pointing device** — the same shape as a QEMU or VMware USB
tablet — and letting the compositor map it onto the desktop. This works on
GNOME, COSMIC and other libinput-based compositors.

Caveats:

- The daemon has no display connection, so the interface reports the desktop
  size to it. If you change your monitor layout while the daemon is running,
  reopen the application so it re-reports.
- On a multi-monitor setup, the compositor may map the absolute device to a
  single output rather than the full desktop. Coordinates are relative to the
  desktop bounding box.
- Relative motion (`Move pointer by`) passes through pointer acceleration, so it
  will not travel exactly the requested number of pixels. Use absolute movement
  when precision matters.

### The pointer's current position cannot be read

No Wayland client can ask where the pointer is globally. The **Pick From Screen**
button therefore opens a fullscreen window and reads pointer motion *inside its
own surface*, which is allowed, and adds the monitor's offset to get a desktop
coordinate. It is accurate, but it requires that brief fullscreen window rather
than being able to sample the position silently.

### Recording only captures input to the recorder window

The recorder uses GTK event controllers, so it needs no permissions at all — but
a Wayland client only receives events for its own focused surface. Actions
performed in *another* application are not recorded. Compose the sequence in the
recorder window, then edit the timing afterwards.

(Global recording is technically possible via evdev, and the machinery for it
exists in the daemon. It is not wired to the recorder because a background
process silently logging every keystroke to a file is a poor default.)

### Some shortcuts cannot be *detected* by pressing them

**Press an Input** cannot see combinations the compositor reserves for itself
(<kbd>Super</kbd>, and whatever your desktop has bound). Choose those from the
categorised list instead — they can still be *used* as triggers, because the
daemon reads them via evdev; it is only the in-window detection that cannot see
them.

### Suppression is per device, not per key

Hiding an input requires an exclusive grab on the whole device. Clicky Clicker
handles this by re-emitting everything except the suppressed codes, so the
device keeps working normally. The visible consequence is that a suppressed
device shows up as an extra `Clicky Clicker Forward …` device in tools that list
input hardware.

### Timing resolution

Delays are honoured to roughly a millisecond — the granularity the kernel
scheduler provides. A macro asking for `Wait 1 ms` gets approximately that, not
exactly that. Sub-millisecond precision would need a busy-wait, which is not
worth burning a core for.

### Not sandbox-friendly

Direct access to `/dev/input` and `/dev/uinput` is fundamentally incompatible
with Flatpak's sandbox. There is no Flatpak build, and a meaningfully sandboxed
one is not possible.

---

## Troubleshooting

### Everything is inactive / "The background service is not running"

Open the **Status** page. It probes each prerequisite and gives you the exact
command to fix it. From a terminal:

```bash
clicky-clicker-daemon --check
systemctl --user status clicky-clicker-daemon.service
journalctl --user -u clicky-clicker-daemon.service -n 50
```

### "None of the N input devices can be read"

You are not in the `input` group, or you have not logged back in since being
added:

```bash
sudo usermod -aG input $USER   # then log out and back in
groups                         # should list "input"
```

### "Cannot create virtual device" / macros do nothing

`/dev/uinput` is not writable. See
[step 2 of the setup](#2-writing-to-devuinput). Quick check:

```bash
ls -l /dev/uinput && getfacl /dev/uinput
```

### The service starts, then immediately stops

Check the journal. The usual causes are a missing `python3-evdev`, or another
Clicky Clicker daemon already holding the control socket.

### A mapping does nothing

- Is the mapping enabled, and is **Apply Mappings** on in Preferences?
- Does the **Mappings** page show a conflict banner? Two mappings on one input
  are ambiguous.
- Is the macro empty, or all of its steps disabled?
- If the binding is restricted to one device, is that device still present?

### My macro pressed a key and left it down

It should not — every run releases what it pressed. If it happens, press the
emergency stop and please
[file a bug](https://github.com/Ezmanw/Clicky-Clicker/issues), because that is a
genuine defect rather than a configuration problem.

### A macro is running away and I cannot stop it

<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Esc</kbd>. If the desktop is too busy to
respond, from a TTY (<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>F3</kbd>):

```bash
systemctl --user stop clicky-clicker-daemon.service
```

### Absolute pointer moves land in the wrong place

Reopen the application so it re-reports your desktop size, and see the
[note on multi-monitor setups](#absolute-pointer-positioning-depends-on-the-compositor).

---

## Development

```bash
meson setup _build --prefix=/usr --buildtype=debug
ninja -C _build
meson test -C _build --print-errorlogs   # data file validation
python3 -m pytest tests -v               # unit tests
ruff check src tools tests               # lint
```

The unit tests import neither GTK nor evdev and touch no hardware, so they run
anywhere, including in a container.

### Regenerating the keycode table

`src/clickyclicker/models/keycodes.py` is generated from your kernel headers and
checked in, so neither the build nor the runtime depends on them:

```bash
./tools/gen_keycodes.py           # regenerate from this machine's headers
./tools/gen_keycodes.py --check   # verify the checked-in table
```

CI runs `--check` rather than comparing byte-for-byte, because the table is
generated from whatever kernel headers are installed and those differ between
distributions. A table generated on a newer kernel legitimately carries codes an
older one has never heard of; what `--check` asserts is that the table never
*contradicts* the headers it is compared against.

### Adding a new macro action

The editor builds its controls from the model, so this needs no UI work:

1. Add a member to `ActionType` in `models/action.py`.
2. Add an `ActionSpec` to `ACTION_SPECS` describing its label, icon, parameters
   and how to summarise it.
3. Add one branch to `RunHandle._perform` in `macros/executor.py`.

The editor will render the parameter controls, the Add Action menu will list it,
and it will serialise, validate and round-trip automatically.

### Continuous integration

`.github/workflows/build.yml` builds on Ubuntu 24.04 and Fedora 40 (covering the
minimum and a recent libadwaita), runs the data validation tests, installs, and
verifies the installed launchers start. A second job lints, runs the unit tests
and checks the generated keycode table.

---

## Project layout

```
src/clickyclicker/
├── models/        Pure data. No I/O, no GTK, no evdev.
├── input/         Reading (evdev) and injecting (uinput). No GTK.
├── macros/        Playback, recording, validation. No GTK.
├── persistence/   Atomic reads and writes. No GTK.
├── services/      Library, daemon client, session integration. No GTK.
├── daemon/        The background service. No GTK.
├── ui/            GTK 4 and libadwaita. The only package that imports GTK.
└── application/   The AdwApplication that hosts the interface.
```

Each layer may import the ones above it and never the ones below. That is what
lets the daemon run headless and the tests run without hardware — and it is
enforced by the daemon genuinely having no GTK dependency, not by convention
alone.

---

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
