# Ulanzi D200H — Firmware protocol and behavior

> **Purpose**: how the D200H actually works. Everything stated here is
> verified against a USB capture of the official app (Ulanzi Studio) on
> Windows, or against inspection of the `UlanziDeckKey` binary. Anything
> NOT verified is marked `[PENDING]`.
>
> **Last revision**: 2026-05-18.
> **Hardware tested**: D200H VID:PID `2207:0019`, `bcdDevice=3.10`,
> firmware `UlanziDeckKey 2.0.3`.
>
> Companion: [firmware-dead-ends.md](firmware-dead-ends.md) collects
> wrong hypotheses, paths not to revisit, and attempted fixes that did
> not work.
>
> References to `_archive/research/...` in this doc (USB capture, RE
> scripts) are a **local audit trail** of the reverse-engineering
> process. That folder is NOT committed to the repo (`.gitignore`); the
> paths describe the provenance of each verified claim — if you clone the
> repo, those files will not be present.

---

## 1. Executive summary

The D200H is an **LCD control panel with no brain of its own for pages**:
the official app runs on the PC and sends it, over **HID vendor**
(EP 0x02 OUT / EP 0x81 IN), a **ZIP per screen change** containing a
`manifest.json` + PNG icons. When the user presses a slot, the firmware
emits an **HID IN report** with the slot id, and the host decides what to
do (change page = send the next ZIP, open a URL, run a hotkey, etc.).

Without a connected host, the firmware shows whatever it has in
`/tmp/standalone/manifest.json` (the factory page), and slots with
`Action: system.open` carrying pure HID tokens (`ctrlc`, `ctrls`, …)
still work because key emission is done by an independent **second HID
Boot Keyboard interface**.

Big implications:
- There are no "4 hardcoded pages", no "OS profiles that load different
  manifests", no 2×2 grid. Those models were wrong.
- Changing the page does **not** require killing `UlanziDeckKey`. The
  official app changes pages with no logo flash because it pushes a ZIP
  over HID.
- ADB is orthogonal to the control protocol. Useful for debugging (logs,
  LCD brightness via sysfs, pushing fallback manifests), but it is not
  the official app's channel.

---

## 2. Hardware

| Feature | Value |
|---|---|
| SoC | Rockchip RK3308 (ARM Cortex-A35) |
| RAM | 128–256 MB |
| OS | Buildroot 2021.11, Linux 5.10, `aarch32` userland |
| Display | non-touch LCD with 13 physical keys + 1 reserved double key |
| Internal app | `UlanziDeckKey` (Qt 5, `-platform linuxfb`) |
| USB | 2.0, single-config, 3 interfaces (see §3) |

---

## 3. USB interfaces and endpoints

`lsusb -v` (summary):

```
Bus 003 Device 004: ID 2207:0019 Fuzhou Rockchip Electronics Company
  Configuration 1, 3 interfaces
  iConfiguration = "hid1_adb_hid0_hid2"
  MaxPower 500mA, Bus Powered

  Interface 0: HID Interface (vendor iface, NOT boot!)
    bInterfaceClass    = 3 (HID)
    bInterfaceSubClass = 0
    bInterfaceProtocol = 0
    HID Descriptor bcdHID 1.01, Report length 35
    EP 0x81 IN  INT 1024 bytes  ← reports device→host (button events, ACKs)
    EP 0x02 OUT INT 1024 bytes  ← reports host→device (ZIP, clock, …)

  Interface 1: ADB Vendor Specific
    bInterfaceClass    = 255
    bInterfaceSubClass = 66
    bInterfaceProtocol = 1
    EP 0x04 OUT BULK 512
    EP 0x83 IN  BULK 512

  Interface 2: HID Boot Keyboard (autonomous)
    bInterfaceClass    = 3
    bInterfaceSubClass = 1 (Boot)
    bInterfaceProtocol = 1 (Keyboard)
    EP 0x85 IN  INT 8 bytes  ← keypresses emitted to the host (Ctrl+C, etc.)
    EP 0x06 OUT INT 8 bytes  ← LEDs/state (probably unused in practice)
```

**Official app control channel** = interface **0**, EPs **0x81 IN** /
**0x02 OUT**, reports of up to 1024 bytes.

**Hotkey-emission channel to the host** = interface **2** (HID Boot
Keyboard). This is what makes the `ctrlc`, `ctrls`, `screenshot`, etc.
tokens work without an Ulanzi app installed on the host.

**ADB channel** = interface 1, bulk. Allows a remote shell on the RK3308
(`adb shell`), file push, etc. The official app **does not use it to
control the device in normal use** (confirmed: zero ADB traffic
throughout the entire page-change capture).

---

## 4. HID vendor protocol (interface 0)

General framing:
- Reports of up to 1024 bytes.
- Every packet starts with the magic `7c 7c` (`||`).
- Bytes `[2..3]` = **message type**.
- Bytes `[4..7]` = payload length, little-endian (uint32).
- Bytes `[8..]` = payload.

Verified types:

| Type | Dir | Meaning |
|---|---|---|
| `00 01` | OUT | Page ZIP transfer (host sends manifest+icons) |
| `00 06` | OUT | Text command / clock |
| `01 01` | IN  | Slot press event (press and release) |
| `01 03` | IN  | Generic heartbeat / ACK from the device |
| `01 0b` | IN  | Specific ACK after receiving a complete ZIP |
| `03 03` | IN  | Device info (JSON `{"Dversion":"…"}`) |

Types seen as strings in the binary but **not observed on the wire**
(likely existing types — see §12 PENDING): `IconMessage`, `KeyAction`,
`BrightnessMessage`, `SmallWindowMessage`, `RunResultMessage`,
`UpdateSelfMessage`.

### 4.1 OUT type `00 01` — ZIP transfer (page load)

Pushes the entire contents of a page (manifest + icons) to the device as
a streamed ZIP.

```
Byte:  0  1  2  3   4  5  6  7   8 ... 1023
       7c 7c 00 01  <length LE 4B> <ZIP bytes ...>
```

- `length` = total ZIP size in bytes (uint32 little-endian).
- Starting at byte 8 comes the start of the ZIP, which begins with the
  `PK\x03\x04` signature.
- If the ZIP does not fit in the first report, **the following reports
  are RAW continuation bytes, with NO magic and NO header**. The host
  keeps sending 1024-byte reports until it has transmitted `length`
  bytes.

Example (cluster 1, "next page 2→3"):

```
Frame 49 OUT 1024B:
  7c 7c 00 01  91 19 00 00  50 4b 03 04 ... [1016 B of ZIP]
Frame 51 OUT 1024B: [1024 B of ZIP, no header]
Frame 53 OUT 1024B: [1024 B of ZIP]
Frame 55 OUT 1024B: [1024 B of ZIP]
Frame 57 OUT 1024B: [1024 B of ZIP]
Frame 59 OUT 1024B: [1024 B of ZIP]
Frame 61 OUT 1024B: [1024 B of ZIP, last 409 B useful + padding]
Total transferred: 0x1991 = 6545 bytes
```

After receiving it, the firmware extracts the ZIP into memory, reads the
`manifest.json`, and repaints the screen with the new slots and icons.
**It does not kill the process, there is no logo flash.**

### 4.2 OUT type `00 06` — Text command (clock)

Sends an ASCII payload with fields separated by `|`. In the entire
capture we only saw it used to update the clock painted in the reserved
slot `3_2+4_2`.

```
Byte:  0  1  2  3   4  5  6  7   8 ...
       7c 7c 00 06  <length LE 4B> <ASCII payload>
```

Example: `1|2|9|00:58:59|1|12H` (length 20).

Observed fields (partial interpretation):

| Pos | Value in capture | Probable meaning |
|---|---|---|
| 0 | `1` | version or "enable clock" `[PENDING]` |
| 1 | `2` | `[PENDING]` |
| 2 | `9` | `[PENDING]` |
| 3 | `HH:MM:SS` | current host time |
| 4 | `1` | `[PENDING]` |
| 5 | `12H` or `24H` | clock format (12h or 24h) |

The official app sends it **before every page change and every ~2 s when
idle** (clusters 0, 2, 5, 11 of the capture are only this command). It is
not strictly necessary for nav — the LCD shows the clock with or without
sync, but it drifts out of sync if not sent.

### 4.3 IN type `01 01` — Slot press event

The firmware sends this to the host when the user presses a physical key,
whatever `Action` that slot has in the active manifest.

```
Byte:  0  1  2  3   4  5  6  7   8         9         10  11  ...
       7c 7c 01 01  f6 03 00 00  01    <slot_id>     01  01  [zeros...]
```

- Bytes `[4..7]` = `f6 03 00 00` = 0x3f6 = **1014** decimal. Probable
  length of the remaining payload (1024 total - 10 header ≈ 1014).
  `[PENDING]` confirm interpretation.
- Byte `[8]` = **slot category**. Verified in session 2026-05-11:
  - `0x01` = regular slot (the 13 numbered slots 0..12 of the 5×3 grid)
  - `0x00` = double clock/logo slot (`3_2`+`4_2`), reported as id 13

  This resolves the v2 question about the "14th keycode" of the
  matrix-keypad driver: it exists physically and it is the clock slot,
  which IS pressable and emits an IN.
- Byte `[9]` = **`slot_id`**. For regular slots = `row * 5 + col`
  (0..12). For the clock slot = `13` (`0x0d`). Verified across the 14
  physical slots of the device:

  | Pressed slot (`row_col` key) | `slot_id` | byte[8] | byte[9] |
  |---|---|---|---|
  | `0_0` | 0 | `0x01` | `0x00` ✓ |
  | `0_1` | 1 | `0x01` | `0x01` ✓ |
  | `0_2` | 2 | `0x01` | `0x02` ✓ |
  | `0_3` | 3 | `0x01` | `0x03` ✓ |
  | `0_4` | 4 | `0x01` | `0x04` ✓ |
  | `1_0` | 5 | `0x01` | `0x05` ✓ |
  | `1_1` | 6 | `0x01` | `0x06` ✓ |
  | `1_2` | 7 | `0x01` | `0x07` ✓ |
  | `1_3` | 8 | `0x01` | `0x08` ✓ |
  | `1_4` | 9 | `0x01` | `0x09` ✓ |
  | `2_0` | 10 | `0x01` | `0x0a` ✓ |
  | `2_1` | 11 | `0x01` | `0x0b` ✓ |
  | `2_2` | 12 | `0x01` | `0x0c` ✓ |
  | `2_3`+`2_4` (clock) | 13 | **`0x00`** | `0x0d` ✓ |

- Byte `[10]` = `0x01` constant. Probable "valid event" flag.
- Byte `[11]` = **press/release**: `0x01` = press, `0x00` = release.
  Verified in session 2026-05-11 with press+release of the same slot.
- Rest = zeros.

**Critical**: the firmware **does not distinguish** "this slot is
page.next vs system.website" when emitting the IN. Every pressed slot
emits the same event type with its `slot_id`. It is the **host's**
responsibility to look at its copy of the active manifest and decide what
to do:
- If the pressed slot's `Action` is
  `page.next`/`prev`/`goto`/`folder`/`back`/`switch` → the host computes
  the destination page and sends it the ZIP.
- If the `Action` is `system.website` → the host opens the URL (with
  xdg-open on Linux).
- If the `Action` is `system.open` with a pure HID token (`ctrlc`, etc.)
  → the firmware **already emitted it on its own** via the HID Boot
  Keyboard (interface 2). The host does not need to do anything — even
  though the IN also arrives, it can be ignored.
- If the `Action` is `system.open` with an app name (`firefox`,
  `calc.app`) → on Win/Mac the firmware opens Win+R/Spotlight and types;
  on Linux this does not work and the host should intercept it by running
  the command.

### 4.4 Other observed IN types (session 2026-05-11)

When the host sends the first command, the device responds with several
messages of different types:

- **`01 03`** — device ACK / heartbeat. Payload almost all zeros. Arrives
  periodically (~1/s) and also as a response to the clock OUT.
- **`01 0b`** — "ZIP received" ACK. Arrives after sending the last chunk
  of a `7c7c 0001` transfer. Payload all zeros.
- **`03 03`** — device info, JSON. Arrives as the first response to the
  handshake. Typical payload: `{"Dversion":"2.0..."}` (full version saved
  in `_archive/research/experiments2/send_zip_dump.txt`). `[PENDING]`
  decode all the JSON fields.

Pattern observed on connect:
```
host  →  device:  clock OUT (7c7c 0006 ...)
device→  host:    info JSON (7c7c 0303 ...)
device→  host:    heartbeat (7c7c 0103 ...) every ~1s
host  →  device:  page ZIP (7c7c 0001 + chunks)
device→  host:    ZIP ACK (7c7c 010b ...)
device→  host:    [user presses] (7c7c 0101 ...)
host  →  device:  clock OUT every ~2s (keepalive)
```

> **Critical — drain the IN before the first ZIP (session 2026-05-15)**.
> If the host sends the ZIP without having first **read** the `0303`
> device-info that arrives after the clock, the firmware **accepts the
> ZIP at the HID level** (returns a normal `010b` ACK) **but does NOT
> render it**. The screen stays on the logo and after ~10 s the firmware
> logs
> `[warning] have a error: "Host information not received, requesting reconnection"`
> and enters screen-off. This is not a firmware bug: order matters
> because the handshake is considered complete only once the host has
> consumed the `0303`.
>
> The bridge must **read() for ~250 ms** after sending the clock before
> starting to send ZIP chunks. The script
> `_archive/research/experiments2/send_zip.py` already does this (drains 5
> reads of 50 ms), which is why it changes the screen correctly.

### 4.4.1 ZIP unpacking in the firmware (session 2026-05-17)

Confirmed by reading the device's `/userdata/logs/log.txt` in parallel
with `adb shell tail -f` while the bridge sends ZIPs:

1. The firmware persists the full payload of the HID OUT `0001` to
   `/tmp/temp.zip` (its size always matches the `length` declared in the
   first report's header).
2. It unzips it with `busybox unzip` into `/tmp/icon/`.
3. It reads `/tmp/icon/manifest.json` and repaints the screen.

Relevant log messages:

- Success: no specific line (the render simply happens).
- Unzip failure: `unzip fail: "unzip: short read\n"` →
  `"/tmp/icon/manifest.json" open fail` → `send getbase message` →
  `send success id( 3 ): ""`. When this happens, the firmware ACKs the
  ZIP at the HID level (`010b`) but **does not render** — the screen
  keeps the previous content.
- Render failure due to a missing icon:
  `keybutton pixmap is null icon path: "/tmp/icon/<path>"`.

**Diagnostic implication**: `adb pull /tmp/temp.zip` right after a
page-change gives the **exact** ZIP the firmware received and persisted.
If it differs from the ZIP the bridge sent, the corruption is in HID
transmission; if it is identical but still fails, it is a manifest or
icon problem.

### 4.4.2 Intermittent loss of 1 byte in HID transmission — RESOLVED 2026-05-18

> **Status**: resolved. Cause: Report ID byte omitted in `os.write` to
> `/dev/hidraw*`. See §4.4.3 for the detail and the definitive fix. This
> subsection is kept as a historical reference of the symptom.

Symptom observed (previous sessions, before the fix):

- When sending a ZIP fragmented across >1 HID report, the firmware
  **occasionally lost 1 byte** of the stream. The lost byte was always
  `0x00` and appeared near the transition between chunks 4 and 5 (offsets
  4085, 4088 and 4092 seen in different cases).
- The total received size still equaled the declared `length`: the rest
  of the bytes shifted 1 position left and an extra `0x00` was left at the
  end.
- Consequence: the ZIP central directory was misaligned and busybox unzip
  failed with `short read`.
- The loss was **dependent on the ZIP content** (deflate), which led to
  false "manifest content" hypotheses.

The behavior was NOT a firmware loss. The Linux kernel was consuming the
first byte of each `os.write` as the HID Report ID; when that byte
happened to be `0x00`, the "loss" was unnoticed; when it was something
else, the report was sent one byte short and the entire ZIP was shifted.
See §4.4.3.

### 4.4.3 Semantics of `write()` to `/dev/hidraw*` on Linux (CRITICAL)

Root cause of the historical bug §4.4.2 and of any future project that
talks to the D200H via raw hidraw.

**Kernel rule** ([Documentation/hid/hidraw.rst][hidraw-doc]): the first
byte of the buffer passed to `write()` is the Report ID. If the device
descriptor is **unnumbered** (it has no `0x85` tag), that first byte must
be `0x00` and the real report starts at byte[1].

The D200H iface 0 descriptor is **unnumbered** (verified:
`cat /sys/class/hidraw/hidrawN/device/report_descriptor | xxd` — no
`0x85` byte anywhere). Therefore every `write()` from the host to the
device must be **1025 bytes**:

```
buffer = b"\x00" + report_1024B   # 1 ID byte + 1024 report bytes
os.write(fd, buffer)              # must return 1025
```

If you omit the `\x00` and write 1024 bytes directly:
- byte[0] of the payload (the `0x7c` of the `||` magic in chunk 0, or
  arbitrary ZIP bytes in continuation chunks) is interpreted by the
  kernel as the Report ID.
- The kernel intermittently rewrites the USB frame and the device
  receives a report with **1 byte fewer** than expected, which misaligns
  the streamed ZIP and breaks the unzip in `/tmp/temp.zip`.

This explains why the official Windows app never had this problem (the
Windows HID stack does not use this prepend convention), and why projects
like `python-elgato-streamdeck` do not suffer it (they use `hidapi`,
which prepends the Report ID automatically).

The fix is implemented in [src/d200h/hid.py][hid-py] in
`HidClient.write_raw`. It only affects writes; `os.read` calls still
return the report as-is (on unnumbered devices no Report ID arrives in
the read).

Verified 2026-05-18: with the fix applied, a 190,237 B page (186 reports,
near the firmware's limit) renders on the first attempt, and `page.goto`
across 19 pages (including chains like
`emojis`→`emojis_2`→`emojis_3`→`emojis_4` and heavy profiles such as
`windows_11_essentials` with 35 KB icons) works without failures.

[hidraw-doc]: https://github.com/torvalds/linux/blob/master/Documentation/hid/hidraw.rst
[hid-py]: ../../src/d200h/hid.py

### 4.5 Behavior on host disconnect

When the host starts sending commands over HID, the firmware
**discards** what it had on screen (the `/tmp/standalone/` fallback page)
and enters "host-managed" mode: it shows the manufacturer logo while it
waits for the first ZIP. After rendering the ZIP, it stays in
host-managed mode while keepalive keeps arriving.

If keepalive stops arriving (session 2026-05-15):
- After ~10 s with no valid host activity, the firmware logs
  `Host information not received, requesting reconnection` and puts the
  screen into `screen off` (showing `:/resource/icon/wallpaper.png`, the
  Ulanzi logo).
- The firmware **does NOT retain the last page sent** when it detects
  inactivity. On losing the host it returns to the logo, not to the last
  useful screen. The firmware's standalone mechanism (`/tmp/standalone/`,
  §8) is the only thing that can leave a screen up without a host, but it
  is volatile (restored to factory on every reboot).

**Cold USB reconnect (unplug → replug)**:
- The firmware re-initializes its side of the USB; the host loses the
  `hidraw` fd.
- After re-enumerating, the firmware **does not load any page** on its
  own (it stays on the logo). It needs the host to redo the full
  handshake (clock + drain + ZIP).
- Resumption: relaunch `d200h bridge` and the screen returns to the
  configured state. The bridge already has `reconnect_delays` to retry
  automatically if the `/dev/hidraw*` shows up with a different number.

---

## 5. Catalog of Actions supported by the firmware

All verified in real ZIPs sent by the official app. Listed without the
common prefix for brevity (they all start with
`com.ulanzi.ulanzideck.`).

### 5.1 Page Actions (the keys to having no logo flash)

| Action | `ActionParam` | Function |
|---|---|---|
| `page.next` | `{}` | Advance to the next manifest of the active profile |
| `page.prev` | `{}` | Go back to the previous one |
| `page.goto` | `{Page: N}` | Absolute jump to page N (1-indexed) |
| `page.folder` | `{ProfileUUID: "<uuid>"}` | Enter the nested folder identified by its UUID |
| `page.back` | `{}` | Exit the folder and return to the parent |
| `page.switch` | `{Profile: "<name>", ProfileUUID: "<uuid>"}` | Switch to a different full profile |
| `page.indicator` | `{}` + `ViewParam[0].Text = "<n>"` | Passive slot: only shows "Page N". No action on press (`[PENDING]` confirm) |

> Important: the firmware **does not implement the logic** of "what the
> next page is". It only emits the IN when the slot is pressed; the HOST
> knows the list of pages and sends the corresponding ZIP.

### 5.2 System Actions (actions the firmware emits toward the host)

| Action | `ActionParam` | How it executes |
|---|---|---|
| `system.open` | `{Path: "<string>"}` | The firmware has an internal table; see §6 |
| `system.website` | `{Url: "<http(s)://...>"}` | The host must open the URL (xdg-open on Linux); the firmware cannot open URLs by itself |
| `sound.play` | `{}` | `[PENDING]` parameters (path to a WAV embedded in the ZIP, probably) |
| `sound.stop` | `{}` | Stop audio |

### 5.3 Other Actions identified in binary strings

Seen in `strings UlanziDeckKey` but NOT observed in the capture.
`[PENDING]` confirm payload and real behavior:

- `com.ulanzi.ulanzideck.keyboard.send` — historically documented, tested
  as **not implemented** in 2.0.3 (slot silently ignored on press).
- `com.ulanzi.ulanzideck.shell.exec` — same.

> Do not use these two in new manifests. To emit keys: use `system.open`
> with pure HID tokens (see §6.1), or have the host emit them with
> `ydotool`/`xdotool` on receiving the IN event.

### 5.4 Full structure of a slot (manifest.json format)

```json
{
  "1_0": {
    "Action": "com.ulanzi.ulanzideck.page.next",
    "ActionID": "80ce174c-662c-43c9-94fe-7b26fc946a63",
    "ActionParam": {},
    "LinkedTitle": true,
    "Name": "Next page",
    "State": 0,
    "ViewParam": [
      {
        "Icon": "com.ulanzi.deck.page/Images/btn_nextPage.png",
        "IconDef": "C:/Users/odin/AppData/Roaming/Ulanzi/UlanziDeck/.../btn_nextPage.png"
      }
    ]
  }
}
```

Fields:

| Field | Type | Notes |
|---|---|---|
| `"col_row"` | key | col 0-4, row 0-2. `3_2` and `4_2` reserved to the firmware (clock/logo); do not include. **This is the WIRE convention** (the real `manifest.json` that travels to the device over HID). The user's YAML in this bridge uses the inverse convention `"row_col"` (row first); the bridge translates row_col → col_row when building the manifest |
| `Action` | string | One of those in §5 |
| `ActionID` | string (UUID) | Optional; identifies the instance. May be omitted or set to a random one |
| `ActionParam` | object | Action parameters. Empty `{}` if not applicable |
| `LinkedTitle` | bool | `true` in factory. `[PENDING]` what it does |
| `Name` | string | Descriptive only, not shown. Optional |
| `State` | int | 0 (default) or 1 (pressed). Almost always 0 |
| `ViewParam` | array | Visual configuration; always use index 0 |
| `ViewParam[0].Icon` | string | Path to the PNG **inside the ZIP** (e.g. `com.ulanzi.deck.page/Images/foo.png` or `Images/foo.png`) |
| `ViewParam[0].IconDef` | string | Original path on the author's system; the firmware ignores it, may be omitted |
| `ViewParam[0].Text` | string | Text over the icon (optional) |
| `ViewParam[0].Font` | object | Text style (`Align`, `Color`, `FontName`, `FontSize`, `Size`, `Weight`, `ShowTitle`) — used e.g. in `page.indicator` |

### 5.4.1 Empty slots: do NOT include in the manifest (session 2026-05-15)

**Confirmed experimentally**: the official app **omits from the JSON**
the slots it does not use (seen in `cluster03_frame71_len3770.zip`: only 4
entries in the manifest for 4 configured slots; the remaining 9 do not
appear). The firmware accepts a manifest with gaps perfectly fine.

**Bug detected and fixed**: filling empty slots with an `empty_slot()`
(Action=system.open, ActionParam={Path:""}, ViewParam with `Icon: ""`)
**breaks the render on page-changes**. The initial handshake
(clock+drain+ZIP) tolerates it and the page paints; but on changing page
after a press, the firmware ACKs the ZIP at the HID level (`010b`) and
**does not apply the new page**. The screen stays on the previous page
and the bridge thinks the press "did nothing".

Resolution: `manifest.build()` no longer fills empty slots
([src/d200h/manifest.py](../../src/d200h/manifest.py)). If a YAML slot
has no entry, it does not appear in the JSON. This fixes the
`page=home → page.goto apps` case: previously apps had 13 slots in its
manifest (12 real + 1 empty_slot for `2_2`), afterwards it has 12 and
renders correctly.

### 5.5 ZIP structure

The ZIP sent over HID has this shape:

```
manifest.json                                  ← mandatory
com.ulanzi.deck.page/Images/btn_nextPage.png   ← icons referenced from the manifest
com.ulanzi.deck.page/Images/btn_prevPage.png
com.ulanzi.deck.sound/Images/btn_playAudio.png
Images/<sha or free name>.png                  ← also accepts a flat "Images/" prefix
```

The icon path in `manifest.json` must be **identical** to the path in the
ZIP. Conventions the official app uses:

- For "system" button icons (next/prev/goto/folder/…):
  `com.ulanzi.deck.page/Images/<name>.png`
- For audio icons: `com.ulanzi.deck.sound/Images/<name>.png`
- For user / website icons: `Images/<hash>.png` or `Images/<name>.png`

"Folders" in the ZIP are entries with `file_size=0` and a name ending in
`/`. Some extractions work without those explicit entries, but it is best
to keep them for compatibility.

---

## 6. `Action: system.open` and `keyMode`

### 6.1 Pure HID tokens (universal)

When `Path` is one of these tokens, the firmware **emits the equivalent
HID combination to the host** via interface 2 (HID Boot Keyboard). They
work with or without an Ulanzi app on the host.

| Token | Combination | Mac token | Combination |
|---|---|---|---|
| `ctrlc` | Ctrl+C | `maccopy` | Cmd+C |
| `ctrlv` | Ctrl+V | `macpaste` | Cmd+V |
| `ctrlx` | Ctrl+X | `maccut` | Cmd+X |
| `ctrls` | Ctrl+S | `macsave` | Cmd+S |
| `ctrlz` | Ctrl+Z | `maccancel` | Cmd+Z |
| `ctrly` | Ctrl+Y | `macredo` | Cmd+Shift+Z |
| `ctrla` | Ctrl+A | `macctla` | Cmd+A |
| `ctrlf` | Ctrl+F | `macsearch` | Cmd+F |
| `screenshot` | Win+Shift+S / PrtScn | `macscreenshot` | Cmd+Shift+3 |
| `pscreenshot` | PrintScreen | `macpscreenshot` | Cmd+Shift+4 |
| `macmultitask` | Mac Mission Control |  |  |

There may be more. To enumerate:
`adb shell "strings /userdata/UlanziDeckKey | grep -E 'ctrl|mac' | sort -u"`.

### 6.2 Apps by launcher (depends on `keyMode`)

`/userdata/keyMode` (3 ASCII bytes):

- `win`: the firmware opens Win+R, types the `Path`, presses Enter.
- `mac`: the firmware uses Spotlight (Cmd+Space), types, Enter.

Known tokens:

- `keyMode=win`: `calc`, `cmd`, `notepad`, `wordpad`, `control`,
  `perfmon`, `devmgmt.msc`, `powershell`, `explorer`, `taskmgr` (any
  Windows Run command).
- `keyMode=mac`: `safari.app`, `mail.app`, `finder.app`, `app store.app`,
  `music.app`, `calendar.app`, `notes.app`, `keynote.app`, `numbers.app`,
  `pages.app`.

> `linux` is NOT an officially supported value. If you write `linux` the
> firmware falls back to Win and ends up typing `rPath` in the active
> window. **On Linux, leave `keyMode=win`**, and for anything that is not
> a pure HID token use the host bridge (on receiving the IN with
> `slot_id`, run the desired command).

```bash
adb shell "echo -n 'win' > /userdata/keyMode"
adb shell "echo -n 'mac' > /userdata/keyMode"
```

---

## 7. Physical button layout

Diagram with the **bridge YAML** keys (`"row_col"`, row first):

```
┌──────┬──────┬──────┬──────┬──────┐
│ 0_0  │ 0_1  │ 0_2  │ 0_3  │ 0_4  │  ← row 0 (top)
├──────┼──────┼──────┼──────┼──────┤
│ 1_0  │ 1_1  │ 1_2  │ 1_3  │ 1_4  │  ← row 1 (middle)
├──────┼──────┼──────┼──────┼──────┤
│ 2_0  │ 2_1  │ 2_2  │  2_3 + 2_4  │  ← row 2 + double slot reserved to the firmware
└──────┴──────┴──────┴─────────────┘
```

- 13 configurable slots (`0_0..0_4`, `1_0..1_4`, `2_0`, `2_1`, `2_2`).
- 1 double slot (YAML `2_3`+`2_4`, wire `3_2`+`4_2`) reserved to the
  firmware for clock/logo. **Do not include** those keys.
- Two conventions:
  - **Bridge YAML** (this project): `"row_col"` (row first). This is what
    you edit in `config/pages/user/*.yaml`.
  - **Wire / real `manifest.json`**: `"col_row"` (col first) — the
    official Windows software convention and what the firmware parses.
    The bridge translates row_col → col_row when building the manifest,
    so the user never touches col_row.
- `slot_id` for the HID IN event: `id = row * 5 + col` (the same in both
  conventions; the physical id does not depend on how the key is
  written).

### 7.1 Kernel SCAN codes (only relevant if accessing the device via ADB+evtest, not via HID)

Each button emits a unique `MSC_SCAN` over `/dev/input/event0`.
Deterministic, independent of the loaded manifest.

Keys in `row_col` convention (row first):

| Slot | id | SCAN | Slot | id | SCAN | Slot | id | SCAN |
|---|---|---|---|---|---|---|---|---|
| `0_0` | 0 | `0x0c` | `1_0` | 5 | `0x04` | `2_0` | 10 | `0x14` |
| `0_1` | 1 | `0x0b` | `1_1` | 6 | `0x03` | `2_1` | 11 | `0x13` |
| `0_2` | 2 | `0x0a` | `1_2` | 7 | `0x02` | `2_2` | 12 | `0x12` |
| `0_3` | 3 | `0x09` | `1_3` | 8 | `0x01` | | | |
| `0_4` | 4 | `0x08` | `1_4` | 9 | `0x00` | | | |

> The matrix-keypad driver declares 14 keycodes. The 14th is the
> **pressable zone of the double clock slot** (`2_3`+`2_4`). Confirmed in
> session 2026-05-11: pressing the clock zone yields an IN of type `0101`
> with byte[8]=`0x00` (special category) and byte[9]=`0x0d`
> (slot_id=13). That is, **the clock IS interactive** — the official app
> could use that press for something (not observed in capture yet).

If you use the HID vendor channel (recommended), **evtest is not needed**:
the `slot_id` already comes in byte[9] of the IN report.

---

## 8. Device filesystem

```
/userdata/
├── UlanziDeckKey         ← Qt firmware binary
├── keyMode               ← "win" | "mac" (3 ASCII bytes)
├── logs/log.txt          ← app logs
└── wallpaper/            ← optional backgrounds

/tmp/standalone/          ← VOLATILE. Regenerated on every boot
├── manifest.json         ← default page with no connected host
├── manifest1.json        ← factory extras
├── manifest2.json        ←   "
├── manifest3.json        ←   "
└── Images/               ← factory icons
```

**The `/userdata/UlanziDeckKey` binary contains an embedded ZIP**
(offsets ~309341..1796861, 49 files: 4 manifests + 45 PNGs). On every
boot the firmware extracts that ZIP into `/tmp/standalone/`. That is why
manual changes to `/tmp/standalone/` are lost on reboot.

**Relevance of `/tmp/standalone/` now**: with the new HID bridge, it is
**not our control channel**. It only determines what is painted when
there is NO connected host. Useful for having a "default screen" with
basic hotkeys (HID tokens) in case the PC disconnects.

### 8.1 Firmware limitations regarding `/tmp/standalone/`

- **No inotify** on `/tmp/standalone/`
  (`ls /proc/$PID/fd | grep inotify` → empty). If you rewrite the
  manifest while it runs, it does NOT detect it.
- **Does not handle SIGHUP** (`SigCgt = 0x1000a4002`, bit 0 clear).
  `killall -HUP UlanziDeckKey` KILLS it, does not reload.
- `WatcherProcess` (init script) relaunches it ~1-2 s after a death. That
  is the "logo flash" we saw with the old bridge.
- **Conclusion**: for live changes you must use the HID channel. For
  fallback changes, push the manifest to `/tmp/standalone/` + reboot.

### 8.2 D200H LCD brightness

- Node: `/sys/class/backlight/backlight/brightness`, range
  0..`max_brightness` (=255 in 2.0.3).
- Direct write with `adb shell` works (root). Immediate.
- There is also an internal `BrightnessMessage` over HID (seen in
  `strings UlanziDeckKey`). `[PENDING]` decode the exact command to do it
  over HID without going through ADB.

**Verified 2026-05-20 — behavior with `brightness=0`**:

- Writing `0` to sysfs turns off the backlight: the LCD goes
  **completely black**, visually indistinguishable from a disconnected
  device.
- The **firmware keeps processing keys**: every physical press is still
  sent as a normal IN report (`0x0101` with `slot_id`) over HID. There is
  NO internal "suspended" mode that silences events when the backlight is
  turned off — keyboard scanning is independent of the panel state.
- Consequently, "brightness 0" only turns off the light; the host can use
  any press to detect user interaction (e.g. the bridge uses the first
  press as a "wake" signal and restores the brightness).
- The firmware does **not** restore brightness on its own when it
  receives a press; that is the host's responsibility. If nobody raises
  the brightness, the LCD stays off indefinitely even if you press every
  key.
- The internal clock widget (`slot_id=13`, slots 2_3+2_4) is also not
  painted when `brightness=0` — it is just another LCD component, not an
  independent segment display.

This underpins the bridge's "standby mode" implementation (see
[architecture.md §4.bis](architecture.md) and
[../user/pages-guide.md §4.7](../user/pages-guide.md)).

---

## 9. Press detection (the 2 alternatives)

### 9.1 Via HID vendor (recommended — official protocol)

Read from the PC the `/dev/hidraw*` corresponding to interface 0 of the
device. Each press arrives as an IN report of up to 1024 bytes with the
structure of §4.3. byte[9] indicates the `slot_id`.

Advantages: it is the official channel, requires no ADB, the screen
change produces no logo flash (because it is done via HID OUT), and it
allows sending arbitrary ZIPs.

### 9.2 Via ADB + `evtest` (legacy of the old project)

Connect to the device's `/dev/input/event0` via ADB and parse `MSC_SCAN`
codes (see §7.1). It works, but:

- It needs ADB always active.
- You only learn the **slot** pressed, not the Action it had configured
  (you need to keep your own copy of the active manifest).
- Changing the page requires rewriting `manifest.json` + killing the
  process = guaranteed logo flash.

**Migrate to §9.1.**

---

## 10. Icons

| Requirement | Value |
|---|---|
| Format | PNG (RGBA optional) |
| Resolution | **196×196** px (official for the LCD keys). Smaller → rendered as a solid-color square with fallback text instead of the PNG |
| Size | Factory ones weigh 30–40 KB. Do not compress aggressively |
| Name | No spaces, ASCII, `.png` extension |
| Location inside the ZIP | The path must EXACTLY match the slot's `ViewParam[0].Icon` |

Quick conversion with Pillow:

```python
from PIL import Image
img = Image.open(src).convert("RGBA")
if img.size != (196, 196):
    img = img.resize((196, 196), Image.Resampling.LANCZOS)
img.save(dst, "PNG")  # no aggressive optimize
```

---

## 11. How to identify the correct `/dev/hidraw*` on the Linux PC

The device exposes two hidraw nodes to the host (interfaces 0 and 2). The
one that serves our protocol is interface 0 (vendor, 1024 B reports).

Robust discrimination without depending on fixed numbers:

```bash
for h in /sys/class/hidraw/hidraw*; do
  dev="${h##*/}"; ifdir="$(dirname "$(readlink -f "$h/device")")"
  if=$(<"$ifdir/bInterfaceNumber")
  vid=$(<"$ifdir"/../idVendor) 2>/dev/null
  pid=$(<"$ifdir"/../idProduct) 2>/dev/null
  # you can also read from /sys/class/hidraw/$dev/device/uevent (HID_ID)
  echo "$dev iface=$if vid=$vid pid=$pid"
done | grep -i '2207.*0019' | awk '$2=="iface=00"'
```

Confirmed in session 2026-05-11: the interface 0 report descriptor is
`Usage Page 0x0c (Consumer)`, `Usage 0x01`, `Report Size 16 bits`,
`Report Count 0x200 = 512` → 1024 bytes per report (in and out). The
interface 2 one is the typical 8-byte HID Boot Keyboard.

> Confirmation 2026-05-15: **writing to the interface 2 `/dev/hidraw*`
> fails with `[Errno 110] ETIMEDOUT`**. That interface only emits reports
> (the pure HID tokens like `ctrlc`/`ctrls` that the firmware generates
> in `keyMode=win|mac`). Every bridge write (clock, ZIP, future messages)
> goes through **iface 0**.

### 11.1 Permissions / udev

The repo's current rule `99-ulanzi-d200h.rules`:

```
SUBSYSTEM=="usb", ATTR{idVendor}=="2207", ATTR{idProduct}=="0019",
    MODE="0666", TAG+="uaccess"
```

It applies to the USB device and, as a side effect of `TAG+="uaccess"`,
leaves the `/dev/hidraw*` nodes with `crw-rw-rw-` permissions accessible
to any logged-in user. Verified in session 2026-05-11.

To make it more explicit it is worth adding:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2207", ATTRS{idProduct}=="0019",
    MODE="0666", TAG+="uaccess"
```

After editing:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## 12. Items pending verification `[PENDING]`

Things not yet verified that should be confirmed before depending on
them. What leaves this list is already consolidated in specific sections.

1. **Other OUT types** (`00 02`, `00 03`, `00 04`, `00 05`, `00 07`,
   `00 08`, …). The binary mentions these message types by name — we
   would need to map which wire format each uses:
   - **BrightnessMessage** — change the LCD brightness over HID. An
     alternative to sysfs `/sys/class/backlight/...` via ADB.
   - **IconMessage / KeyAction** — probably to repaint ONE slot without
     resending the full ZIP. Useful for fast updates (timer, counters).
     For the initial bridge it is NOT necessary; the full ZIP can be
     resent.
   - **SmallWindowMessage** — control the clock-slot widget (CPU, audio
     status, custom logo, etc.). If we solve it, we can change the clock
     for something else.
   - **UpdateSelfMessage** — firmware OTA. Dangerous to touch.
   - **RunResultMessage** — device IN reporting the result of an action.
     We have not seen one in capture yet.
2. **Full JSON of the `0303`** (device info). We only captured the first
   24 bytes (`{"Dversion":"2.0`). The rest was lost and to recover it you
   must power-cycle the device and re-run the cold probe.
3. ~~**Behavior if we stop sending keepalive**~~ Confirmed 2026-05-15:
   timeout ~10 s → `screen off` with logo. USB unplug/replug
   re-initializes cleanly: the host must relaunch the handshake. See
   §4.5.
4. **Clock command fields** positions 0, 1, 2 and 4 (`1|2|9|...|1|12H`).
   Hypothesis: timezone, day-of-week, format, locale. Does not block
   anything; the clock only needs HH:MM:SS to look correct.
5. **Path for audio** in `sound.play`/`sound.stop`: probably an
   `ActionParam` field indicating the WAV inside the ZIP. The capture has
   no example with audio played.
6. **Does `page.indicator` emit an IN on press?** Yes it does — confirmed
   session 2026-05-11 with slot id=4. It is an electrically normal slot;
   the "does nothing" is the host's responsibility (ignore the press in
   its mapping).
7. **`LinkedTitle: true`** — field in all factory manifests. We do not
   know what it does or whether it affects rendering.
8. **Behavior on pressing the clock slot (id=13)** in the official app.
   The original USB capture does not include that press. Hypothesis: it
   changes the widget shown in the clock (CPU↔clock↔logo).
9. **Maximum ZIP size (initial handshake): ~196 KB** — *confirmed
   experimentally 2026-05-15*. Larger ZIPs are discarded **silently**:
   the firmware emits the `010b` ACK as if all were well but does NOT
   render. A 199,642 B ZIP failed; a 190,268 B ZIP (same manifest, same
   icons, only recompressed with
   `Image.save(..., optimize=True, compress_level=9)`) rendered.
   **Related suspicion (unconfirmed)**: that `Image.quantize` of icons
   >15 KB is needed to go lower. It was applied in `convert.py` but it
   has NOT been isolated whether it helps on its own or whether
   `optimize=True` was enough. What is a fact: at ~196 KB the firmware
   stops rendering on the initial send.
10. ~~**Selective page-change: the firmware rejects some ZIPs on
    page-change**~~. **Resolved 2026-05-18**: the real cause was the
    Report ID byte omitted in `os.write` to `/dev/hidraw*` — see §4.4.3.
    The firmware was never at fault; the byte shift was induced by the
    Linux kernel consuming the first byte of each write as the Report ID.

---

## 13. Quick glossary

- **Manifest**: JSON file with the configuration of the 13 slots of ONE
  page/screen. Goes inside the ZIP the host sends over HID.
- **Slot / Key**: a configurable cell of the screen. Two key conventions
  in this project: the **bridge YAML** uses `"row_col"` (row first), but
  the **real `manifest.json`** that travels to the firmware uses
  `"col_row"` (col first, the official Windows software convention). The
  bridge translates row_col → col_row when building the manifest.
  Physical id: `row*5+col` (identical in both).
- **Profile**: a set of related pages. Identified by `ProfileUUID`. The
  official app handles several profiles and allows switching between them
  with `page.switch`.
- **Page**: within a profile. Numbered 1..N. Navigated with
  `page.next`/`page.prev`/`page.goto`.
- **Folder**: a profile nested inside another page. Entered with
  `page.folder`, exited with `page.back`.
- **Action**: JSON field that indicates what the slot represents.
- **Page ZIP**: binary package with `manifest.json` + icons that the host
  pushes to the device via HID OUT type `0001`.
- **Logo flash**: the manufacturer-logo flicker that happens when
  `UlanziDeckKey` is killed and `WatcherProcess` relaunches it. With the
  new HID bridge it does NOT happen, because the process is not killed.

---

## 14. Recommended architecture for the bridge

This section describes **how to implement** a user project on top of the
protocol. An agent arriving for the first time can build the bridge from
scratch following this.

### 14.1 Minimal components

```
┌─────────────────────────────────────────────────────────────┐
│                    bridge (host process)                    │
│                                                             │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │ HID client │   │  Page Store  │   │  Action runner   │   │
│  │ /dev/hidraw│   │  pages[N]    │   │  shell/url/keys  │   │
│  └─────┬──────┘   └──────┬───────┘   └────────┬─────────┘   │
│        │                 │                    │             │
│        │ IN events       │ active page id     │             │
│        ▼                 ▼                    │             │
│   ┌──────────────────────────────────────────┴────────┐     │
│   │              Dispatcher                            │    │
│   │  (page, slot_id) → firmware action or host action?│    │
│   └────────────────────────────────────────────────────┘    │
│        │                                                    │
│        │ "change to page N"            "run shell X"        │
│        ▼                                                    │
│  ┌────────────┐                                             │
│  │ HID client │ ←── sends new ZIP (page.next/goto/etc.)     │
│  └────────────┘                                             │
└─────────────────────────────────────────────────────────────┘
```

### 14.2 Main loop

```python
hid = HidClient("/dev/hidraw2")
pages = PageStore.from_yaml_dir("config/pages/")  # compile YAMLs → ZIPs
hid.send_clock()                                  # handshake

# CRITICAL: drain the 0303 device-info + initial heartbeats before the
# first ZIP. If you don't, the firmware ACKs the ZIP at the HID level but
# does NOT render it (stays on the logo and after ~10s requests reconnect).
# See §4.4.
drain_until = now() + 0.25
while now() < drain_until:
    hid.poll(timeout=0.05)

hid.send_zip(pages.home_zip())                    # first screen
active = pages.home_id()
last_keepalive = now()
while True:
    if now() - last_keepalive > 2.0:
        hid.send_clock()
        last_keepalive = now()
    msg = hid.poll(timeout=0.1)
    if not msg:
        continue
    if msg.type == "0101" and msg.is_press:
        page_slot = (active, msg.slot_id)
        action = pages.action_for(page_slot)
        if action.kind == "firmware":
            # page.next/prev/goto/folder/back/switch → change screen
            new_id = pages.resolve_nav(active, action)
            hid.send_zip(pages.zip_of(new_id))
            active = new_id
        elif action.kind == "host":
            # shell / keys / url / volume / etc.
            ActionRunner.run(action)
        # else: ignored (indicator, empty, etc.)
```

### 14.3 Recommended "page YAML" model

Each slot can have two flavors of action:

```yaml
# config/pages/page_home.yaml
title: "Home"
slots:
  "0_0":
    label: "Next page"
    fw_action: page.next
    icon: btn_nextPage.png   # from the pack or from user icons
  "0_1":
    label: "Firefox"
    fw_action: system.open
    fw_param:
      Path: ""                # empty → the firmware does nothing
    icon: firefox.png
    host_action:              # what the bridge runs on receiving the IN
      kind: shell
      cmd: ["firefox"]
  "0_2":
    label: "Volume up"
    fw_action: system.open
    fw_param: { Path: "" }
    icon: volup.png
    host_action:
      kind: keys
      keys: "XF86AudioRaiseVolume"
  "0_4":
    label: "Page 1"
    fw_action: page.indicator
    fw_param: {}
    icon: btn_pageIndicator.png
    text: "1"
```

- `fw_action` is the `Action` that goes into the ZIP's `manifest.json`.
- `host_action` is what the bridge runs on receiving the slot's IN.
- If the `fw_action` is something in the `page.*` family, the bridge
  ignores `host_action` and does the nav.
- If the `fw_action` is `system.open` with a pure HID token Path (ctrlc),
  the firmware will emit it via interface 2 — the bridge can ignore the
  IN.
- If the `fw_action` is `system.website`, the bridge **must** open the
  URL.

### 14.4 Compiling page YAML → ZIP

```
for each page:
  1. load yaml
  2. build the manifest.json dict from the slots, omit 3_2 and 4_2
  3. collect referenced icons:
     - btn_*.png from the factory pack if fw_action is page.*
     - user icons (process to 196×196 RGBA if not already)
  4. assemble an in-memory ZIP with manifest.json + Images/*
  5. cache ZIP_bytes in the PageStore
```

ZIPs are precompiled at startup so that `send_zip` is instant.

> **Maximum size: ~196 KB**. The firmware silently discards ZIPs above
> that threshold (see §12.9). When saving PNGs with Pillow, use
> `optimize=True, compress_level=9` — the RGBA 196×196 PNGs the official
> app exports weigh ~35 KB each, and that, on a page with 10-13 icon
> slots, saturates the limit easily. With that optimization they drop
> ~25-30% and the whole page fits.

### 14.5 Firmware icon pack (re-using the factory ones)

It is worth keeping a local copy in `config/icons/_firmware/` with the
PNGs the official app uses for system buttons:

- `btn_nextPage.png`, `btn_previousPage.png`, `btn_goToPage.png`,
  `btn_folder.png`, `btn_backToParent.png`, `btn_pageIndicator.png`,
  `btn_switchProfile.png`, `btn_playAudio.png`, `btn_stopAudio.png`.

We extracted them from the original USB capture; see
`_archive/research/experiments2/zips/*.zip`. Take from any official-app
ZIP and copy.

### 14.6 Fallback (what you see without a host)

Keep in `/tmp/standalone/manifest.json` a page with slots
`Action: system.open` + pure HID tokens (ctrlc/ctrlv/ctrls/screenshot) so
the device stays useful with no connected host. Push with ADB in
`d200h deploy --factory-fallback` or similar.

### 14.7 LCD brightness

Until we decode the `BrightnessMessage` over HID, write directly to sysfs
via ADB:

```bash
adb shell "echo <0-255> > /sys/class/backlight/backlight/brightness"
```

It is the path supported by our capture.

### 14.8 Shutdown and cleanup

On receiving SIGINT/SIGTERM, the bridge does **not** need to restore
anything on the device — the firmware stays in host-managed mode until
the next cold boot. On disconnecting the USB, the next time it is plugged
in it reads `/tmp/standalone/manifest.json` again (fallback).

### 14.9 Errors and reconnection

- If `read()` returns `OSError ENODEV`: the cable was disconnected. Close
  the fd, wait for the device (`udev` or polling `/dev/hidraw*`), and
  redo the handshake + send of the current ZIP.
- If `write()` returns an error: same.
- The device's `0103` pings/heartbeats serve as an "I'm alive" signal —
  optionally alarm if they stop arriving for more than a few seconds.

---

## 15. References

- USB capture of the official app:
  [_archive/research/wireshark/d200h.json](../../_archive/research/wireshark/d200h.json),
  [_archive/research/wireshark/d200h.csv](../../_archive/research/wireshark/d200h.csv).
- Actions performed during the capture:
  [_archive/research/wireshark/pruebas_en_windows.md](../../_archive/research/wireshark/pruebas_en_windows.md).
- ZIPs extracted from the stream:
  `_archive/research/experiments2/zips/`.
- HID validation scripts (step by step):
  - `_archive/research/experiments2/extract_zips.py` — extracts the ZIPs
    from the capture
  - `_archive/research/experiments2/probe_hid.py` — handshake + IN read
  - `_archive/research/experiments2/send_zip.py` — handshake + ZIP send +
    keepalive
  - `_archive/research/experiments2/get_capabilities.py` — request the
    device's `0303` info
- Historical errors, attempted fixes that did not work, and paths not to
  revisit: [firmware-dead-ends.md](firmware-dead-ends.md).
