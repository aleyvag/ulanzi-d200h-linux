# Troubleshooting & known limitations

If something does not work, start here. For YAML-specific errors
(`PageError: …`) see the table in [pages-guide.md §8](pages-guide.md).

---

## Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `HID: (not found)` in `status` | udev rule not installed or D200H not connected | reinstall the rule, unplug/replug |
| `Permission denied` opening `/dev/hidraw2` | incomplete udev rule | confirm the rule `SUBSYSTEM=="hidraw" ATTRS{idVendor}=="2207" ...` |
| Screen stays on the logo and never repaints | bridge closed or crashed mid-handshake | relaunch `d200h bridge` |
| Buttons type `rcalc`, `rcmd`, etc. | the bridge is not running and `keyMode=win` with factory pages | launch the bridge — it enters host-managed mode and stops typing garbage |
| Slot text does not paint | old version without the placeholder | update the code |
| A page does not load (other pages do) | the ZIP exceeds ~196 KB, or two slots share the same icon file | lighten the icons / use fewer custom-icon slots; rename duplicate icons (see [Known limitations](#known-limitations)) |
| `type: spotify` slot opens a "Spotify disabled" popup | missing `config/secrets/spotify.yaml` or `D200H_SPOTIFY=0` | run `uv run d200h spotify-auth`, or unset the env var (see [spotify-setup.md](spotify-setup.md)) |
| `type: spotify` slot opens a "no_device" popup | no active Spotify player | open the Spotify app (desktop/mobile/web) and press again |
| Focus pages do not switch | running under Wayland, or no `xprop` | the feature is X11 only (see [focus-pages.md](focus-pages.md)) |
| Terminals/apps opened from a deck button warn `VIRTUAL_ENV … does not match` or run inside the bridge's venv | the bridge was started with `uv run`, which exports `VIRTUAL_ENV` | fixed in the bridge (see [below](#apps-launched-from-the-deck-inherit-the-bridges-virtualenv)) — update and restart |

---

## Apps launched from the deck inherit the bridge's virtualenv

The recommended autostart command is `uv run d200h bridge`, and `uv run`
exports `VIRTUAL_ENV=<project>/.venv` into the bridge process. Every
`host_action` (`type: shell`, `type: app`, …) is launched with
`subprocess.Popen`, which inherits that environment — so a button that
opens a terminal or an editor used to spawn it **inside the bridge's
venv**. The visible symptoms:

- new terminals show the project's venv as active (prompt, `which python`);
- running `uv run …` / `python` in another project warns
  `` `VIRTUAL_ENV=…/.venv` does not match the project environment path
  `.venv` and will be ignored ``.

**Fix (already in the code):** `bridge.run()` strips `VIRTUAL_ENV` and
`VIRTUAL_ENV_PROMPT` from its own `os.environ` at startup, so every app it
launches runs in a clean environment. Removing the variable does **not**
change which interpreter the bridge itself uses — it only stops the value
from propagating to child processes. Pull the update and restart:

```bash
systemctl --user restart d200h.service
```

> Alternative if you prefer not to rely on the scrub: install the unit
> with an `ExecStart` that points straight at the venv binary, which never
> exports `VIRTUAL_ENV`:
> `uv run d200h install --force --exec-start "$PWD/.venv/bin/d200h bridge"`.

---

## Two bridges fighting over the device

If you run the systemd service (`d200h.service`) and a local
`uv run d200h bridge` at the same time, they fight over `/dev/hidraw*` and
the behavior becomes erratic. Before any local test:

```bash
systemctl --user stop d200h.service
pkill -f 'd200h bridge'
pgrep -af d200h            # confirm nothing old is still running
```

---

## Debugging

```bash
uv run d200h -v bridge                       # DEBUG logs from the bridge
adb shell tail -f /userdata/logs/log.txt     # firmware logs (needs ADB)
```

If you suspect HID transmission corruption rather than a manifest/icon
problem, `adb pull /tmp/temp.zip` right after a page change gives the
exact ZIP the firmware received. Compare it against what the bridge sent.
Deep detail in
[../developer/firmware-protocol.md §4.4.1](../developer/firmware-protocol.md).

---

## Known limitations

- **The device does not retain profiles.** If you stop the bridge,
  disconnect USB, or reboot the computer, the D200H **returns to the
  logo** after ~10 s without keepalive. Pages live only on the host: you
  must relaunch `d200h bridge` for the device to show them again. (This is
  inevitable — the official Windows app does the same.)
- **Maximum page size on the initial send: ~196 KB.** The firmware
  silently discards larger ZIPs (no error, normal ACK but no render). If
  you converted a Windows profile with many heavy PNGs and the page does
  not load, reduce the weight by editing the icons or use fewer slots with
  a custom icon. The converter applies
  `Image.save(..., optimize=True, compress_level=9)` and an optional
  `Image.quantize` for icons >15 KB; **it is not confirmed that the
  quantize is always necessary** (the optimize may suffice), but the
  official exports ship 35 KB icons that saturate the limit quickly.
- If the bridge dies without keepalive, the device keeps showing whatever
  it last drew until the next handshake. Relaunch the bridge to recover.
- The clock widget (double slot `2_3`+`2_4`) is controlled by the
  firmware; right now the bridge syncs its time but cannot change its
  content. Decoding the `SmallWindowMessage` HID for that is pending — see
  [../../ROADMAP.md](../../ROADMAP.md).
- The D200H LCD brightness goes through ADB (sysfs
  `/sys/class/backlight/...`). It works, but ADB has to be available.

Full technical detail and reverse-engineering pending items in
[../developer/firmware-protocol.md](../developer/firmware-protocol.md).

---

## Cross-references

- [getting-started.md](getting-started.md) — setup and first run.
- [pages-guide.md §8](pages-guide.md) — YAML compile-time errors.
- [../../ROADMAP.md](../../ROADMAP.md) — pending work and not-supported
  features.
