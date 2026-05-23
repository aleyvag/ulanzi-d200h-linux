# Getting started

From a freshly cloned repo to a working bridge in a few minutes. Every
command runs from the repo root.

---

## 1. Install the udev rule (no-sudo device access)

```bash
sudo cp 99-ulanzi-d200h.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

This gives your logged-in user access to the device's `/dev/hidraw*`
without `sudo`. Unplug and replug the D200H after applying it.

---

## 2. Host dependencies

You need `python >= 3.9` with [`uv`](https://github.com/astral-sh/uv) (or
pip). The bridge's host actions are optional — install only the tools for
the features you use:

| Feature | Package | Notes |
|---|---|---|
| Keys / text | `xdotool` (X11) or `ydotool` (Wayland) | hybrid: picks per session |
| Volume | `wpctl` (PipeWire) or `pactl` (PulseAudio) | either works |
| Media keys | `playerctl` | fallback for `media` actions |
| Host brightness | `brightnessctl` or `light` | agnostic (sysfs) |
| Focus/raise apps (`app`, `url focus`) | `wmctrl` | **X11 only** (on Wayland opens a new window) |
| Window-focus pages | `xprop` | **X11 only** ([focus-pages.md](focus-pages.md)) |
| Notifications | `notify-send` | |
| Stub popups | `python3-tk` (Debian/Ubuntu) | optional, only if you imported Windows profiles and want untranslated slots to open a descriptive window instead of a `notify-send` |
| Device LCD brightness + `status` | `adb` | only for `brightness_device` and `status` |

> ⚠️ **Window system**: this project is mostly **X11**. The window-focus
> feature is X11 only; raising windows (`wmctrl`) is X11 only. Wayland is
> untested and likely partly broken. Keys/text and volume work on both.

---

## 3. Install the package

```bash
uv sync          # creates .venv and resolves dependencies
```

### Alternative: plain `pip + venv` (no `uv`)

`uv` is the recommended path — it is ~10× faster than pip and manages
the venv and Python version transparently. Install it once with
`curl -LsSf https://astral.sh/uv/install.sh | sh` and you are done.

If you really cannot or do not want to install `uv`, the project is a
regular PEP 621 package and `pip` works fine:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After activating the venv, drop the `uv run` prefix from every command
below (`d200h bridge` instead of `uv run d200h bridge`). Remember to
re-activate the venv (`source .venv/bin/activate`) in every new shell,
or use `uv` and skip that step.

---

## 4. Run the bridge

```bash
uv run d200h bridge          # starts the bridge (main loop)
uv run d200h status          # check ADB + HID of the device
```

Press any button on the D200H and the configured action fires on the
host. By default the bridge loads the pages in `config/pages/user/`,
falling back to `config/pages/examples/` if that folder is empty.

> 💡 **Try the auto page-switch feature (X11 only)**: with the shipped
> examples, run
> `cp config/focus_rules.yaml.example config/focus_rules.yaml` and
> restart the bridge. Focusing **Chrome/Firefox/Chromium/Brave**
> automatically jumps to the **`browser`** page (YouTube, Google,
> tab hotkeys…); focusing **Nautilus/Nemo/Thunar/Dolphin** jumps to the
> **`files`** page (Downloads, Documents…). Full details in
> [focus-pages.md](focus-pages.md).

Other useful commands:

```bash
uv run d200h list            # list detected pages
uv run d200h validate        # validate + dry-compile (does not send to the device)
uv run d200h compile --out /tmp/zips   # dump the ZIPs to disk for inspection
```

For everything you can put in a page, see the
[pages cheat-sheet](pages-cheatsheet.md) and the full
[pages guide](pages-guide.md).

---

## 5. Autostart (systemd user service)

To have the bridge start automatically on boot (not just on graphical
login) and survive suspend/resume, install it as a systemd user service.

```bash
uv run d200h install --now --linger        # write the unit + enable + linger, all in one
```

That writes the unit, runs `daemon-reload`, `enable --now`, and tries to
activate `linger` **without sudo**. By design, `d200h install` never
calls `sudo` and never kills other processes.

> **Why `enable-linger`**: the unit uses
> `WantedBy=graphical-session.target`. Without linger, the systemd user
> manager only lives while there is an active graphical session, so after
> a reboot (or in sessions that do not trigger that target) the service
> would not start on its own. `enable-linger` keeps the user manager alive
> from boot and guarantees autostart.

If your distro requires privileges to enable linger (most do), the
command above prints a hint and you finish manually with:

```bash
sudo loginctl enable-linger $USER
```

Check with `loginctl show-user $USER | grep Linger` — `Linger=yes` means
the service will start on boot without a graphical login.

### If something goes wrong

If a previous bridge instance is fighting for `/dev/hidraw*` or you have
a stale system-level unit, stop everything and reinstall cleanly:

```bash
systemctl --user stop d200h.service        # stop the service
pkill -f 'd200h bridge'                    # kill any stray local bridge
uv run d200h install --now --linger --force  # rewrite and restart
```

Verify and inspect:

```bash
systemctl --user status d200h.service
journalctl --user -u d200h.service -f
```

Uninstall (stops, disables, and removes the unit):

```bash
uv run d200h uninstall --now
```

`linger` is never touched on uninstall (another user service may use it).
To remove it by hand: `loginctl disable-linger $USER`.

> On **suspend/resume** you do not need to do anything: the firmware falls
> to the Ulanzi logo after ~10 s without keepalive, and on return the
> bridge detects the `/dev/hidraw*` reappearing and re-handshakes with
> exponential backoff (1, 2, 4, 8, 16, 30 s).

---

## Quick troubleshooting

| Symptom | Fix |
|---|---|
| `HID: (not found)` in `status` | reinstall the udev rule, unplug/replug the device |
| Screen stuck on the logo | relaunch `d200h bridge` (it died or never finished the handshake) |
| Buttons type `rcalc`/`rcmd` garbage | launch the bridge — it switches the device to host-managed mode |

More in [troubleshooting.md](troubleshooting.md).

---

## Next steps

- **Import what you already had on Windows** →
  [importing-windows-profiles.md](importing-windows-profiles.md).
- **Build pages from scratch** → [pages-cheatsheet.md](pages-cheatsheet.md)
  then [pages-guide.md](pages-guide.md).
- **Control Spotify** → [spotify-setup.md](spotify-setup.md).
- **Auto-switch pages per app** → [focus-pages.md](focus-pages.md).
- **Custom icons** → [icons.md](icons.md).
