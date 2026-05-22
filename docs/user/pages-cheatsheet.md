# Pages cheat-sheet — every bridge YAML command (condensed)

Ultra-fast cheat-sheet of **everything** the `page_*.yaml` files accept:
documented + undocumented aliases/fields (marked `# (no-doc)`). Each
alternative appears **only once**. Full detail in the
[pages guide](pages-guide.md). Verify with `uv run d200h validate`.

---

## Page / slot structure

```yaml
title: "My page"          # optional, descriptive
brightness: 60            # optional 0-100 (informative, NOT applied yet)
slots:
  "0_0":                  # row_col key (row 0-2, col 0-4). 2_3 and 2_4 reserved (clock)
    fw_action: page.goto                 # optional, default system.open
    fw_param: {Page: "media"}            # ActionParam of the fw_action
    icon: firefox                        # PNG in config/icons (with or without .png)
    text: "Label"                        # goes over the icon (no icon → transparent placeholder)
    font: {}                             # optional, same dict as the official app
    name: "desc"                         # optional, ignored by firmware
    host_action: {type: keys, keys: "ctrl+s"}
```

```yaml
icon: subfolder/icon.png                 # path relative to config/icons/
icon: ../../create_icons/x/y.png         # ../ allowed (goes up out of config/icons/)
```

---

## fw_action — navigation / firmware (in the manifest)

```yaml
fw_action: page.goto      # ⭐ absolute jump — ONLY supported nav
fw_param: {Page: "home"}
```
```yaml
fw_action: page.back      # returns to the previous page (bridge stack)
fw_param: {}
```
```yaml
fw_action: page.indicator # decorative slot; the converter adds a host_action stub
fw_param: {}
text: "1/5"
```

```yaml
# DEPRECATED (the loader REJECTS them with PageError): page.next, page.prev, page.switch
# If fw_action is nav (page.*) and there is a host_action → nav wins, host_action ignored.
# For nav + host action in the same slot: use host_action type: page (see below).
# system.open is the DEFAULT (no need to write it). Internal firmware tokens
# (system.open/website, sound.*, page.folder): see ../developer/firmware-protocol.md.
```

---

## host_action — what the bridge runs on press

### shell — generic command (sh -c, does not wait)
```yaml
host_action: {type: shell, cmd: "notify-send hi"}
```

### keys — key combo (xdotool/ydotool)
```yaml
host_action: {type: keys, keys: "ctrl+shift+t"}
host_action: {type: keys, keys: "XF86AudioPlay"}
```

### text — type text
```yaml
host_action: {type: text, text: "Hello"}
host_action: {type: text, text: "Hello", delay_ms: 20}    # ms between keypresses
```

### media — media keys (XF86Audio*, falls back to playerctl)
```yaml
host_action: {type: media, cmd: play-pause}   # default; alias: play
host_action: {type: media, cmd: pause}
host_action: {type: media, cmd: next}
host_action: {type: media, cmd: previous}
host_action: {type: media, cmd: stop}
```

### volume — system volume (wpctl/pactl)
```yaml
host_action: {type: volume, cmd: up, step: 5}    # step optional, default 5 (%)
host_action: {type: volume, cmd: down, step: 5}
host_action: {type: volume, cmd: mute}
```

### brightness_host — monitor brightness (brightnessctl/light)
```yaml
host_action: {type: brightness_host, cmd: up, step: 10}    # step default 10
host_action: {type: brightness_host, cmd: down, step: 10}
```

### brightness_device — the D200H LCD brightness (via ADB)
```yaml
host_action: {type: brightness_device, cmd: up, step: 10}
host_action: {type: brightness_device, cmd: down, step: 10, floor_pct: 5}  # floor default 10%
host_action: {type: brightness_device, cmd: zero}   # turns LCD off (skips floor), arms standby
```

### app — focus if open, launch if not (wmctrl)
```yaml
host_action: {type: app, match: "Firefox", cmd: "firefox"}
host_action: {type: app, cmd: "firefox"}                    # match derived from the 1st token of cmd
```

### close — kill a process (pkill -f)
```yaml
host_action: {type: close, name: firefox}
```

### url — open a URL
```yaml
host_action: {type: url, url: "https://x.com"}                 # xdg-open (always new)
host_action: {type: url, url: "https://x.com", focus: true}    # focus if it exists (X11+wmctrl)
host_action: {type: url, url: "https://x.com", focus: true, match: "Outlook"}  # explicit match
```

### system — session / power
```yaml
host_action: {type: system, cmd: lock}
host_action: {type: system, cmd: suspend}
host_action: {type: system, cmd: shutdown}
host_action: {type: system, cmd: reboot}
host_action: {type: system, cmd: logout}
```

### notify — toast notification (notify-send)
```yaml
host_action: {type: notify, summary: "D200H", body: "text"}
```

### page — change page from the host
```yaml
# NOT the same as fw_action: page.goto. page.goto navigates via the firmware and
# IGNORES the slot's host_action. type: page navigates from the host → it is the only
# way to navigate inside a multi or combined with another action.
host_action: {type: page, target: "media"}
```

### delay — pause (time.sleep). Meant for inside multi
```yaml
host_action: {type: delay, ms: 300}    # ms<=0 = no-op
```

### multi — sequence of sub-actions (pauses via a `delay` sub-action)
```yaml
host_action:
  type: multi
  actions:
    - {type: keys, keys: "ctrl+t"}
    - {type: delay, ms: 300}
    - {type: text, text: "https://x.com/"}
    - {type: keys, keys: "Return"}
```

### spotify — Web API (Premium + active device)
```yaml
host_action: {type: spotify, cmd: play}
host_action: {type: spotify, cmd: pause}
host_action: {type: spotify, cmd: play-pause}
host_action: {type: spotify, cmd: next}
host_action: {type: spotify, cmd: previous}
host_action: {type: spotify, cmd: volume-up, step: 10}       # step default 10
host_action: {type: spotify, cmd: volume-down, step: 10}
host_action: {type: spotify, cmd: volume-set, value: 70}     # 0-100
host_action: {type: spotify, cmd: shuffle}
host_action: {type: spotify, cmd: like}
host_action: {type: spotify, cmd: play, device_id: "latest"} # device_id optional ("latest"/empty = autodetect)
```

### stub — "untranslated action" feedback (Tk popup, emitted by the converter)
```yaml
host_action: {type: stub, command: "spotify.play", args: {Track: "..."}, hint: "Use type: spotify"}
```

---

## icon_generate — auto-generated icon (exclusive with icon:)
```yaml
"0_0":
  icon_generate:
    text: "./run.sh"        # mandatory
    color: "#1a4f8a"        # optional, background (default blue)
    fg: "#ffffff"           # optional, text (default white)
  host_action: {type: shell, cmd: "./run.sh"}
```

---

## focus_rules.yaml — page change by focus (X11; optional)
```yaml
# config/focus_rules.yaml
default: home               # optional: where to return if nothing matches
rules:
  - {match: "Code",    page: vscode}     # match = substring of the 2nd WM_CLASS string
  - {match: "Spotify", page: spotify}
# Discover the class:  xprop WM_CLASS  → use the 2nd string.
```
Full guide: [focus-pages.md](focus-pages.md).

---

## Quick notes

- Valid types (loader): `shell keys text media volume brightness_host
  brightness_device app close url system page multi notify delay stub spotify`.
- Any `type` outside that list → `PageError` at compile time.
- `cmd: zero` does not persist 0 in the cache; the first press after zero
  only wakes the LCD (does not run the slot).
- The same icon file **cannot** repeat in two slots of the same page (the
  firmware leaves the page blank). Text-only and `icon_generate` are
  exempt.
