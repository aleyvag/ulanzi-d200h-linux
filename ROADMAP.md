# Roadmap & pending work

Living document. **Closed** items move to the official documentation
([docs/user/pages-guide.md](docs/user/pages-guide.md),
[README](README.md), [docs/developer/](docs/developer/)) and are removed
from here. **Pending** items or **findings under investigation** live here
until they close. Things that were tried and failed go to
[docs/developer/firmware-dead-ends.md](docs/developer/firmware-dead-ends.md).

---

## 1. Findings — turning the D200H off on suspend/shutdown

> The part already **solved** (a Sleep/Power-OFF slot that puts the LCD
> into *standby* via `brightness_device cmd: zero` before suspending/
> shutting down, with a safe wake and brightness restore) is documented in
> [docs/user/pages-guide.md §4.7](docs/user/pages-guide.md) and
> [docs/developer/architecture.md §4.bis](docs/developer/architecture.md).
> What remains open is covering suspends that do **not** originate from
> the deck itself.

### What the user wants

- **Critical**: when the PC shuts down, the D200H should turn off too
  (instead of staying on the Ulanzi logo forever).
- **Desirable**: the same when the PC suspends.

### Hardware verified on this machine

- `uhubctl` v2.4.0 is installed but **finds no PPPS-compatible hubs**
  (`No compatible devices detected!` even with `sudo`). The USB hubs on
  this board **cannot power the +5V off per port via software**. Dead
  end.
- The D200H is plugged into `Bus 03 → Port 1 (480M sub-hub) → Port 2`.
  For future reference — if the port or external hub changes, re-test
  `sudo uhubctl`.

### Options evaluated

| Path | Status | Reason |
|---|---|---|
| **BIOS — "ErP Ready" / "Deep Sleep S5"** | **Recommended** for shutdown | Cuts +5VSB on power-off. Trade-off: loses "wake on USB". No code needed. |
| `uhubctl -a off` | **Impossible** on this hardware | Hubs without PPPS. |
| systemd hook with `brightness=0` via ADB | **Rejected** by the user | "if the daemon fails and can't bring it back up, I wouldn't know if it's off or not". A black screen is indistinguishable from dead: **worse** than the Ulanzi logo for diagnosis. |
| **Sleep/Power-OFF slot with `multi` (standby)** | **Implemented** | Covers the "I suspended/shut down from the deck" case. See variant B below and [docs/user/pages-guide.md §4.7](docs/user/pages-guide.md). |
| HID reverse — "turn screen off" command | **Pending reverse-engineering** | Not decoded yet. If decoded, it would be the clean path: a SIGTERM signal handler that sends the command and exits. Survives suspend/shutdown without ADB or hooks. See [docs/developer/firmware-protocol.md](docs/developer/firmware-protocol.md). |
| systemd service with `ExecStop=` | **Not enough** | Only stops with the graphical target, which does not always coincide with poweroff/suspend. Dedicated hooks (`/lib/systemd/system-sleep/`) would be the right thing, but since `brightness=0` was rejected, there is no useful payload to put there. |

### Final recommendation while there is no HID reverse

1. **Shutdown** → BIOS (ErP Ready). No code.
2. **Suspend from the deck** → already covered by the Sleep slot (standby
   + suspend).
3. **Suspend NOT initiated from the deck** (lid, GNOME idle, `systemctl
   suspend`) → leave the Ulanzi logo, or implement variant A (DBus) of the
   idea below.
4. **Once the HID "turn screen off" is decoded** → wire it as a bridge
   signal handler so it runs whenever the bridge dies cleanly (suspend,
   shutdown, restart, or manual stop).

### Idea — `suspended` page / automatic DBus trigger

User proposal, **variant B implemented, variant A pending**.

**Concept**: switch to a minimal page (or put the LCD into standby) on
suspend, so the state is diagnosable at a glance instead of showing the
Ulanzi logo.

- **A) Automatic trigger via DBus (generic) — PENDING**. The bridge
  subscribes to `org.freedesktop.login1.Manager.PrepareForSleep` (standard
  systemd-logind signal). `True` → standby / suspended page; `False`
  (resume) → restore. **Advantage**: covers all ways of suspending (lid,
  `systemctl suspend`, GNOME idle). **Implementation**: a thread with
  `pydbus` or similar; survives HID reconnections.
- **B) Manual trigger from the D200H itself — IMPLEMENTED**. The Sleep slot
  is a `multi` (`brightness_device cmd:zero` → `delay` → `system
  suspend`). **Limitation**: only fires when suspending FROM the deck. For
  the other cases, A is needed.

**Closed risk**: if the bridge dies before processing `PrepareForSleep` or
dispatching the `multi`, the firmware falls to the Ulanzi logo after 10s
without keepalive. The worst case is the current behavior, not something
worse.

**Pending verification (empty-page variant)**: confirm whether empty
slots consume extra power. On a backlit LCD the main consumption is the
backlight, not what each slot paints — the real saving would be marginal
unless combined with lowering `brightness_device`.

---

## 2. Operational notes — to avoid repeating known mistakes

- **Do not re-run `d200h convert` without `--keep-existing`** — it
  overwrites hand-edited `user/` YAMLs. The user already flagged this. See
  [docs/user/importing-windows-profiles.md](docs/user/importing-windows-profiles.md).
- **Do not run two bridges at once** — the global one (`d200h.service`)
  and a local one (`uv run d200h bridge`) fight over `/dev/hidraw*` and
  produce erratic behavior. Before any local test:
  `systemctl --user stop d200h.service && pkill -f 'd200h bridge'`.
- **Verify nothing old is still running** with `pgrep -af d200h`.

---

## 3. Pending decisions / open items

- **`WantedBy=graphical-session.target` vs linger tension.** The unit uses
  `WantedBy=graphical-session.target`, but linger exists to start without a
  graphical session — they are in tension. Real headless support would
  require revisiting that target. A behavior change; tackle separately.

---

## 4. Not supported / possible future work

- **OBS control** — *not supported.* Unlike the reference project this
  project drew inspiration from, this bridge does **not** control OBS.
  There is no OBS handler, no WebSocket, no scenes; OBS would only work via
  generic `keys`/`shell`/`app` actions. (The only trace of "obs" in the
  code is a Windows-executable→generic-command mapping row in
  `convert.py`.) A real implementation could start from
  [obs-websocket](https://github.com/obsproject/obs-websocket) as a
  WebSocket-style handler.
- **Wayland support** — the window-focus feature is **X11 only**
  (`xprop`); raising windows (`wmctrl`) is X11 only. Wayland is untested
  and likely partly broken. A backend would depend on the compositor
  (DBus/KDE, GNOME extension, Sway/Hyprland IPC).
- **Decode `SmallWindowMessage` (HID)** — to control the clock-slot widget
  (clock ↔ CPU ↔ logo) instead of only syncing the time. See
  [docs/developer/firmware-protocol.md §12](docs/developer/firmware-protocol.md).
- **"Turn screen off" HID command** — would let the bridge blank the LCD
  cleanly on shutdown without ADB or system hooks (ties into §1).
- **`BrightnessMessage` over HID** — change LCD brightness without going
  through ADB.


