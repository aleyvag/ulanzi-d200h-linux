# create_icons — icon-processing pipeline

A standalone batch pipeline for producing polished button icons: autocrop,
contain-fit, optional gradient recoloring, framing, and composing several
icons into one.

> **This folder is a complement, NOT part of the bridge.** Nothing here
> runs at bridge runtime. It produces PNGs that you then copy (or
> reference with a relative `icon:` path) into `config/icons/` of the
> bridge. For the bridge's simple inline generator (`icon_generate` /
> `d200h icon-gen`) — a different, much simpler thing — see
> [../docs/user/icons.md](../docs/user/icons.md).

All configuration lives in YAML files. **You edit the YAML, not the
code.**

---

## The three scripts

Under `scripts/`:

| Script | Config | What it does |
|---|---|---|
| `create_icon_with_frame.py` | `config_create_icon_with_frame.yaml` | autocrop → contain-fit → optional gradient recolor → compose onto a frame |
| `create_frame.py` | `config_create_frame.yaml` | generates a standalone frame PNG |
| `compose.py` | `config_compose.yaml` + `compositions.csv` | combines several icons onto one canvas per a CSV table |
| `utils.py` | — | shared helpers, not run directly |

---

## Dependencies

Python packages (already in the project's `.venv`):

- [Pillow](https://pillow.readthedocs.io/) — raster manipulation (PNG/WebP
  with transparency).
- [cairosvg](https://cairosvg.org/) — rasterize SVG to PNG from the
  vector.
- [PyYAML](https://pyyaml.org/) — read the configuration.
- [NumPy](https://numpy.org/) — linear gradient computation.

System library (`cairosvg` needs **libcairo**):

- **Debian / Ubuntu / Pop!_OS**: `sudo apt install libcairo2`
- **Fedora**: `sudo dnf install cairo`
- **macOS (Homebrew)**: `brew install cairo`

Run with the project's root `.venv`:

```bash
source .venv/bin/activate
python create_icons/scripts/create_icon_with_frame.py
```

Input formats: SVG (rasterized at high resolution from the vector), PNG,
and WebP. Output is always PNG with transparency.

---

## Expected `icons/` structure

The `icons/` folder is **your personal configuration** and is **not
versioned** (it is gitignored — see [project .gitignore](../.gitignore)).
A minimal example skeleton is committed so the scripts run out of the box;
you fill `pending_icons/` with your own art.

```
create_icons/
├── icons/
│   ├── pending_icons/        ← PUT the icons to process here
│   │   ├── (loose .png/.svg/.webp icons)
│   │   ├── recolor/          ← (optional) icons that will receive the gradient
│   │   ├── compose/          ← base icons that compose.py will combine
│   │   └── composed/         ← compose.py output
│   ├── ready_icons/          ← final output of create_icon_with_frame.py
│   └── template/
│       └── frame.png         ← (optional) frame PNG, if you use frame.add: "file"
└── scripts/
    ├── utils.py
    ├── create_icon_with_frame.py
    ├── create_frame.py
    ├── compose.py
    ├── config_create_icon_with_frame.yaml
    ├── config_create_frame.yaml
    ├── config_compose.yaml
    └── compositions.csv
```

> The recolor folder is set with `recolor_dir` and can point **anywhere**,
> it does not have to be inside `input`.

---

## `create_icon_with_frame.py` — frame, fit, and gradient

Processes each icon in `input/` and saves the result in `output_dir/`,
replicating the subfolder hierarchy.

Per-icon flow:

1. Load (if SVG, rasterize at high resolution **from the vector**).
2. Autocrop to the real content bounding box.
3. **Contain**-style fit inside the usable box (defined by the padding).
4. If the icon is inside `recolor_dir`, apply the gradient using its
   silhouette as a mask. Otherwise, keep its original color. (Recoloring
   is decided **by location**, not by color analysis.)
5. If `frame.add` is set, paste the icon onto the frame PNG. Otherwise it
   stays on a transparent canvas.
6. Save PNG with transparency.

Config (`config_create_icon_with_frame.yaml`):

```yaml
output:
  width: 196
  height: 196

input: "../icons/pending_icons/logos"
output_dir: "../icons/ready_icons/logos"

# Frame. add: "file" | "create" | false
frame:
  add: "create"
  path: "../icons/template/frame.png"   # only if add: "file"
  width: 8                               # only if add: "create" (thickness)
  radius: 20                             # only if add: "create" (bevel)
  gradient:                              # only if add: "create"
    color1: "#42A3FF"
    color2: "#2F88FF"
    angle: 90

padding_percent: 20

# Icon recoloring (independent of the frame gradient).
recolor_dir: "../icons/pending_icons/recolor"
output_recolor_dir: "../icons/ready_icons/recolor"
recolor_gradient:
  color1: "#0A4DA6"
  color2: "#4FA3FF"
  angle: 90
```

Gradient angle convention: `0°` → color1 left, travels right; `90°` →
color1 top, travels down; `180°` → color1 right; `270°` → color1 bottom,
goes up; `45°` → corner-to-corner diagonal. If `color1 == color2`, the
result is flat color.

```bash
python create_icon_with_frame.py                  # uses ./config_create_icon_with_frame.yaml
python create_icon_with_frame.py /other/path.yaml
```

---

## `create_frame.py` — generate a standalone frame PNG

Same algorithm as `frame.add: "create"`, but as a separate utility to
generate and save a reusable frame PNG. Configured by
`config_create_frame.yaml` (fields: `output.{width,height}`,
`frame_width`, `frame_radius`, `gradient`, `output_path`).

```bash
python create_frame.py
```

---

## `compose.py` — combine icons

Combines several icons into one image, per a CSV table. Input icons are
already cropped and perfect; they are only positioned. The composed output
can then be passed through `create_icon_with_frame.py` (move them into its
`input` folder).

Config (`config_compose.yaml`):

```yaml
input: "../icons/pending_icons/compose"
output_dir: "../icons/pending_icons/composed"
table: "./compositions.csv"

canvas:
  width: 512
  height: 512
```

CSV format (`compositions.csv`):

```csv
output_name,icon,x,y,z
lock_cloud.png,cloud.png,0,0,1
lock_cloud.png,lock.png,180,180,2
user_check.png,user.png,0,0,1
user_check.png,check.png,300,300,2
```

- One row per icon. Rows sharing the same `output_name` are combined into
  one image.
- `x`, `y` = top-left corner where the icon is pasted on the canvas.
- `z` = stacking order (higher Z is drawn on top).

```bash
python compose.py                  # uses ./config_compose.yaml
python compose.py /other/path.yaml
```

---

## Recommended workflow

1. Place loose icons in `pending_icons/` (those that need no composition).
2. Put the ones that should get the gradient where `recolor_dir` points.
3. If you need to combine several icons into one:
   - Place the base icons in `pending_icons/compose/`.
   - Define the composition in `compositions.csv`.
   - Run `python compose.py`. Output goes to `pending_icons/composed/`.
   - Move those composites into `pending_icons/` (or `recolor/`) as
     needed.
4. Run `python create_icon_with_frame.py`. Results land in `ready_icons/`
   (recolored ones in `output_recolor_dir`).
5. Copy the finished PNGs into the bridge's `config/icons/`, or reference
   them directly with a relative `icon:` path (see
   [../docs/user/icons.md](../docs/user/icons.md)).
