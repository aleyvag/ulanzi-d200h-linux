# Building YAML pages for the bridge — full guide

This is the complete guide to what you can define in the `page_*.yaml`
files the D200H bridge loads. It is the reference that is kept in sync
with the real bridge (handlers in
[src/d200h/bridge.py](../../src/d200h/bridge.py) and the validator in
[src/d200h/pages.py](../../src/d200h/pages.py)).

> **New here?** Start with the [pages cheat-sheet](pages-cheatsheet.md) —
> all the syntax on one page. This guide is the long-form companion.
>
> For setup, installation and the `d200h` commands, see
> [getting-started.md](getting-started.md) and the
> [README](../../README.md). For the HID protocol + what the D200H
> firmware understands, see
> [../developer/firmware-protocol.md](../developer/firmware-protocol.md).
> For the Windows Ulanzi profile converter, see
> [../developer/convert-internals.md](../developer/convert-internals.md).
> For an overview of the project flow, see
> [../developer/architecture.md](../developer/architecture.md).

---

## 1. Where pages live

```
config/pages/
├── user/       ← what the bridge LOADS (priority 1)
└── examples/   ← fallback if user/ is empty
```

- File name: `page_<id>.yaml` (the `<id>` is what you use in `page.goto`
  and as the page_id in logs).
- The `home` page (or `page_0`) is used as the initial screen.
- To add your own page: create `config/pages/user/page_<id>.yaml` or copy
  one from `examples/`.
- Slots `2_3` and `2_4` are **reserved to the firmware** (clock widget).
  Do not include them — the loader rejects them.

---

## 2. File structure

```yaml
title: "My page"          # optional, descriptive
brightness: 60            # optional, 0-100 (informative, not applied yet)

slots:
  "0_0":                  # "row_col" key, row 0-2, col 0-4
    fw_action: <firmware Action without prefix>   # optional, default "system.open"
    fw_param: {<...>}                             # ActionParam — depends on the Action
    icon: <PNG name>                              # optional
    text: "label"                                 # optional, goes over the icon
    font: {<...>}                                 # optional, same dict as the official app
    name: "descriptive"                           # optional, ignored by the firmware
    host_action: {<...>}                          # optional, what the bridge runs
```

- Any slot with no entry stays empty (nothing is painted — do not include
  a decorative slot if you do not want to use it, to avoid inflating the
  ZIP).
- If a slot has `text` but no `icon`, the bridge inserts a transparent
  placeholder so the text is visible (firmware limitation: it does not
  paint `Text` without an `Icon` present).

Validation: the loader raises `PageError` at compile time if it finds: a
duplicate slot, an unknown `fw_action`, a `host_action.type` not in the
list of §4, or an `icon` that does not exist in `config/icons/` or
`config/icons/_firmware/`. Test with `uv run d200h validate`.

---

## 3. Navigating between pages — `fw_action`

`fw_action` is what travels in the ZIP manifest the bridge sends to the
firmware. For page-navigation actions, the **recommended form** is
`page.goto` with an explicit `Page:`.

| `fw_action` | `fw_param` | Use |
|---|---|---|
| `page.goto` ⭐ | `{Page: "<page_id>"}` | Absolute jump to the given page. **The only supported form** of navigation. |
| `page.back` | `{}` | Returns to the previous page (the bridge's internal stack) |
| `page.indicator` | `{}` + `text: "1/5"` | Decorative slot. The official app gives it no action on press; the converter adds a `host_action: stub` so a press opens the Tk popup |

`system.open` is the **default** for any slot without `fw_action` (a
passive slot): you do not need to write it. To open a URL use
`host_action: {type: url}`, not `fw_action`.

> **Deprecated**: `page.next`, `page.prev`, `page.switch`. The loader
> rejects them with `PageError`; the profile converter translates them
> automatically to `page.goto` with the explicit page_id of the next/
> previous within the profile. Do not use them in new YAMLs.

> **Internal firmware tokens** (`system.open`, `system.website`,
> `sound.play`, `sound.stop`, `page.folder`): they exist in the firmware
> but the bridge does not expose them as user `fw_action`s — their job is
> covered by a `host_action` (URL→`url`, audio→`shell`+`aplay`) or they
> are the passive default. Full firmware catalog in
> [../developer/firmware-protocol.md §4](../developer/firmware-protocol.md).

> If you set `fw_action: page.*` and also a `host_action`, the bridge
> **prioritizes the firmware nav** and **ignores** the `host_action`. If
> you want the same slot to navigate AND do something on the host, use
> `host_action: {type: page, target: "<page_id>"}` (without a nav
> `fw_action`) — the bridge changes page from the host via the
> dispatcher.

---

## 4. `host_action` — everything the bridge runs on press

Canonical list: [src/d200h/bridge.py](../../src/d200h/bridge.py) in
`_HOST_HANDLERS`. Valid types enumerated in
[src/d200h/pages.py](../../src/d200h/pages.py) `VALID_HOST_TYPES`.

### 4.1 `shell` — generic shell command

```yaml
host_action:
  type: shell
  cmd: "notify-send 'D200H' \"$(date '+%H:%M:%S')\""
```

| Field | Notes |
|---|---|
| `cmd` | string. Passed to `sh -c`, so it accepts pipes, variables, etc. |

It is launched with `subprocess.Popen(..., shell=True)` and does NOT wait
for it to finish. Requires: `sh` (always present).

### 4.2 `keys` — key combination

```yaml
host_action: {type: keys, keys: "ctrl+shift+t"}
```

Uses `xdotool` on X11, `ydotool` on Wayland. X11-style key names
(`Return`, `Escape`, `XF86AudioPlay`, `bracketright`, etc.). The converter
already normalizes Ctrl/Alt/Shift/Super and most common symbols.

### 4.3 `text` — type text

```yaml
host_action: {type: text, text: "Hello from the D200H", delay_ms: 20}
```

`delay_ms` optional: ms between keypresses (xdotool `--delay` / ydotool
`--key-delay`). Useful to keep the destination app from swallowing keys.

The converter (`d200h convert`) automatically translates the `system.text`
Action of the Windows Ulanzi software to this type, whether it comes as a
direct slot action or inside a `multiactions.routine`. It does not
generate stubs for text.

### 4.4 `media` — media keys

```yaml
host_action: {type: media, cmd: play-pause}
```

`cmd`: `play-pause` (alias `play`), `pause`, `next`, `previous`, `stop`.
Prefers `xdotool/ydotool` with `XF86Audio*`; falls back to `playerctl` if
not.

### 4.5 `volume` — system volume

```yaml
host_action: {type: volume, cmd: up, step: 5}
host_action: {type: volume, cmd: mute}
```

`cmd`: `up`, `down`, `mute`. `step` optional (default 5, in %). Uses
`wpctl` (PipeWire) or `pactl` (PulseAudio).

### 4.6 `brightness_host` — host monitor brightness

```yaml
host_action: {type: brightness_host, cmd: up, step: 10}
```

Uses `brightnessctl` or `light`. Requires permission to write to
`/sys/class/backlight/*/brightness` (the `video` group is usually
enough).

### 4.7 `brightness_device` — the D200H LCD brightness

```yaml
host_action: {type: brightness_device, cmd: up, step: 10}
host_action: {type: brightness_device, cmd: down, step: 10, floor_pct: 5}
host_action: {type: brightness_device, cmd: zero}
```

Goes through ADB (writes the device's
`/sys/class/backlight/backlight/brightness`). Requires ADB active.
Pending: doing it over HID — see
[../developer/firmware-protocol.md §12](../developer/firmware-protocol.md)
`BrightnessMessage`.

`cmd`:

| `cmd` | What it does |
|---|---|
| `up` | raises by `step %` of max (clamped at max) |
| `down` | lowers by `step %` of max (clamped at the floor — see below) |
| `zero` | **skips the floor** and writes 0 (LCD off, power saving) |

**Safety floor** (only `down`): brightness never drops below **10% of
max** (default). Reason: if it reaches 0, the screen goes black and is
indistinguishable from a "dead device", which makes failures hard to
diagnose. To override the limit per slot, pass `floor_pct: N` (0-100) in
the host_action. **`cmd: zero` ignores the floor on purpose** — meant to
turn the LCD off manually when you are going to leave the machine idle but
the bridge keeps running.

**Standby mode (safe wake after `cmd: zero`)**: when the bridge runs
`cmd: zero` it enters *standby mode*: the LCD stays at 0, the firmware
keeps emitting HID, but the bridge **intercepts the next press** and,
instead of running the slot, restores the previous brightness (from the
`~/.cache/d200h/brightness` cache) and exits standby. This way the user
never fires actions blindly: the first touch only wakes the screen, the
following ones operate normally. Details:

- The waking press can be any slot, even one without `host_action` (an
  empty slot) — useful for having "wake keys" with no real function.
- The `cmd: zero` handler does **not** persist 0 in the cache. The file
  keeps the last visible brightness — if the bridge crashes in standby,
  the next handshake restores a useful brightness.
- If for some reason the cache were empty or 0 on waking, the bridge uses
  a fallback (~70% of max) so it does not get stuck.
- The clock slot (`2_3`/`2_4`) is handled by the firmware and does not
  participate in the wake; use any of the 13 user slots.
- If the bridge re-handshakes (returning from suspend, service restart,
  HID reconnect): `_session()` restores the brightness from the cache and
  clears the standby flag to `False` — the first press post-resume runs
  normally, no extra "dead wake" needed.

#### Recommended pattern: Sleep/Shutdown that also turns the LCD off

There are **two variants** and they coexist in the same bridge:

| Variant | YAML slot | Behavior |
|---|---|---|
| Bare suspend | `host_action: {type: system, cmd: suspend}` | Suspends the PC. The LCD stays as it is; the firmware will fall to the Ulanzi logo after ~10s without keepalive. |
| Suspend with LCD off | `host_action: {type: multi, actions: [...]}` (see below) | Puts the D200H into standby (LCD at 0) **before** suspending. On return, the handshake restores brightness from the cache and clears standby. |

Example of the "with LCD off" variant (the real wiring is in
[`config/pages/user/page_linux.yaml`](../../config/pages/user/page_linux.yaml)
slot `0_1` Sleep):

```yaml
host_action:
  type: multi
  actions:
    - {type: brightness_device, cmd: zero}
    - {type: delay, ms: 250}     # gives the LCD time to turn off
    - {type: system, cmd: suspend}
```

Same pattern for `shutdown`/`reboot`: change the `cmd` of the last
`system`. The ~250 ms delay covers the ADB latency of the zero command;
you can raise it if on your hardware you see the PC suspend before the LCD
has changed.

> **Important limitation**: this pattern only covers the "I suspended from
> the deck" case. If you suspend with the laptop lid, the physical power
> button, `systemctl suspend` from a terminal, or the GNOME idle timeout,
> the slot does not fire and the firmware will fall to the Ulanzi logo as
> always. To cover all paths you would need a DBus watcher
> (`PrepareForSleep` from `logind`); it is in
> [../../ROADMAP.md](../../ROADMAP.md) and not implemented yet.

**Persistence across startups**: the D200H firmware does **not** persist
brightness (it resets to the default value on every handshake). The bridge
does it for you: every `brightness_device` (including `zero`) saves the
RAW value to `~/.cache/d200h/brightness` (override with the
`D200H_CACHE_DIR` env var), and every time the bridge completes the
handshake it restores that value via ADB. If you want a reset: delete the
file. Restoration is best-effort — if ADB is not available at startup, it
does not fail, it is just skipped.

### 4.8 `app` — focus if open, launch if not

```yaml
host_action:
  type: app
  match: "Firefox"          # case-insensitive substring of the window title
  cmd: "firefox"            # command if it was not open
```

Uses `wmctrl -a <match>` for focus. For Flatpak:
`cmd: "flatpak run org.mozilla.firefox"`.

### 4.9 `close` — kill a process

```yaml
host_action: {type: close, name: firefox}
```

Equivalent to `pkill -f <name>`. Be careful with very generic names.

### 4.10 `url` — open a URL

```yaml
# Case 1: always opens a new tab/window (default — classic behavior).
host_action: {type: url, url: "https://anthropic.com"}

# Case 2: focus the window if it already exists; if not, open a new one.
host_action:
  type: url
  url: "https://outlook.cloud.microsoft/mail/inbox"
  focus: true
  match: "Outlook"     # optional; if omitted it is derived from the URL
```

Without `focus`, it uses `xdg-open` (always new).

With `focus: true` (X11 only + `wmctrl` installed): it does
`wmctrl -a <match>` to activate the first window whose `WM_NAME` contains
`match` (case-insensitive). If `match` is not given, it is derived from
the URL host (e.g. `outlook.cloud.microsoft` → `outlook`). If `wmctrl`
finds no match, it falls back to `xdg-open`. On Wayland or without
`wmctrl`, `focus: true` is silently ignored and a new one is opened (with
an INFO in the log).

**Important limitation**: `wmctrl` only sees windows, not individual
browser tabs. If your Outlook is in a tab that is not the active one of
its Chrome/Brave window, the match fails and a new one opens. For real
tab-switching you would need the Chrome DevTools Protocol or a native
extension — out of scope for the current bridge.

### 4.11 `system` — session / power

```yaml
host_action: {type: system, cmd: lock}
```

`cmd`: `lock`, `suspend`, `shutdown`, `reboot`, `logout`. Uses
`loginctl`/`systemctl`/`xdg-screensaver`/`gnome-session-quit` as
available.

### 4.12 `notify` — desktop notification

```yaml
host_action: {type: notify, summary: "D200H", body: "Page loaded"}
```

Via `notify-send`.

### 4.13 `page` — change page from the host

```yaml
host_action: {type: page, target: "media"}
```

**Not the same as `fw_action: page.goto`.** `page.goto` navigates via the
firmware path and **ignores** the slot's `host_action`. `type: page`
navigates from the host dispatcher, so it is the **only way to navigate
inside a `multi`** or combined with another host action in the same slot.
For a slot that only navigates, use `page.goto`; for "do X and then change
page", use `type: page` (typically inside a `multi`).

### 4.14 `multi` — sequence of sub-actions

```yaml
host_action:
  type: multi
  actions:
    - {type: keys, keys: "ctrl+t"}
    - {type: delay, ms: 300}
    - {type: text, text: "https://webpage.com/"}
    - {type: delay, ms: 500}
    - {type: keys, keys: "Return"}
```

Pauses are **always** expressed with a `{type: delay, ms: N}` sub-action
as one more line of the `actions` block. Each action on its own line
(block style) makes reading and diffs easier. The Windows profile
converter emits this same form.

### 4.15 `spotify` — Spotify control via the Web API

```yaml
host_action: {type: spotify, cmd: play-pause}
host_action: {type: spotify, cmd: volume-up, step: 10}
host_action: {type: spotify, cmd: volume-set, value: 70}
host_action: {type: spotify, cmd: like}
```

| `cmd` | Fields | What it does |
|---|---|---|
| `play` | — | starts playback |
| `pause` | — | pauses |
| `play-pause` | — | toggle (reads `is_playing` and switches) |
| `next` | — | next track |
| `previous` | — | previous track |
| `volume-up` / `volume-down` | `step?` (default 10) | raises/lowers the device % |
| `volume-set` | `value` (0-100) | sets the absolute % |
| `shuffle` | — | toggle shuffle |
| `like` | — | toggle save/unsave of the current track |
| (all) | `device_id?` | Spotify device ID to use. `"latest"` or empty → autodetect (active, with a 30 s cache) |

Enabling the feature:

- If `config/secrets/spotify.yaml` exists with valid
  `client_id`/`client_secret` → active.
- The `D200H_SPOTIFY=0|false|off|no` var turns the integration off even if
  the file exists (useful in systemd:
  `systemctl --user set-environment D200H_SPOTIFY=0` and
  `restart d200h.service`).
- If it is off, the `type: spotify` slots do not crash: they open a Tk
  popup explaining how to enable it (falling back to notify-send if there
  is no display).

Full setup (developer app, credentials, OAuth):
[spotify-setup.md](spotify-setup.md). Quick recap:

1. `uv run d200h convert "config/ulanzi_deck_profiles/"` already pre-fills
   `config/secrets/spotify.yaml` with `client_id`/`client_secret`
   extracted from any `.ulanziDeckProfile` that has `spotify.*` slots.
2. `uv run d200h spotify-auth` launches the OAuth flow in the browser and
   writes the `refresh_token`. If you did not convert profiles first, copy
   `config/secrets/spotify.yaml.example` first and fill in your own app's
   credentials from <https://developer.spotify.com/dashboard> (Redirect
   URI: `http://127.0.0.1:30901/oauth2callback`).
3. `uv run d200h spotify-status` verifies the token works and lists the
   available devices.

Requirements: a **Spotify Premium** account (the Web API rejects playback
control with Free accounts → HTTP 403), and at least one active Spotify
device (desktop app, mobile or web player open).

### 4.16 `delay` — pause execution

```yaml
host_action: {type: delay, ms: 300}
```

`ms`: milliseconds of `time.sleep`. Meant to be used as a sub-action of
`multi`. Used outside `multi` it blocks the bridge dispatcher while it
lasts — not recommended for standalone slots. If `ms` is 0 or negative, it
does nothing.

### 4.17 `stub` — "untranslated action" feedback

```yaml
host_action:
  type: stub
  command: "spotify.play"
  args: {Track: "..."}
  hint: "Replace with type: shell using spotify-cli, or type: media."
```

On pressing the slot, it opens a 480×220 Tk popup showing `command`,
`args` and `hint`. If there is no display, it falls back to `notify-send`.
This is what the converter generates when an Action from the official
Ulanzi software (Windows) has no direct Linux equivalent — the bridge
policy: **no slot of the original profile stays silent**, everything gives
feedback on press.

If you edit a stub slot by hand to replace it with a real action, just
swap the whole block for the `host_action:` of the corresponding type. The
`examples/page_test_*.yaml` sheets show a stub slot and a slot with its
ready-to-copy Linux equivalent.

---

## 4.bis Automatic page change by window focus

Apart from each slot's `host_action`/`fw_action`, the bridge supports
**automatically changing page according to the focused app** (just like
the Windows software). It is configured in
[`config/focus_rules.yaml`](../../config/focus_rules.yaml). If the file
does not exist, the feature is disabled — it is not mandatory.

This feature is **X11 only**. Full format, how to discover the WM_CLASS,
matching behavior, and the Wayland caveat are documented in the dedicated
guide: [focus-pages.md](focus-pages.md).

---

## 5. Icons

Full mechanics (where the packs live, resizing, format): see
[icons.md](icons.md).

Key points for this doc:

- In the YAML you reference by name (with or without `.png`).
- The loader looks first in `config/icons/` (your icons), then in
  `config/icons/_firmware/` (the factory pack: `btn_nextPage`,
  `btn_goToPage`, `btn_pageIndicator`, `btn_switchProfile`,
  `btn_playAudio`, `btn_stopAudio`, `btn_folder`, `btn_backToParent`,
  `btn_previousPage`).
- If the file does not exist in any path, the loader raises `PageError`
  when validating/compiling the page. It is not a silent warning.
- If a slot has `text` but no `icon`, the bridge inserts a transparent
  PNG so the text renders (the firmware does not paint `Text` without an
  `Icon`).

#### Referencing icons outside `config/icons/` (relative paths)

The `icon:` value does not have to be a bare name: the loader does
`config/icons/<value>`, so you can use **subdirectories** and even leave
`config/icons/` with `../`. This is useful to point at an icon pack that
lives in another repo folder without copying it into `config/icons/`:

```yaml
# config/icons/<value>  →  resolved relative to config/icons/
icon: subfolder/my_icon.png                       # config/icons/subfolder/my_icon.png
icon: ../../create_icons/icons/ready_icons/00_home/headphones.png  # repo_root/create_icons/...
```

Details:

- The path is **always relative to `config/icons/`**, not to the repo root
  or the cwd. To reach `create_icons/...` (which hangs off the root) you
  need two `../` (up out of `icons/` and out of `config/`).
- It still works with or without the `.png` suffix (`resolve_icon` adds it
  if missing).
- If the resolved path does not exist, it is still a `PageError` at
  compile time — verify it with `uv run d200h validate`.
- The arcname inside the ZIP uses only the file's **basename**
  (`Images/<name>.png`), not the full path. Mind the §5.2 restriction
  below: two icons with the same file name (even from different folders)
  collide within the same page.
- The folder can be **read-only**: the bridge resizes icons in memory at
  compile time, it does not write temporaries next to the original.

### 5.1 `icon_generate` — automatic icon generator

When you create a new slot and have no designed PNG, declare the icon with
`icon_generate` and the bridge renders it at compile time (blue frame +
centered text, the style of the `page_home` buttons):

```yaml
"0_0":
  icon_generate:
    text: "./run.sh"        # mandatory
    color: "#1a4f8a"        # optional, background (default home-style blue)
    fg: "#ffffff"           # optional, text color
  host_action: {type: shell, cmd: "./run.sh"}
```

- Mutually exclusive with `icon`. If you set both, the loader raises
  `PageError`.
- The PNG is cached in `config/icons/__generated__/<hash>.png` (hash of
  the spec). If you change `text` or `color` → new hash, new PNG; the old
  one is left orphaned.
- The cache is gitignored — deleting it breaks nothing, it is regenerated
  on the next compile.
- The YAML is the source of truth: the bridge does **not mutate** your
  file. The directive stays as-is, regenerable.
- Auto-fit: the text shrinks until it fits in 196×196 with 14 px padding
  and word wrap. Multi-line with `\n` also works.

Useful commands:

```bash
uv run d200h icon-gen --text "Run script" --out /tmp/test.png
# Preview a PNG without touching the cache or the YAMLs.

uv run d200h icon-gen --gc
# Deletes from the __generated__/ cache the PNGs not referenced by any
# user page YAML.
```

Requires a TTF font (DejaVu Sans Bold or Liberation Sans Bold; both come
with Debian/Ubuntu by default). Without a TTF it falls back to PIL's
bitmap font — it works but looks pixelated; install `fonts-dejavu` or
`fonts-liberation`.

### 5.2 The same icon cannot repeat in one page

**Firmware limitation (confirmed experimentally 2026-05-17)**: if two or
more slots of the **same page** reference exactly the same `Icon` path
inside the ZIP, the firmware **silently rejects** the render of the whole
page (normal ACK, but paints nothing). There is no error in the bridge or
the device — the page simply stays blank.

Practical implications when choosing icons:

- Each icon slot within a page must use a **different file**. If you want
  "the same button" in two slots (e.g. two multi-actions with the same
  generic icon), you need two copies of the PNG with different names.
- The arcname is built with the file's **basename**, so two different
  paths ending in the same name (`a/play.png` and `b/play.png`) **also
  collide**. Rename one.
- *Text-only* slots (no icon) are exempt: the bridge gives them a unique
  transparent placeholder per slot (`Images/_blank_<slot_id>.png`),
  precisely to work around this restriction.
- The `icon_generate` generator also works around it: its arcname includes
  the `slot_id` (`Images/__gen_<hash>_<sid>.png`), so two slots with the
  same `text`+`color` do not clash.

This rule is partially enforced by the code at compile time, but the
safest approach is not to reuse an icon file in two slots of the same
sheet. Implementation: see `_blank_icon_bytes` and `compile_page` in
[src/d200h/pages.py](../../src/d200h/pages.py).

---

## 6. Pattern "create your own page step by step"

```bash
# 1. Copy an example to your user folder
cp config/pages/examples/page_hotkeys.yaml config/pages/user/page_myshortcuts.yaml

# 2. Edit the slots
$EDITOR config/pages/user/page_myshortcuts.yaml

# 3. Verify it compiles and the icons exist
uv run d200h validate

# 4. Make sure something navigates to your new page (e.g. from home).
#    Edit config/pages/user/page_home.yaml and add:
#      "1_4":
#        fw_action: page.goto
#        fw_param: {Page: "myshortcuts"}
#        icon: btn_goToPage
#        text: "Shortcuts"

# 5. Relaunch the bridge
uv run d200h bridge
```

---

## 7. When to choose which `host_action`

| I want to… | Use |
|---|---|
| Universal keyboard shortcut | `keys` |
| Launch/focus an app | `app` (with the title `match`) |
| Shell command or script | `shell` |
| Raise/lower/mute volume | `volume` |
| Play/pause/next music | `media` |
| Monitor brightness | `brightness_host` |
| D200H LCD brightness | `brightness_device` (needs ADB) |
| Open a URL | `url` |
| Lock / suspend / shut down | `system` |
| Toast notification | `notify` |
| Type text in the active window | `text` |
| Close an app by name | `close` |
| Chain several actions | `multi` |
| Pause between sub-actions inside `multi` | `delay` (with `ms`) |
| Change page from the host (combinable with another host action) | `page` |
| Control Spotify (play/pause/next/volume/like) | `spotify` |
| Mark a slot as "Windows action not translated yet" | `stub` |

---

## 8. Common errors and what causes them

| Message | Cause | Fix |
|---|---|---|
| `PageError: icon 'foo' not found` | the name in `icon:` does not exist in `config/icons/` or `config/icons/_firmware/` | fix the name or copy the PNG into the directory |
| `PageError: host_action.type 'X' invalid` | type not listed in §4 | use one of the supported ones |
| `PageError: fw_action 'X' unknown` | Action not supported by the D200H firmware | use one of §3 |
| `PageError: duplicate slot k` | two entries with the same `row_col` key | rename one |
| `PageError: reserved slot 2_3/2_4` | you try to use the clock widget zone | move the entry to another slot |
| Pressing a slot does nothing visible | missing `host_action`, or the handler requires an uninstalled tool | check logs (`uv run d200h -v bridge`) and look at the §4 row for which binary is needed |
| Pressing opens a Tk popup "action not implemented" | the slot is a converter `host_action: stub` | edit the slot and replace it with the corresponding Linux `type:` |
| Pressing a `type: spotify` slot opens a "Spotify disabled" popup | missing `config/secrets/spotify.yaml` or `D200H_SPOTIFY=0` | run `uv run d200h spotify-auth`, or unset the env var |
| Pressing a `type: spotify` slot opens a "no_device" popup | there is no active Spotify player | open the Spotify app (desktop/mobile/web) and press again |
| `PageError: use icon or icon_generate, not both` | the slot has both directives | keep one of the two |
| `PageError: icon_generate.text is required` | spec without text | add `text: "..."` to the `icon_generate` |

---

## 9. Cross-references

- [getting-started.md](getting-started.md) — install, dependencies, first
  bridge.
- [pages-cheatsheet.md](pages-cheatsheet.md) — condensed one-page syntax
  reference.
- [icons.md](icons.md) — icon packs, resizing, generator, advanced
  pipeline.
- [focus-pages.md](focus-pages.md) — automatic page change by window focus
  (X11).
- [spotify-setup.md](spotify-setup.md) — Spotify developer app + OAuth
  from scratch.
- [../../README.md](../../README.md) — project hub, included example
  pages.
- [../developer/architecture.md](../developer/architecture.md) — project
  flow overview (Windows profiles → YAML → ZIP → HID → device → press →
  host).
- [../developer/convert-internals.md](../developer/convert-internals.md) —
  what the `d200h convert` converter does, how it maps each Windows Action
  to YAML.
- [../developer/firmware-protocol.md](../developer/firmware-protocol.md) —
  HID protocol, firmware, limitations.
