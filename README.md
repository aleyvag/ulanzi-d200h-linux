# Ulanzi D200H — Linux HID bridge

Turn the **Ulanzi D200H** macro keypad into a full Stream-Deck on Linux.
The screen and buttons are driven over **native USB HID** — the same
protocol the official Windows app speaks, discovered by reverse
engineering. No logo flash on page change, no killing the firmware, no
patching anything.

Each page is a YAML file. The bridge (`d200h bridge`) compiles pages into
in-memory ZIPs, talks to the D200H over `/dev/hidraw*`, and pushes the
active page's ZIP on every change. When you press a key, the firmware
sends an HID event with the slot id; the bridge looks at the YAML and runs
the action (change page, or a host action: shell, keys, volume, notify…).

---

## Features

- 🪟 **Import Windows profiles** — translate `.ulanziDeckProfile` exports
  into YAML + icons; never fails (untranslatable slots become guided
  stubs).
- 📑 **Pages & pagination** — `page.goto` / `page.back` over a 5×3 grid.
- 🎵 **Spotify control** — native Web API (OAuth2+PKCE), play/pause/next/
  volume/shuffle/like.
- 🎯 **Focus pages** — auto-switch page based on the active window (X11).
- 🔗 **Multi actions** — several sub-actions per button, with `delay`
  sequencing.
- 🌙 **Standby mode** — turn the LCD off; the first press safely wakes it.
- 🎨 **Auto icons** — declarative `icon_generate` renders button PNGs at
  compile time.
- ⚙️ **systemd service** — robust user service that survives suspend/resume
  with backoff reconnect.

> ### ⚠️ Two front-line limitations
> - **No OBS control.** This bridge does not integrate OBS (no handler, no
>   WebSocket, no scenes). OBS only works via generic `keys`/`shell`/`app`
>   actions. See [ROADMAP](ROADMAP.md).
> - **X11 only.** The window-focus feature and window raising are X11
>   only. **Wayland is untested and likely partly broken.** Keys/text and
>   volume work on both.

---

## Quick Start

```bash
# 1. Device access (udev rule)
sudo cp 99-ulanzi-d200h.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# 2. Install deps
uv sync

# 3. Run the bridge
uv run d200h bridge

# 4. Check the device is connected
uv run d200h status
```

Press any button and the configured action fires. Full setup, host
dependencies (X11 vs Wayland), and the systemd autostart are in
**[docs/user/getting-started.md](docs/user/getting-started.md)**.

---

## Button layout

```
┌──────┬──────┬──────┬──────┬──────┐
│ 0_0  │ 0_1  │ 0_2  │ 0_3  │ 0_4  │  ← row 0 (top)
├──────┼──────┼──────┼──────┼──────┤
│ 1_0  │ 1_1  │ 1_2  │ 1_3  │ 1_4  │  ← row 1 (middle)
├──────┼──────┼──────┼──────┼──────┤
│ 2_0  │ 2_1  │ 2_2  │  2_3 + 2_4  │  ← row 2 + double slot (clock, reserved)
└──────┴──────┴──────┴─────────────┘
```

13 configurable slots (`row_col` keys). The double slot `2_3`+`2_4` is the
firmware clock/logo — **do not configure it**.

---

## Documentation map

**For users** — start here:

- 🚀 [Getting started](docs/user/getting-started.md) — udev, deps, first
  bridge, systemd autostart.
- 📋 [Pages cheat-sheet](docs/user/pages-cheatsheet.md) — all syntax on one
  page.
- 📖 [Pages guide](docs/user/pages-guide.md) — full reference for every
  `host_action`/`fw_action`.
- 🪟 [Importing Windows profiles](docs/user/importing-windows-profiles.md).
- 🎵 [Spotify setup](docs/user/spotify-setup.md) — developer app + OAuth
  from scratch.
- 🎯 [Focus pages](docs/user/focus-pages.md) — auto-switch by window (X11).
- 🎨 [Icons](docs/user/icons.md) — icon packs, generator, advanced
  pipeline.
- 🛠️ [Troubleshooting](docs/user/troubleshooting.md) — and known
  limitations.

**For developers**:

- [Architecture](docs/developer/architecture.md) — end-to-end flow.
- [Firmware protocol](docs/developer/firmware-protocol.md) — the reverse-
  engineered HID protocol.
- [Firmware dead ends](docs/developer/firmware-dead-ends.md) — wrong
  hypotheses, do not repeat.
- [Converter internals](docs/developer/convert-internals.md) — how
  `d200h convert` works.

Also: [ROADMAP](ROADMAP.md) (pending work & not-supported features) ·
[create_icons/](create_icons/README.md) (advanced icon pipeline) ·
[docs/](docs/README.md) (index).

---

## Related projects & credits

This bridge is **exclusively for the Ulanzi D200H**. The D200H and the
plain **D200** are different devices that speak different USB HID
protocols — what works here will not drive a D200.

If you have a **D200** (not the D200H), use
[racerxdl/ulanzi-d200-linux](https://github.com/racerxdl/ulanzi-d200-linux)
instead — it targets that device and is likely the better fit (it even
includes OBS control, which this project does not).

Thanks to that project for the inspiration on documentation style and
project layout. 🙏

## Project info

- **License**: MIT.
- **Firmware logs** (needs ADB): `adb shell tail -f /userdata/logs/log.txt`.
- **Hardware**: D200H VID:PID `2207:0019`, firmware `UlanziDeckKey 2.0.3`.
