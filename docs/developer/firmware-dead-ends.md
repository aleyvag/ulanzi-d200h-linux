# Ulanzi D200H — Wrong hypotheses, attempted fixes, and discarded paths

> **Purpose**: record incorrect hypotheses, fixes that were tried and did
> NOT work, and paths NOT to revisit. For how the device really works,
> see [firmware-protocol.md](firmware-protocol.md).
>
> Each entry lists the belief/attempt, context, the evidence that refutes
> it, and what is in its place.

---

## 1. "It is NOT a pure HID device for configuration. Using hidapi/hid.device() crashes the firmware"

**Origin**: `info_dev_ulanzi_D200h.md` (v1, deleted) §"What it is NOT".

**Why it was wrong**: the D200H **IS a vendor HID device**, and that is
exactly the channel the official Windows app uses to control it in real
time. The USB capture of the official app shows **zero ADB traffic**
during nav actions; everything goes over HID.

The "crash" probably came from:
- Trying to speak the **Elgato Stream Deck protocol** (Stream Deck
  Original / Mini / XL / +), which is NOT the D200H's.
- Sending reports without the `7c 7c` magic or with the wrong size.

**What to do**: use the protocol described in
[firmware-protocol.md §4](firmware-protocol.md#4-hid-vendor-protocol-interface-0).
Magic `7c 7c`, type in bytes [2..3], length LE in [4..7] for type `0001`.
**Important**: see §4.4.3 of the main doc for the Report ID byte
convention in `write()` to hidraw — a common error that breaks streamed
ZIP transmission.

---

## 2. "It does NOT read configuration from USB packets in real time → It reads JSON files from the filesystem"

**Origin**: `info_dev_ulanzi_D200h.md` (v1, deleted).

**Why it was wrong**: the firmware **does read configuration over HID in
real time**. The official app pushes a full ZIP (`manifest.json` +
icons) every time it changes page. The firmware extracts it into memory
and repaints without restarting.

`/tmp/standalone/manifest.json` is only the **fallback** screen shown when
there is no connected host. It is not the control channel.

---

## 3. "There are no OS profiles that load different manifests. `keyMode=linux` does not exist."

**Still partly true**: `keyMode` only affects how the firmware interprets
the `Path` of a `system.open` when that `Path` is NOT a pure HID token.
Confirmed: `linux` is not a supported value; the firmware falls back to
Win.

**What was wrong, associated with this**: the idea that "the firmware has
4 fixed hardcoded manifests (`manifest`, `manifest1`, `manifest2`,
`manifest3`) and nothing else". That was true only for the **factory
fallback** (what you see with no host). With the official app connected,
the host sends **any number** of pages, in any order, all different, over
HID.

---

## 4. "The firmware has a 2×2 grid of manifests with hardcoded SCAN-based nav"

**Origin**: `info_dev_ulanzi_D200h_v2.md` (v2, deleted) §6 ("Firmware
native navigation").

**Why it was wrong**: the "native nav" we saw in the
`experiments/page-nav-test/` experiment (slots 0_2 / 1_2 / 2_2 triggering
something nav-like) was a **misinterpretation of coincidences**:

- Those three slots are the ones that, in the factory manifests, have
  icons `Images/multi_task.png`, `Images/win.png` (or `mac.png`),
  `Images/switch_page.png`.
- When pressed, the firmware emits a normal HID IN report with the
  corresponding `slot_id`.
- In the official app, **the host** interprets that IN and sends the ZIP
  of the destination page → change with no flash.
- In our experiment, **with no active HID host**, the firmware seemed to
  "navigate on its own": in reality it was loading manifests from
  `/tmp/standalone/` with the factory fallback heuristic (which does have
  that 2×2 toggle between `manifest`/`manifest1`/`manifest2`/`manifest3`).

**Summary**: what looked like "firmware nav by SCAN" was the factory
fallback logic with no host. With a host, **there is no such 2×2 toggle**
— there are N pages at the host's discretion.

---

## 5. "There is no Action JSON for 'go to page N'"

**Origin**: `info_dev_ulanzi_D200h_v2.md` (v2, deleted) §"Critical
implications".

**Refuted**: ALL of these Actions exist, seen in real manifests extracted
from the capture:

- `com.ulanzi.ulanzideck.page.next`
- `com.ulanzi.ulanzideck.page.prev`
- `com.ulanzi.ulanzideck.page.goto` with `ActionParam: {Page: N}`
- `com.ulanzi.ulanzideck.page.folder` with `ActionParam: {ProfileUUID: "<uuid>"}`
- `com.ulanzi.ulanzideck.page.back`
- `com.ulanzi.ulanzideck.page.switch` with `ActionParam: {Profile, ProfileUUID}`
- `com.ulanzi.ulanzideck.page.indicator`
- `com.ulanzi.ulanzideck.system.website` with `ActionParam: {Url}`
- `com.ulanzi.ulanzideck.sound.play` / `.sound.stop`

That they did not appear in `strings UlanziDeckKey` did not mean they did
not exist — it meant they are **serialized as JSON strings** contained in
the manifest ZIP, not as literals in .rodata.

> Lesson: the absence of a string in the binary is not evidence that the
> Action does not exist. The presence of a string IS evidence that
> something exists, but absence proves nothing.

---

## 6. "`/dev/hidg1` is the channel the official app uses"

**Origin**: `info_dev_ulanzi_D200h_v2.md` (v2, deleted) §7.

**Why it was wrong**: `/dev/hidg1` and `/dev/hidg0` are the **gadgets on
the device side** (the RK3308 presents them to the host as keyboard +
vendor). From the host PC you do not access them by those names — from
the host you see a normal USB device with its 3 interfaces.

**The correct way**: from the Linux PC, open `/dev/hidraw*` (one per HID
interface of the device). The one with 1024-byte reports is **interface 0
(vendor)**, the 8-byte one is **interface 2 (Boot Keyboard)**.

---

## 7. "The binary does not contain `com.ulanzi.ulanzideck.*` literals (they are in the embedded factory ZIP)"

**Origin**: `STATUS_v2.md` (deleted).

**Refinement**: it is **true** that many Action strings appear inside the
embedded ZIP (the factory `manifest.json` files). But some **are** in the
binary: `Protocol::ID::APP_EXIT`, `Protocol::ID::SHUTDOWN`,
`Protocol::ID::GETBASE`, the dispatcher types (`IconMessage`,
`KeyAction`, `BrightnessMessage`, `SmallWindowMessage`,
`RunResultMessage`, `UpdateSelfMessage`).

These are **HID protocol command IDs, not JSON Actions**. Probable
mapping: each corresponds to a value of bytes[2..3] of the HID packet
different from those we have seen (`0001`, `0006`, `0101`).

> `[PENDING]` decode the switch in `DeviceWorker::receiveData()` to map
> each `Protocol::ID::*` to the real wire format.

---

## 8. "The binary must be patched to avoid the logo flash"

**Origin**: `STATUS_v1.0.md` (deleted), `STATUS_v2.md` (deleted).

**Refuted**: the logo flash is a consequence of **our wrong
architecture** (killing the process to force a manifest re-read). The
official app does NOT kill the process: it pushes the ZIP over HID and
the screen changes without restart.

**Correct solution**: switch the bridge to direct HID. Do not touch the
binary.

---

## 9. "Only the factory has manifests; any `manifest4.json` you add will be ignored"

**Origin**: `info_dev_ulanzi_D200h_v2.md` (v2, deleted) §6.

**Important nuance**: true in fallback mode (no host). The firmware only
regenerates `manifest.json` + `manifest1.json` + `manifest2.json` +
`manifest3.json` from the embedded ZIP and only knows those four names
for fallback mode.

**But with a connected HID host**, pages are unlimited. What matters is
not how many files there are in `/tmp/standalone/`, but which ZIPs the
host sends over HID.

---

## 10. "The logo appears because there is no SIGHUP handler / no inotify"

**True but irrelevant to the real use case**.

`SigCgt = 0x1000a4002` (no SIGHUP bit), no inotify, `WatcherProcess`
relaunches after kill — all confirmed. But **this is not the path to
solve the flash**, because the official app never kills the process nor
needs SIGHUP. It solves the problem by pushing ZIPs over HID.

---

## 11. "`com.ulanzi.ulanzideck.keyboard.send` and `shell.exec` do not exist"

**Still true in 2.0.3** as we tested before. I have not seen them again
in the captured ZIPs of the official app — the official app does not use
them.

**But**: with the new HID bridge, they are not needed. To emit keys to
the host it is enough for the host to capture the IN of the pressed slot
and use `ydotool`/`xdotool` locally. To run shell, likewise from the
host.

---

## 12. "We must reverse-engineer the binary with ghidra"

Partly true only for decoding HID types we have not yet seen pass over
the wire (BrightnessMessage, IconMessage, etc.).

**For the priority case** (page change with no flash, websites, audio):
not needed. The USB capture shows the entire required wire format.

---

## 13. "Each (page, slot) → action is resolved on the host with a YAML binding"

The old bridge model (ADB + evtest). **Still conceptually correct** but
with one important change:

- Before: the host **did not know** which Action each slot had, it only
  had a parallel YAML (`bindings.yaml`) mapping slots to host actions.
- Now: the host **owns** the active manifest (it builds it itself before
  pushing it). The parallel YAML can be removed: each slot **carries its
  Action already in the manifest**, and the host decides, on receiving
  the IN, what to do based on its own copy.

This radically simplifies the model: a single source of truth (the
project's page YAML), compiled to manifest+ZIP, and on receiving an IN
the same source is re-read to know what to do.

---

## 14. "We must keep a manual inventory of manifests preloaded at boot"

The old bridge model: `deploy --all` pushes all pages to
`/tmp/standalone/_pages/` and overwrites `manifest1/2/3.json` with HOME
"defensively".

**No longer necessary** with the HID bridge. Whatever is in
`/tmp/standalone/` is **only the no-host fallback**. The active host sends
each ZIP live.

> Recommendation: leave a simple "HID-tokens-only fallback" page
> (ctrlc/ctrls/ctrlv/...) in `/tmp/standalone/manifest.json` so the device
> keeps serving as a basic macro keyboard if the PC turns off or the
> bridge crashes.

---

## 15. "ANALYSIS.md indicates exact rows of the JSON capture"

**Origin**:
[_archive/research/wireshark/ANALYSIS.md](../../_archive/research/wireshark/ANALYSIS.md).

The line references (`D200h_temp.json:27069`, "CSV rows 44, 57, 71,
85…") and the absolute paths of the original capture no longer apply —
the current files are `_archive/research/wireshark/d200h.json` and
`_archive/research/wireshark/d200h.csv`, with different numbering.

ANALYSIS.md was right about the endpoint list and the general idea
("commands on EP2 OUT of 1051 bytes with a 27-byte ACK response"), and
about identifying the device descriptor. The rest (specific row numbers,
hypotheses about `/dev/usb-ffs/adb/4`) should NOT be used.

---

## 16. "It is best to read `/dev/input/event0` over ADB with `evtest`" (legacy)

It works but it is the long way. The info it gives (pressed slot) is **the
same** the HID IN gives (`byte[9] = slot_id`), but it requires ADB always
active and does not solve the logo-flash problem.

Use direct HID. Keep evtest as a plan B if for some reason opening
`/dev/hidraw*` fails on some host.

---

## 17. "Changing the clock/widget of the reserved slot requires patching the binary"

**Origin**: `STATUS_v1.0.md` (deleted) §"Research 2026-05-05".

**Partly refuted**: the OUT command `00 06` that syncs the time shows the
host DOES control what is shown in that slot (at least its time).
Probably the `SmallWindowMessage` that appears in the binary strings is
another OUT command (type `00 xx`) that allows changing the full widget
of the reserved slot (clock↔CPU↔logo).

`[PENDING]` capture that interaction (changing the clock widget config in
the official app) to identify the wire format.

---

## 20. "Two slots cannot reference the same `Icon` in the manifest" (session 2026-05-17, refuted the same day)

**Origin**: while fixing the page-change bug to `test_app_open_windows`,
making the transparent placeholders unique per slot
(`Images/_blank_<slot_id>.png` instead of a single shared
`Images/_blank.png`) made the page render. It was hastily concluded that
the firmware **rejects the render** when two slots reference the same path
in `ViewParam[0].Icon`.

**Why it was wrong**: the `home` page (hand-curated in
`config/pages/examples/page_home.yaml`) has 11 slots that reference
exactly `com.ulanzi.deck.page/Images/btn_goToPage.png` and **renders
without problems** since day 1. Direct counter-example: if "duplicate
Icon" were the cause, home would never have worked.

**What was really happening**: making the blanks unique changed the byte
layout of the `test_app_open_windows` ZIP. The intermittent loss of 1
byte in HID transmission (see §4.4.2 of the main doc) affects certain
positions depending on the ZIP content. After the change, that page
stopped landing in a fatal position — **not because of the rule, but by
layout coincidence**.

**What is in its place**: the real bug is the HID byte-drop (§4.4.2). The
unique-blanks-per-slot hack **is still applied** in `pages.compile_page`
because it empirically resolves the T·app case with no known
contraindications, but it is a workaround, not a structural firmware
rule. Do not introduce validations (neither in the loader nor in the
converter) that reject YAMLs with repeated icons: it would mark home as
invalid.

---

## 21. Attempted fixes for the HID byte-drop (§4.4.2) — session 2026-05-17

> **Resolved 2026-05-18**: the real cause was omitting the Report ID byte
> in `os.write` to `/dev/hidraw*` (see §4.4.3 of the main doc). This
> section is kept as history to avoid repeating the same tests: none of
> the following fixes targeted the correct cause.

Symptom: for certain page-changes the firmware logs `unzip fail:
"short read"` and does not render. Confirmed by byte-diff between the sent
ZIP and the device's `/tmp/temp.zip`: 1 byte `0x00` lost near the chunk
4→5 transition. The loss is intermittent and depends on the exact ZIP
content.

### 21.1 Sleep between consecutive `os.write`s

Tested in `hid.HidClient.send_zip`:

- `time.sleep(0.002)` (2 ms) → the same pages fail.
- `time.sleep(0.010)` (10 ms) → T·win, T·snd still fail; others OK.
- `time.sleep(0.020)` + drain of IN reports → T·win/T·snd OK **only with
  a working copy of the content** (T·win with YAML identical to T·vol's).
  With the original content they keep failing.
- `time.sleep(0.100)` (100 ms) → pages that worked without sleep start to
  fail (including T·vol). Perceptible, unacceptable latency.

Conclusion: the delay between writes is not the solution. There is a
content-dependent component that the sleep does not resolve.

### 21.2 Padding the ZIP to a chunk boundary

Add bytes to the EOCD *ZIP comment* up to size = `1016 + N*1024` so the
last HID chunk always carries 1024 real bytes (no zero padding).
Implemented in `zip_pack.py`. Local validation (`zipfile.testzip()`) OK.
On device the user reported "very slow and the ones that worked no longer
work".

**It is possible the slowness was due to more HID reports (larger ZIPs)
and the regression due to interaction with other simultaneous
experiments**. It is worth revisiting it in isolation in a future session
— it is the most promising open line.

### 21.3 Double send of the ZIP

Send each ZIP twice in a row (`for _ in range(2)`) with `sleep(0.030)`
between sends. Hypothesis: random timing between attempts would make at
least one arrive intact and overwrite the corrupt `/tmp/temp.zip` of the
first attempt. Result: it got worse — pages that previously rendered
stopped doing so and the device got stuck.

### 21.4 Reorganizing home → page_test_home

Idea: lighten `page_home.yaml` by moving the T·* launchers to a new
`page_test_home.yaml`, in case the number of loaded or referenced pages
from home mattered. Result: the same pages (T·win, T·snd) keep failing
regardless. It is not the number of loaded pages.

### 21.5 Changing the `Text` field in the manifest

Change `text: "widget"` to `text: "Mute"` in T·win (same Text as in T·vol
which does work). Result: T·win keeps failing. The `ViewParam[0].Text`
field is not the cause.

### 21.6 Functional content copy between pages

Copy the T·vol YAML (works) over the T·win one (fails) and the T·app one
(works) over the T·snd one (fails). Functionally identical manifests
(differing only in random UUIDs). Result: T·win and T·snd **keep failing**
with the copied content. Confirms the cause is in the exact ZIP bytes
(which change due to UUIDs and the deflate output), not in the manifest's
logical content.

---

## 18. Summary of "what NOT to do"

1. ❌ Do not use `killall UlanziDeckKey` as a page-change mechanism.
2. ❌ Do not rewrite manifests in `/tmp/standalone/` for live changes.
3. ❌ Do not try to send `keyboard.send` or `shell.exec` in manifests:
   the slots are silently ignored.
4. ❌ Do not assume the firmware "navigates on its own" by physical SCAN.
   The only thing it does on press is emit a HID IN with `slot_id`.
5. ❌ Do not assume a 4-page cap. That was a factory-fallback thing.
6. ❌ Do not open `/dev/hidg0` or `/dev/hidg1` from the host PC — those
   are device-side nodes.
7. ❌ Do not use `keyMode=linux` — it causes garbage typing. Keep `win`.
8. ❌ Do not rely on absent strings to rule out features — the page
   Actions were inside the factory ZIP, not in .rodata.
9. ❌ Do not introduce a "two slots cannot share `Icon`" validation: it
   is **false** (`home` violates it and renders perfectly). See §20.
10. ❌ Do not repeat the §21 fixes without a new reason (sleep between
    chunks, double send, etc.). For the HID byte-drop (§4.4.2 of the main
    doc), the open line is padding the ZIP to full chunks (§21.2) — test
    in isolation, with no added sleeps.

---

## 19. Summary of "what TO do"

1. ✅ Speak HID vendor (interface 0) from the host PC's `/dev/hidraw*`.
2. ✅ **Prepend a `0x00` byte to each `os.write(fd, ...)`** (Report ID for
   the unnumbered descriptor). Without this, the Linux kernel consumes the
   first byte of the payload as the Report ID and the ZIP chunks are sent
   one byte short intermittently. Detail in
   [firmware-protocol.md §4.4.3](firmware-protocol.md).
3. ✅ Push a ZIP per page change using OUT type `00 01`.
4. ✅ Read IN type `01 01` to detect presses; `byte[9]` = slot_id.
5. ✅ Keep a fallback page in `/tmp/standalone/manifest.json` with pure
   HID tokens for no-host use.
6. ✅ Leave `keyMode=win` and forget about it.
7. ✅ Keep ADB only for debug/diagnostics/LCD brightness.
