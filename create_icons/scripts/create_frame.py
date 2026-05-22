"""
create_frame.py — Genera el PNG de un marco con esquinas redondeadas y
relleno de gradiente.

El marco es un rectángulo redondeado HUECO (solo trazo): se construye
restando un rectángulo redondeado interior a otro exterior, y al alfa
resultante se le aplica el gradiente configurado. La salida es un PNG
RGBA del tamaño indicado en el YAML.

Es un script independiente: no depende de `frame.py` ni de `compose.py`.
Solo reutiliza dos helpers de `utils.py` (gradiente sobre silueta y
guardado PNG) para no duplicar código.

Configuración: `config_create_frame.yaml`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from utils import apply_gradient_to_silhouette, build_frame_silhouette, save_png


DEFAULT_CONFIG = Path(__file__).parent / "config_create_frame.yaml"


def load_config(path: Path) -> dict:
    """Carga y valida mínimamente el YAML de configuración."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config in {path}")
    return cfg


def main(config_path: Path = DEFAULT_CONFIG) -> int:
    cfg = load_config(config_path)

    out_cfg = cfg.get("output", {}) or {}
    width = int(out_cfg.get("width", 196))
    height = int(out_cfg.get("height", 196))

    stroke = int(cfg.get("frame_width", 8))
    radius = int(cfg.get("frame_radius", 20))

    grad_cfg = cfg.get("gradient", {}) or {}
    color1 = str(grad_cfg.get("color1", "#000000"))
    color2 = str(grad_cfg.get("color2", "#000000"))
    angle = float(grad_cfg.get("angle", 90))

    out_raw = cfg.get("output_path")
    if not out_raw:
        print("ERROR: output_path is required in config.", file=sys.stderr)
        return 1
    output_path = Path(out_raw).expanduser()

    try:
        silhouette = build_frame_silhouette(width, height, stroke, radius)
        frame = apply_gradient_to_silhouette(silhouette, color1, color2, angle)
        save_png(frame, output_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print(
        f"OK  frame {width}x{height} stroke={stroke} radius={radius}"
        f"  ->  {output_path}"
    )
    return 0


if __name__ == "__main__":
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    sys.exit(main(cfg_path))
