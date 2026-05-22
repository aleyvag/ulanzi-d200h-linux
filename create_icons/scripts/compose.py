"""
compose.py — Composición de varios iconos en una sola imagen.

Lee `config_compose.yaml` y una tabla CSV. Cada fila del CSV define un
icono dentro de una imagen compuesta de salida; las filas que comparten
`output_name` se combinan en la misma imagen.

Los iconos de entrada ya vienen recortados y perfectos (este script NO
hace autocrop ni encuadre): solo se posicionan en el lienzo según las
coordenadas (x, y) y el orden de apilado z.

La salida de este script son iconos compuestos individuales, que luego
pueden pasarse por `frame.py` para aplicarles marco / encuadre / gradiente.

Formato del CSV
---------------
Columnas: output_name, icon, x, y, z

- Una fila por icono. Las filas con el mismo `output_name` se combinan
  en una imagen.
- `x`, `y` son la esquina superior izquierda donde se pega el icono dentro
  del lienzo (coordenadas absolutas respecto al lienzo configurado en el
  YAML).
- `z` es el orden de apilado: el icono con z más alto se dibuja encima.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml
from PIL import Image

from utils import load_image, save_png


DEFAULT_CONFIG = Path(__file__).parent / "config_compose.yaml"


@dataclass
class IconLayer:
    """Representa un icono a colocar dentro de un lienzo de composición."""
    icon: str
    x: int
    y: int
    z: int


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config in {path}")
    return cfg


def read_table(csv_path: Path) -> Dict[str, List[IconLayer]]:
    """
    Lee el CSV y agrupa las filas por `output_name`. Devuelve un dict
    {output_name: [IconLayer, ...]}.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV table not found: {csv_path}")

    compositions: Dict[str, List[IconLayer]] = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"output_name", "icon", "x", "y", "z"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"CSV {csv_path} is missing required columns: {sorted(missing)}"
            )
        for i, row in enumerate(reader, start=2):  # start=2 por el encabezado
            try:
                layer = IconLayer(
                    icon=row["icon"].strip(),
                    x=int(row["x"]),
                    y=int(row["y"]),
                    z=int(row["z"]),
                )
            except (ValueError, KeyError) as e:
                raise ValueError(
                    f"Invalid row in {csv_path}:{i}: {row} ({e})"
                ) from e
            output_name = row["output_name"].strip()
            if not output_name:
                raise ValueError(f"Empty output_name in {csv_path}:{i}")
            compositions[output_name].append(layer)
    return compositions


def compose_image(
    layers: List[IconLayer],
    input_dir: Path,
    canvas_width: int,
    canvas_height: int,
) -> Image.Image:
    """
    Crea el lienzo transparente y pega cada icono en su posición, en orden
    de Z ascendente (Z mayor queda encima).
    """
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))

    for layer in sorted(layers, key=lambda c: c.z):
        icon_path = input_dir / layer.icon
        # load_image acepta SVG y PNG; estos iconos ya vienen perfectos.
        img = load_image(icon_path)
        canvas.alpha_composite(img, dest=(layer.x, layer.y))

    return canvas


def main(config_path: Path = DEFAULT_CONFIG) -> int:
    cfg = load_config(config_path)

    input_dir = Path(cfg.get("input", "./icons_to_compose")).expanduser()
    output_dir = Path(cfg.get("output_dir", "./composed")).expanduser()
    table_path = Path(cfg.get("table", "./compositions.csv")).expanduser()

    canvas_cfg = cfg.get("canvas", {}) or {}
    canvas_width = int(canvas_cfg.get("width", 512))
    canvas_height = int(canvas_cfg.get("height", 512))

    if not input_dir.exists():
        print(f"ERROR: input folder does not exist: {input_dir}", file=sys.stderr)
        return 1

    compositions = read_table(table_path)

    processed = 0
    errors = 0
    for output_name, layers in compositions.items():
        try:
            img = compose_image(layers, input_dir, canvas_width, canvas_height)
            out_path = output_dir / output_name
            if out_path.suffix.lower() != ".png":
                out_path = out_path.with_suffix(".png")
            save_png(img, out_path)
            processed += 1
            print(f"OK  {output_name}  ({len(layers)} layers)  ->  {out_path}")
        except FileNotFoundError as e:
            errors += 1
            print(f"ERR {output_name}: file not found: {e}", file=sys.stderr)
        except ValueError as e:
            errors += 1
            print(f"ERR {output_name}: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"ERR {output_name}: {type(e).__name__}: {e}", file=sys.stderr)

    print(f"\nDone. Compositions: {processed}. Errors: {errors}.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    sys.exit(main(cfg_path))
