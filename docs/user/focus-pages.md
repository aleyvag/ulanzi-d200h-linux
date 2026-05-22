# Focus pages — automatic page switch by window focus (X11)

The bridge can automatically change the active page when you give focus to
a specific application (just like the official Windows software does). It
is configured in
[`config/focus_rules.yaml`](../../config/focus_rules.yaml). If the file
does not exist, the feature is disabled — it is **not** mandatory.

> ⚠️ **X11 only.** The watcher uses
> `xprop -spy -root _NET_ACTIVE_WINDOW`. On **Wayland it does not work**
> (untested / likely broken) and the bridge disables the feature on its
> own if there is no `DISPLAY` or `xprop` is missing. Wayland support is
> pending and depends on the compositor (DBus/KDE, GNOME extension,
> Sway/Hyprland IPC) — see [../../ROADMAP.md](../../ROADMAP.md).

---

## Format

```yaml
# config/focus_rules.yaml
default: home              # optional: where to return if nothing matches

rules:
  - { match: "Code",    page: vscode }          # VS Code (case-insensitive substring)
  - { match: "Brave",   page: brave_incog }
  - { match: "Spotify", page: spotify }
```

- `match`: case-insensitive substring against the **CLASS** of `WM_CLASS`
  (the 2nd string returned by `xprop WM_CLASS`).
- `page`: destination page_id (must exist — if not, a warning is logged
  and the current page is kept).
- `default`: optional. If defined, it is loaded when no rule matches
  (terminal, file manager, etc.). If not defined, the bridge keeps the
  current page on unmatched focus.

---

## How to discover a window's WM_CLASS

```bash
xprop WM_CLASS    # click the target window
# output: WM_CLASS(STRING) = "code", "Code"
#                                     ^^^ the 2nd string is the one the bridge uses
```

---

## Key behavior

The watcher only fires when the WM_CLASS **changes** between consecutive
windows. Repeated events of the same class do not re-fire — this way you
can navigate manually within the group of pages (`vscode` → `vscode_2`)
without the watcher dragging you back to the rule's first page. If you
switch to another app and come back, it takes you again to the entry
page.

---

## Note on profile conversion

The `.ulanziDeckProfile` exported by the Windows software does **not**
include the app→profile association (that metadata lives in the
software's global config, not in the export). That is why `d200h convert`
does not generate `focus_rules.yaml` automatically — you must edit it by
hand. See
[../developer/convert-internals.md §7](../developer/convert-internals.md).

---

## Cross-references

- [pages-guide.md](pages-guide.md) — full YAML reference (slots,
  `host_action`, `fw_action`, icons).
- [pages-cheatsheet.md](pages-cheatsheet.md) — condensed syntax.
- [troubleshooting.md](troubleshooting.md) — debugging and known
  limitations.
