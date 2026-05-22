# Icons

How icons work in the bridge: where they live, how they are referenced
from a page, the inline generator, and the advanced pipeline for
producing your own.

---

## Where icons live

| Where | What for |
|---|---|
| `config/icons/*.png` | your icons. Resized to 196×196 RGBA on the fly |
| `config/icons/_firmware/*.png` | factory pack (`btn_*`) extracted from the official firmware; already 196×196. Only the example pages use it for navigation buttons — it is not mandatory: if you build your own pages with your own icons, you can ignore or delete it |

The native LCD key resolution is **196×196 px**. Smaller images are
rendered by the firmware as a solid-color square with fallback text
instead of the PNG, so the bridge resizes everything to 196×196 RGBA in
memory at compile time (it does not modify your source files).

---

## Referencing icons from a page YAML

In the YAML you reference by name, with or without `.png`:

```yaml
icon: btn_nextPage      # factory pack, in config/icons/_firmware
icon: firefox           # your own icon, in config/icons
icon: my_icon.png       # the extension is fine too
```

The loader looks first in `config/icons/` (your icons), then in
`config/icons/_firmware/` (factory pack). If the file does not exist in
any path, the loader raises `PageError` when validating/compiling — it is
not a silent warning.

Icons available in the factory pack: `btn_nextPage`, `btn_previousPage`,
`btn_goToPage`, `btn_folder`, `btn_backToParent`, `btn_pageIndicator`,
`btn_switchProfile`, `btn_playAudio`, `btn_stopAudio`.

### Subfolders and relative paths

The `icon:` value is resolved relative to `config/icons/`, so you can use
subdirectories and even leave that folder with `../`:

```yaml
icon: subfolder/my_icon.png                       # config/icons/subfolder/my_icon.png
icon: ../../create_icons/icons/ready_icons/00_home/headphones.png  # repo_root/create_icons/...
```

Full detail and caveats (read-only folders, basename collisions) in
[pages-guide.md §5](pages-guide.md).

> ⚠️ **One icon file cannot repeat in two slots of the same page** — the
> firmware leaves the whole page blank. Text-only slots and
> `icon_generate` are exempt. See [pages-guide.md §5.2](pages-guide.md).

---

## Inline generator (`icon_generate` / `d200h icon-gen`)

If you do not want to design a PNG for every new button, declare the icon
inline in the slot's YAML and let the bridge render it at compile time
(blue frame + centered text, the style of the `page_home` buttons):

```yaml
"0_0":
  icon_generate:
    text: "./run.sh"
    color: "#1a4f8a"     # optional, default blue
    fg: "#ffffff"        # optional, default white
  host_action: {type: shell, cmd: "./run.sh"}
```

- The PNG is cached in `config/icons/__generated__/<hash>.png`
  (gitignored). Changing `text`/`color` → new hash → new PNG.
- Mutually exclusive with `icon`. Validate with `uv run d200h validate`.
- Standalone tester: `uv run d200h icon-gen --text "Run" --out /tmp/x.png`.
- Cleanup: `uv run d200h icon-gen --gc` deletes orphaned cached PNGs.

Requires a TTF font (DejaVu Sans Bold or Liberation Sans Bold; both ship
with Debian/Ubuntu). Without one it falls back to PIL's bitmap font — it
works but looks pixelated; install `fonts-dejavu` or `fonts-liberation`.

---

## Advanced pipeline: `create_icons/`

The inline generator above is intentionally simple (a blue card with
text). For real, polished icons — autocrop, contain-fit, gradient
recoloring, frames, and composing several icons into one — there is a
separate manual pipeline in the `create_icons/` folder.

It is a **complement, not part of the bridge**. It produces PNGs that you
then copy (or reference with a relative `icon:` path) into
`config/icons/`. See [../../create_icons/README.md](../../create_icons/README.md).

| | Inline `icon-gen` | `create_icons/` pipeline |
|---|---|---|
| Output | blue card + text | autocropped, recolored, framed, composed icons |
| Trigger | `icon_generate:` in a slot, or `d200h icon-gen` | manual `python create_icon_with_frame.py` etc. |
| Part of the bridge | yes | no (standalone scripts) |

---

## Cross-references

- [pages-guide.md](pages-guide.md) — full YAML reference, including
  `icon_generate` and the same-icon restriction.
- [../../create_icons/README.md](../../create_icons/README.md) — advanced
  icon pipeline.
- [troubleshooting.md](troubleshooting.md) — "icon not found" and blank
  page issues.
