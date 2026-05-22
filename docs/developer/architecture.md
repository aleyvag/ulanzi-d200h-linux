# Project architecture — overview

A **1-minute** map of how information flows through the project. The key
takeaways: never hand-edit what a pipeline generates, and never invent
commands the bridge does not understand.

> Deep detail on each layer: the HID protocol in
> [firmware-protocol.md](firmware-protocol.md), the converter in
> [convert-internals.md](convert-internals.md), and the YAML format the
> bridge consumes in [../user/pages-guide.md](../user/pages-guide.md).

---

## 1. End-to-end flow

```
┌─────────────────────────────┐  d200h convert  ┌─────────────────────────────┐
│  config/ulanzi_deck_profiles│ ───────────────▶│  config/pages/user/          │
│   *.ulanziDeckProfile       │                 │   page_*.yaml                │
│   (export from the official │                 │   (bridge format)            │
│    Windows software)        │                 │                              │
└─────────────────────────────┘                 └─────────────────────────────┘
                                                              │
                                                              │ d200h bridge
                                                              ▼
┌─────────────────────────────┐  send_zip (HID) ┌─────────────────────────────┐
│  in-memory ZIP              │ ───────────────▶│   D200H (Qt firmware)        │
│   manifest.json + Images/   │                 │   paints the active page     │
│   (compiled at startup)     │                 │                              │
└─────────────────────────────┘                 └────────────────┬────────────┘
                ▲                                                 │
                │ send_zip on page change                         │ IN: slot pressed
                │                                                 ▼
┌─────────────────────────────┐  PRESS event   ┌─────────────────────────────┐
│  bridge dispatcher          │ ◀──────────────│   /dev/hidraw* (iface 0)     │
│   - fw_action page.* → ZIP  │                │   HID event with slot_id     │
│   - host_action → handlers  │                │                              │
└─────────────────────────────┘                 └─────────────────────────────┘
                │
                │ shell / keys / app / multi / spotify / ...
                ▼
   subprocess (xdotool, wpctl, brightnessctl, notify-send, …)
   or Spotify Web API (urllib + rotating refresh_token persisted to
   config/secrets/spotify.yaml)
```

Secondary layers (derivable, with no user involvement at runtime):

- `config/secrets/spotify.yaml` — Spotify credentials (gitignored).
  Pre-filled by `d200h convert` from the `ActionParam` of the spotify.*
  slots, completed by `d200h spotify-auth` (OAuth2 PKCE, callback on
  `127.0.0.1:30901`). The bridge updates the `refresh_token` every time
  Spotify rotates it.
- `config/icons/__generated__/<hash>.png` — icon generator cache.
  Derivable: if you delete it, it is regenerated from the `icon_generate:`
  directives of the YAMLs.
- `~/.cache/d200h/brightness` — last RAW brightness value of the D200H
  LCD (written by `brightness_device` on each change, read by the bridge
  after the handshake completes to restore it). The firmware does not
  persist brightness across reboots; this file covers that gap. Override
  with `D200H_CACHE_DIR`. Deleting the file = reset to the firmware's
  default brightness on the next startup. See
  [../user/pages-guide.md §4.7](../user/pages-guide.md).

---

## 2. Components and key files

| Layer | What it does | Code |
|---|---|---|
| **Converter** | Reads `.ulanziDeckProfile`, unzips it, translates each Windows Action into a Linux `host_action`/`fw_action`. Anything untranslatable → `stub` with a `hint`. Auto-extracts Spotify credentials to `config/secrets/spotify.yaml`. | [src/d200h/convert.py](../../src/d200h/convert.py) |
| **Loader / validator** | Parses the `page_*.yaml`, validates `fw_action`, `host_action.type`, icons, and the `icon_generate` directive. Raises `PageError` at compile time if anything is wrong. | [src/d200h/pages.py](../../src/d200h/pages.py) |
| **ZIP compiler** | Builds `manifest.json` and packs the referenced PNGs (196×196 RGBA, optimized) into a ZIP under 196 KB. | [src/d200h/manifest.py](../../src/d200h/manifest.py), [src/d200h/zip_pack.py](../../src/d200h/zip_pack.py), [src/d200h/icons.py](../../src/d200h/icons.py) |
| **Icon generator** | Renders 196×196 PNGs (blue card + auto-fit text) for slots with `icon_generate:`. Hash-keyed cache in `config/icons/__generated__/`. | [src/d200h/icon_gen.py](../../src/d200h/icon_gen.py) |
| **HID client** | Opens the `/dev/hidraw*` of the vendor interface, performs the handshake (clock + drain + ZIP), sends keepalive every 2 s, parses IN reports. | [src/d200h/hid.py](../../src/d200h/hid.py) |
| **Bridge / dispatcher** | Main loop. Handles PRESS events resolving firmware nav vs `host_action`. Implements the handlers (`shell`, `keys`, `app`, `multi`, `spotify`, `stub`, …). | [src/d200h/bridge.py](../../src/d200h/bridge.py) |
| **Spotify client** | OAuth2 Web API (transparent refresh, persistent rotation). Optional: active if `config/secrets/spotify.yaml` exists and `D200H_SPOTIFY` does not disable the feature. | [src/d200h/spotify.py](../../src/d200h/spotify.py), [src/d200h/spotify_auth.py](../../src/d200h/spotify_auth.py) |
| **CLI** | Sub-commands `bridge`, `validate`, `compile`, `convert`, `list`, `status`, `spotify-auth`, `spotify-status`, `icon-gen`, `install`, `uninstall`. | [src/d200h/cli.py](../../src/d200h/cli.py) |

---

## 3. Golden rule: never hand-edit what the converter generates

`config/pages/user/page_*.yaml` is a **product** of the converter when
you came from Windows profiles. If you edit it by hand and then run
`d200h convert` without `--keep-existing`, **you lose it**. Two correct
ways to customize:

1. **`--keep-existing`**: re-converts everything while keeping the YAMLs
   you already edited. Useful once you have invested time fixing stubs.
2. **Improve the converter**: if you spot a Windows pattern the converter
   could translate better to Linux (not inventing one, but a real,
   verified equivalent), add the case to
   [src/d200h/convert.py](../../src/d200h/convert.py) and re-run
   `d200h convert`. That is the correct flow. Hand-editing the YAML with
   that translation is a local patch that is lost on re-conversion and
   benefits nobody else.

If the bridge needs a new `host_action.type` (it does not exist in
`_HOST_HANDLERS`), adding it is bridge work — do not invent types in the
YAML, because the loader rejects them
([VALID_HOST_TYPES in pages.py](../../src/d200h/pages.py)).

---

## 4. Policy for actions with no Linux equivalent

Policy: **every slot in the original profile must give feedback when
pressed, never silence.** The converter materializes any Action with no
direct equivalence as `host_action: stub` with:

- `command`: the firmware Action without the `com.ulanzi.ulanzideck.`
  prefix
- `args`: the original `ActionParam`
- `hint`: a textual hint of how the user would write it on Linux

On press, the bridge opens a Tk popup with those three pieces of data
(falling back to `notify-send` if there is no display). The user sees
"this slot existed on Windows, this is what it did, this is how you fix
it". Editing the YAML to replace the `stub` with the corresponding Linux
`type:` is manual work — the converter never invents dubious
equivalences.

Applies to: `system.switchhotkey`, `sound.play`, `sound.stop`,
`smallwindow.window`, `page.indicator`, `system.open` with an empty Path
or with no app known to `_KNOWN_APPS`. See
[convert-internals.md §4](convert-internals.md) and the
`config/pages/examples/page_test_*.yaml` sheets for the cases.

> Exception: `spotify.*` does **not** fall back to a stub. The converter
> translates each
> `spotify.play|pause|next|previous|volumeup|volumedown|volumeset|shuffle|tracklike`
> to the native handler `host_action: {type: spotify, cmd: ...}` (OAuth2
> Web API). If the Spotify client is disabled at runtime (no credentials
> or `D200H_SPOTIFY=0`), the handler opens the same Tk popup as the stub
> with instructions to enable it — same "no slot stays silent" principle,
> different layer.

---

## 4.bis Bridge runtime states

Apart from the slot dispatcher and the page stack, the bridge keeps two
runtime states whose behavior cuts across the active page:

| State | Where it lives | Trigger | Effect |
|---|---|---|---|
| **Persistent LCD brightness** | `~/.cache/d200h/brightness` (RAW, written by `_h_brightness_device`) | Every `cmd: up`/`down` | On the next handshake, `_restore_brightness_device()` re-applies the value via ADB. `cmd: zero` does NOT persist — it preserves the last visible value to survive crashes. |
| **Standby mode** | `BridgeContext.standby_mode: bool` (memory) | `host_action: brightness_device cmd: zero` | LCD at 0. The next press (any slot) in `_on_press()` intercepts it: restores brightness and clears the flag, **without** running the slot. Re-handshake also clears the flag, so a suspend/resume never leaves standby stuck. |

Together these two solve "turn the D200H off when the PC suspends"
without system hooks or decoding new HID: the Sleep slot is a `multi`
that does zero → delay → `system suspend`, and on return the bridge
restores the LCD from the cache. Limitation: it only covers suspends
initiated from the deck (not lid close, not GNOME idle). See
[../user/pages-guide.md §4.7](../user/pages-guide.md) and
[../../ROADMAP.md](../../ROADMAP.md).

---

## 5. Where the logic is NOT (common mistakes)

- **The D200H firmware does not know what "the next page" is.** It only
  emits an IN report when you press. The bridge resolves the nav and
  pushes a new ZIP over HID. If the bridge is not running, the device
  returns to the logo after ~10 s.
- **The firmware does not launch apps by itself on Linux.** In
  `keyMode=win` it would try to use Win+R (which does not exist on
  Linux); that is why EVERY useful action goes through the host. There
  is no internal "Linux profile".
- **Do not edit `/tmp/standalone/`** expecting something to change live:
  the firmware has no inotify and no SIGHUP. That path is only for the
  screen shown when there is no host. For live changes: HID via the
  bridge.
