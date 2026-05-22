# Converter internals: `.ulanziDeckProfile` → bridge YAML

How a profile from the official Ulanzi software (Windows) is translated
into the format this bridge consumes, and what information is needed for
the translation to be correct. Implemented in
[src/d200h/convert.py](../../src/d200h/convert.py).

> Looking for the **user how-to** (export from Windows, run the command,
> fix stubs)? See
> [../user/importing-windows-profiles.md](../user/importing-windows-profiles.md).
> This document covers the internals.

---

## 1. The `.ulanziDeckProfile` container

Each file exported by the official app is **a single profile** and has
this binary shape:

```
bytes 0..11   "#Version: 2\n"          ← fixed ASCII header (12 bytes)
bytes 12..    <standard ZIP>            ← PK\x03\x04 signature
```

To read it: discard the first 12 bytes, decompress the rest as a normal
ZIP. If the header does not match, it still tries it as a pure ZIP (with
a `log.warning`) — some versions might change the header.

### ZIP contents

```
<profile-uuid>.ulanziProfile/
├── manifest.json          # metadata of the whole profile
├── icon_<name>.png        # profile icon (optional)
└── Profiles/
    └── <page-uuid>/
        ├── manifest.json  # definition of the 13 slots of ONE page
        ├── Images/        # PNG/SVG referenced by the slots
        └── Files/         # attachments (usually empty)
```

> Important: **each `.ulanziDeckProfile` contains ONE profile only**. The
> pages are flat inside the ZIP, not nested. The "folders" and "switch
> profile" actions of the official software reference other profiles by
> UUID; those profiles live in OTHER separate `.ulanziDeckProfile` files.
> That is why the converter operates on the whole directory, not file by
> file.

---

## 2. Profile `manifest.json` (root)

```json
{
  "Device":  {"Model": "D200H", "UUID": "..."},
  "Name":    "VSCode",
  "Icon":    "icon_vscode.png",
  "Pages":   {
    "Current": "<page-uuid>",           // the profile's default page
    "Pages":   ["<uuid-1>", "<uuid-2>"] // all pages, in order
  },
  "Version": 2
}
```

What matters to the converter:

| Field | Use |
|---|---|
| `Name` | Slugified to snake_case for the page_id (`"VSCode"` → `vscode`) |
| `Pages.Pages` | Page order — basis for resolving `page.next/prev` |
| `Pages.Current` | The profile's entry page — its page_id stays **without a suffix** |

---

## 3. `manifest.json` of each page

```json
{
  "Controllers": [
    {
      "Type": "Keypad",
      "Actions": {
        "0_0": {
          "Action":     "com.ulanzi.ulanzideck.system.hotkey",
          "ActionID":   "<this-slot-uuid>",
          "ActionParam": {"Hotkey": "Ctrl+Alt+H"},
          "LinkedTitle": true,
          "Name":       "Hotkey",
          "State":      0,
          "ViewParam":  [{
            "Icon":     "Images/btn_hotkey.png",
            "IconDef":  "Images/btn_hotkey.png",
            "IconEx":   "C:/Users/.../btn_hotkey.png",
            "Text":     "Roo Code"
          }]
        }
      }
    },
    { "Type": "Encoder", "Actions": {} }    // the D200H has no encoder; always empty
  ],
  "Icon": "",
  "Name": ""
}
```

- In the Ulanzi manifest the keys come as `"col_row"` (`0_0`…`4_2`, col
  first) — the converter **translates** them to the bridge convention
  `"row_col"` (`0_0`…`2_4`, row first). The input slots `3_2`/`4_2`
  (= `2_3`/`2_4` in the output) are **reserved to the firmware**
  (clock/logo widget); the converter skips them without error.
- Only `Controllers[0]` ("Keypad") has content on the D200H.
- `ViewParam[].Icon` can be:
  - **Relative** inside the ZIP: `"Images/foo.png"`, `"Images/<sha>.png"`
    → the icon is in `Profiles/<uuid>/Images/`.
  - **Absolute Windows path**: `"C:/Users/.../foo.png"` → the real icon
    usually exists under `Images/` with the same basename; the converter
    looks it up by basename.
- `ViewParam[].IconDef` is almost always the author's original path
  (Windows or local); it is used **only if `Icon` is empty**.
- `Text` is what the slot paints over the icon.
- `ActionID`/`Name`/`State`/`LinkedTitle` are not used by the bridge.

---

## 4. `Action` catalog (prefix `com.ulanzi.ulanzideck.`)

| Action | `ActionParam` | Translation to bridge YAML |
|---|---|---|
| `system.hotkey` | `{Hotkey: "Ctrl+Alt+H"}` | `host_action: {type: keys, keys: "ctrl+alt+h"}` (normalized) |
| `system.multimedia` | `{Hotkey: "Volumn_Down"\|"Play_Pause"\|...}` | `host_action: {type: volume\|media, cmd: ...}` |
| `system.website` | `{Url: "..."}` | `host_action: {type: url, url: ...}` |
| `system.open` | `{Path: "C:\\...exe"}` | Heuristic: URL→`url`; basename in `_KNOWN_APPS`→`type: app`; rest→`stub` with a comment |
| `multiactions.routine` | `{Actions: [...]}` with sub-actions interleaved with `multiactions.delay` | `host_action: {type: multi, actions: [...]}`; each `multiactions.delay` is emitted as a `{type: delay, ms: N}` sub-action |
| `page.next` | `{}` | `fw_action: page.goto, fw_param: {Page: <next-in-this-profile>}` |
| `page.prev` | `{}` | `fw_action: page.goto, fw_param: {Page: <previous-in-this-profile>}` |
| `page.goto` | `{Page: N}` (1-indexed) | `fw_action: page.goto, fw_param: {Page: <resolved-page_id>}` |
| `page.folder` | `{ProfileUUID: "..."}` | Same as switch — points to the destination profile's entry |
| `page.switch` | `{Profile, ProfileUUID}` | `fw_action: page.goto, fw_param: {Page: <destination-slug>}`. If the ProfileUUID was not converted → `host_action: stub` |
| `page.back` | `{}` | `fw_action: page.back` (natively supported) |
| `page.indicator` | `{}` + `ViewParam[0].Text` | Decorative slot (only `icon` + `text`, no action) |
| `smallwindow.window` | `{SmallViewMode: N}` | Decorative + comment (firmware widget, not host-controllable) |
| `spotify.play` | `{ClientId, ClientSecret, deviceId, ...}` | `host_action: {type: spotify, cmd: play-pause}` + credential extraction (see §4.1) |
| `spotify.pause` | same | `host_action: {type: spotify, cmd: pause}` |
| `spotify.next` / `spotify.previous` | same | `host_action: {type: spotify, cmd: next\|previous}` |
| `spotify.volumeup` / `spotify.volumedown` | same | `host_action: {type: spotify, cmd: volume-up\|volume-down}` |
| `spotify.volumeset` | `{volumeValue: N, …}` | `host_action: {type: spotify, cmd: volume-set, value: N}` |
| `spotify.shuffle` | same | `host_action: {type: spotify, cmd: shuffle}` (toggle) |
| `spotify.tracklike` | same | `host_action: {type: spotify, cmd: like}` (toggle save/unsave) |
| `sound.play` / `sound.stop` | various | `host_action: stub` (not implemented in the bridge) |
| **Any other / unknown** | — | `host_action: stub` with the original as `args` |

### 4.1 Automatic extraction of Spotify credentials

The official Windows app embeds its OAuth2 credentials inside the
`ActionParam` of each `spotify.*` slot (fields `ClientId` and
`ClientSecret`). The converter captures them **only once** — the first
slot in the bundle with both fields non-empty wins, regardless of profile
or page name — and, if `config/secrets/spotify.yaml` does not yet exist,
writes a template with:

```yaml
client_id: "<extracted from the slot>"
client_secret: "<extracted from the slot>"
refresh_token: ""   # empty until you run d200h spotify-auth
```

Permissions `0600`, gitignored. The auto-extraction does NOT overwrite an
existing file — if you already ran `spotify-auth`, the real
`refresh_token` stays intact and the converter touches nothing.

After converting, complete the OAuth:

```bash
uv run d200h spotify-auth        # opens browser, callback :30901, writes refresh_token
uv run d200h spotify-status      # diagnostics (token + devices + current track)
```

Detail of the `type: spotify` handler and supported `cmd`s in
[../user/pages-guide.md §4.15](../user/pages-guide.md). End-to-end setup
of the Spotify developer app in
[../user/spotify-setup.md](../user/spotify-setup.md).

### Hotkey normalization

| Original | Output | Notes |
|---|---|---|
| `Ctrl+Alt+H` | `ctrl+alt+h` | Modifiers in lowercase |
| `Win+...` | `super+...` | Win/Meta/Cmd/Command → super |
| `Ctrl+]` | `ctrl+bracketright` | Symbols to X11 names |
| `Ctrl+/` | `ctrl+slash` | |
| `Alt+up` / `Return` / `Esc` | `alt+Up` / `Return` / `Escape` | X11 capitalization |
| `F11` | `F11` | F1..F24 directly |
| Something unrecognized | (preserved as-is) | Slot marked as a TODO |

### Variants of `system.multimedia`

The firmware has a typo (`Volumn` sic). The converter accepts both:

| Original Hotkey | Bridge |
|---|---|
| `Volumn_Up` / `Volume_Up` | `volume up` |
| `Volumn_Down` / `Volume_Down` | `volume down` |
| `Mute` / `Volume_Mute` | `volume mute` |
| `PlayPause` / `Play_Pause` | `media play-pause` |
| `Next` / `Next_Track` | `media next` |
| `Previous` / `Previous_Track` | `media previous` |
| `Stop` / `Stop_Track` | `media stop` |
| `Play` / `Pause` | `media play` / `media pause` |

---

## 5. Links between profiles (rebuilding the structure)

Each `.ulanziDeckProfile` exports ONE complete profile. The slots that do
`page.switch` or `page.folder` point to OTHER profiles by `ProfileUUID`.
To rebuild cross-profile navigation the converter operates in **two
passes** over the entire set:

### Pass 1 — Discovery

1. Unzip all `.ulanziDeckProfile` in the input.
2. Read each one's root `manifest.json` and register:
   - `profile_uuid → {name, slug, ordered page_uuids, current_page_uuid}`.
3. Resolve slug collisions (`vscode`, `vscode_2`, …).
4. Decide the "home" slug:
   - `--default-profile NAME` if the user asked for it.
   - Otherwise, the profile named `Default Profile` (auto-detect).
   - If there is no detectable default, no profile is renamed to `home`
     and the bridge will enter the first alphabetical page (current
     behavior).
5. Assign `page_uuid → page_id`:
   - The profile's `Pages.Current` page → page_id = `<slug>`.
   - The rest, in the order of `Pages.Pages`, → `<slug>_2`, `<slug>_3`, …

### Pass 2 — Translation & write

1. For each slot, translate its `Action` according to the §4 table.
2. `page.next/page.prev` are resolved **explicitly** to the next /
   previous page_id of the **same profile** (with circular wrap). This is
   necessary because the current bridge resolves `page.next` by **global**
   alphabetical order, not per profile — if we left the literal
   `fw_action: page.next`, they would jump between profiles.
3. `page.switch`/`page.folder`/`page.goto` are resolved against the
   global mapping. If the destination ProfileUUID was not converted in
   this run → a `host_action: stub` is generated with the original UUID
   as `args`, **the converter does not fail**.
4. Icons:
   - `Images/<something>` inside the ZIP → copy to
     `config/icons/<slug>__<something>` (namespace per profile).
   - Absolute Windows path → look up the basename in the profile's own
     `Images/`; if it exists, copy; if not, leave the slot without `icon`
     and add a comment with the original path.
   - Factory icons (`btn_nextPage`, `btn_switchProfile`, etc.) → use the
     canonical name from the `config/icons/_firmware/` pack without
     copying.
   - SVG is never referenced as `icon:` (the bridge only processes PNG);
     the SVG is copied with a comment suggesting manual conversion, and
     the slot is left without an icon.
5. YAML writing: done by hand, line by line (PyYAML does not preserve
   comments). Each problematic slot carries **a single** commented line
   just above it with the untranslated data — minimal, so the user can
   search for them with `grep '^\s*#' config/pages/user/*.yaml`.

---

## 6. Error handling and "the untranslatable"

Principle: **the conversion never fails out of ignorance**. Whatever the
converter cannot translate always produces a functional slot + a pointer
for the user to fix.

| Case | Result |
|---|---|
| Hotkey with an unknown key | `keys: "<original>"` + comment "no full translation" |
| `system.open` with a `.exe` not listed in `_KNOWN_APPS` | `host_action: stub, command: app.open` + comment with the Win path |
| `system.open` with a listed `.exe` | real Linux `host_action: {type: app, match: ..., cmd: ...}` + informative comment with the original Win path |
| `page.switch` to an unconverted ProfileUUID | `host_action: stub` with UUID/Name in `args` |
| Completely unknown Action | `host_action: stub, command: <action-without-prefix>, args: <ActionParam>` |
| Absolute-path icon with no match | Slot without `icon` (bridge's transparent placeholder) + `text` + comment |
| SVG icon | Copied to `config/icons/` with a comment; slot without `icon` |
| Unexpected 12-byte header | Warning + attempt to parse as a pure ZIP |
| Duplicate profile UUID in the input | Warning, the duplicate is ignored |
| Nested sub-profile (folder) not exported | Like an unresolved `page.switch` → stub |

The bridge's `_h_stub` handler
([src/d200h/bridge.py](../../src/d200h/bridge.py)) shows, on press, a Tk
window with the slot's `command` and `args`, so the user knows what the
original was trying to do. If there is no display server or Tk is not
available → fallback to `notify-send`.

> Tip: if a slot ended up without `icon:` because the original PNG was
> not inside the profile's ZIP (absolute Windows path with no match), you
> do not have to draw a new PNG by hand. Replace the slot with
> `icon_generate: {text: "My label"}` and the bridge renders an automatic
> home-style icon at compile time. Detail in
> [../user/pages-guide.md §5.1](../user/pages-guide.md).

---

## 7. Known limitations

- **App→profile association (focus rules) is not imported**: the official
  Windows software supports "activate this profile when a specific app has
  focus", but that metadata lives in the software's global config, **not**
  inside the exported `.ulanziDeckProfile` (verified: the root
  `manifest.json` only carries `Device`, `Name`, `Icon`, `Pages`,
  `Version`). Result: `d200h convert` **cannot** generate
  [config/focus_rules.yaml](../../config/focus_rules.yaml) automatically.
  After converting, edit that file by hand using the slugs the converter
  reported (the "Discovered profiles" section of the output). Format and
  behavior in
  [../user/focus-pages.md](../user/focus-pages.md).
- **Large ZIPs**: the firmware accepts up to ~196 KB per page (seen in
  the USB capture of the switch profile). Profiles with many or very heavy
  icons can produce ZIPs above that threshold. If the slot does not paint
  after a press, that is probably the reason.
- **Sound**: `sound.play` / `sound.stop` are not implemented in the
  bridge; they stay as stubs.
- **Spotify**: native integration via the OAuth2 Web API. The converter
  translates all `spotify.*` to `host_action: {type: spotify, ...}` (see
  §4) and auto-extracts credentials from the first slot to
  `config/secrets/spotify.yaml`. To enable it the user must run
  `d200h spotify-auth` (OAuth, callback on `127.0.0.1:30901`). Requires a
  **Spotify Premium** account (the Web API rejects playback control on
  Free accounts → HTTP 403). The Windows profile's `accountId` is ignored
  — the API already resolves the account via the token.
- **Clock widget** (`smallwindow.window`): it is firmware-controlled; the
  bridge cannot change its mode. The slot is left decorative.
- **Truly nested sub-profiles**: in the official app the "folders" seem to
  be exported as separate `.ulanziDeckProfile` files (they do not appear
  embedded inside the parent ZIP in the analyzed samples). If in some
  future version they appear nested as
  `<parent>.ulanziProfile/Profiles/<folder-uuid>.ulanziProfile/...`, the
  current converter will ignore them — `_discover()` would need to be
  extended to recurse into `*.ulanziProfile` subdirectories.
- **Encoder**: the manifest always carries an empty
  `{"Type":"Encoder","Actions":{}}` on the D200H. If on other Ulanzi
  devices it comes populated, the current translation would ignore it.
