"""
create_icon_with_frame.py — Procesa iconos en batch y opcionalmente los
monta sobre un marco (cargado de archivo o generado al vuelo).

Flujo por cada icono encontrado:
    1. Cargar (convertir si es SVG, rasterizado a alta resolución).
    2. Autocrop (recorte del espacio transparente).
    3. Calcular la caja útil interior del lienzo a partir del padding
       configurado (padding del 25% por lado = caja del 50% central).
    4. Escalar el icono con "contain" para que quepa en esa caja y centrarlo.
    5. Si el icono está dentro de `recolor_dir`, aplicar `recolor_gradient`
       usando su silueta como máscara.
    6. Componer sobre el marco según `frame.add`:
         - "file"   → cargar `frame.path` y pegar encima el icono.
         - "create" → generar el marco con `frame.width`, `frame.radius`
           y `frame.gradient` (rectángulo redondeado hueco).
         - false    → lienzo transparente del tamaño de salida.
    7. Guardar (a `output_dir` o a `output_recolor_dir` según corresponda).

IMPORTANTE: el recoloreo se decide POR UBICACIÓN (carpeta), no por
detección automática de color.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PIL import Image

from utils import (
    apply_gradient_to_silhouette,
    autocrop,
    build_frame_silhouette,
    compute_inner_box,
    fit_contain,
    is_supported_image,
    load_image,
    save_png,
)


DEFAULT_CONFIG = Path(__file__).parent / "config_create_icon_with_frame.yaml"


def load_config(path: Path) -> dict:
    """Carga y valida mínimamente el YAML de configuración."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid config in {path}")
    return cfg


def build_frame_image(
    cfg_frame: dict, output_width: int, output_height: int
) -> Image.Image | None:
    """
    Resuelve el marco según `frame.add`:
        "file"   → carga `frame.path` (redimensiona si hace falta).
        "create" → genera el marco con `frame.width`, `frame.radius` y
                   `frame.gradient`.
        false    → None (sin marco; lienzo transparente).

    Devuelve un RGBA del tamaño de salida o None.
    """
    add = cfg_frame.get("add", False)

    if add is False or add is None or add == "false":
        return None

    if add == "file":
        raw = cfg_frame.get("path")
        if not raw:
            raise ValueError("frame.add='file' but frame.path is missing.")
        frame_path = Path(raw).expanduser()
        if not frame_path.exists():
            raise FileNotFoundError(f"Frame PNG not found: {frame_path}")
        img = Image.open(frame_path).convert("RGBA")
        if img.size != (output_width, output_height):
            print(
                f"WARNING: frame {img.size} does not match output size "
                f"({output_width}x{output_height}). It will be resized.",
                file=sys.stderr,
            )
            img = img.resize((output_width, output_height), Image.LANCZOS)
        return img

    if add == "create":
        stroke = int(cfg_frame.get("width", 8))
        radius = int(cfg_frame.get("radius", 20))
        grad = cfg_frame.get("gradient", {}) or {}
        color1 = str(grad.get("color1", "#000000"))
        color2 = str(grad.get("color2", "#000000"))
        angle = float(grad.get("angle", 90))

        silhouette = build_frame_silhouette(
            output_width, output_height, stroke, radius
        )
        return apply_gradient_to_silhouette(silhouette, color1, color2, angle)

    raise ValueError(
        f"frame.add must be 'file', 'create' or false. Got: {add!r}"
    )


def process_icon(
    input_path: Path,
    output_path: Path,
    output_width: int,
    output_height: int,
    padding_percent: float,
    apply_recolor: bool,
    recolor_color1: str,
    recolor_color2: str,
    recolor_angle: float,
    frame: Image.Image | None,
) -> None:
    """Procesa un único icono y lo guarda en `output_path`."""
    # 1. Cargar (rasteriza SVG a alta resolución desde el vector).
    img = load_image(input_path, raster_size=max(output_width, output_height) * 2)

    # 2. Autocrop sobre el bounding box real del contenido.
    img = autocrop(img)

    # 3. Caja útil dentro del lienzo de salida.
    cx, cy, cw, ch = compute_inner_box(output_width, output_height, padding_percent)

    # 4. Encuadre "contain" dentro de la caja útil.
    fitted = fit_contain(img, cw, ch)

    # Lienzo final del tamaño de salida con el icono ya posicionado.
    icon_layer = Image.new("RGBA", (output_width, output_height), (0, 0, 0, 0))
    icon_layer.paste(fitted, (cx, cy), fitted)

    # 5. Recoloreo del icono con su propio gradiente (si aplica).
    if apply_recolor:
        icon_layer = apply_gradient_to_silhouette(
            icon_layer, recolor_color1, recolor_color2, recolor_angle
        )

    # 6. Marco (o lienzo transparente si frame is None).
    if frame is not None:
        base = frame.copy()
        base.alpha_composite(icon_layer)
        result = base
    else:
        result = icon_layer

    # 7. Guardar.
    save_png(result, output_path)


def iter_icons(input_dir: Path):
    """Itera recursivamente sobre los iconos soportados de la carpeta."""
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and is_supported_image(path):
            yield path


def main(config_path: Path = DEFAULT_CONFIG) -> int:
    cfg = load_config(config_path)

    output_cfg = cfg.get("output", {}) or {}
    output_width = int(output_cfg.get("width", 1024))
    output_height = int(output_cfg.get("height", 1024))

    input_dir = Path(cfg.get("input", "./icons")).expanduser()
    output_dir = Path(cfg.get("output_dir", "./output")).expanduser()

    # Carpeta cuyos iconos reciben gradiente. Ruta INDEPENDIENTE.
    recolor_raw = cfg.get("recolor_dir")
    recolor_dir = Path(recolor_raw).expanduser() if recolor_raw else None

    # Salida separada para recoloreados, evita colisiones de nombre.
    recolor_out_raw = cfg.get("output_recolor_dir")
    output_recolor_dir = (
        Path(recolor_out_raw).expanduser() if recolor_out_raw else None
    )

    padding_percent = float(cfg.get("padding_percent", 25))

    # Gradiente del recoloreo del icono (independiente del del marco).
    rec_grad = cfg.get("recolor_gradient", {}) or {}
    recolor_color1 = str(rec_grad.get("color1", "#000000"))
    recolor_color2 = str(rec_grad.get("color2", "#000000"))
    recolor_angle = float(rec_grad.get("angle", 90))

    if not input_dir.exists():
        print(f"ERROR: input folder does not exist: {input_dir}", file=sys.stderr)
        return 1

    # Resolver marco (file / create / false) una sola vez.
    try:
        frame_img = build_frame_image(
            cfg.get("frame", {}) or {}, output_width, output_height
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Plan de trabajos: (icono, base_dir, base_out_dir, apply_recolor).
    jobs: list[tuple[Path, Path, Path, bool]] = []

    input_resolved = input_dir.resolve()
    for p in iter_icons(input_dir):
        jobs.append((p, input_resolved, output_dir, False))

    if recolor_dir is not None and recolor_dir.exists():
        if output_recolor_dir is None:
            print(
                "ERROR: recolor_dir is set but output_recolor_dir is not.",
                file=sys.stderr,
            )
            return 1
        recolor_resolved = recolor_dir.resolve()
        for p in iter_icons(recolor_dir):
            # Si recolor_dir está dentro de input_dir, ese icono ya entró
            # como no-recoloreado en el primer loop: lo quitamos y lo
            # añadimos como recoloreado para que no se duplique.
            jobs = [j for j in jobs if j[0].resolve() != p.resolve()]
            jobs.append((p, recolor_resolved, output_recolor_dir, True))

    processed = 0
    errors = 0
    for in_path, base_dir, base_out_dir, apply_recolor in jobs:
        try:
            rel = in_path.resolve().relative_to(base_dir)
            out_path = (base_out_dir / rel).with_suffix(".png")

            process_icon(
                input_path=in_path,
                output_path=out_path,
                output_width=output_width,
                output_height=output_height,
                padding_percent=padding_percent,
                apply_recolor=apply_recolor,
                recolor_color1=recolor_color1,
                recolor_color2=recolor_color2,
                recolor_angle=recolor_angle,
                frame=frame_img,
            )
            processed += 1
            print(f"OK  {rel}  ->  {out_path}")
        except FileNotFoundError as e:
            errors += 1
            print(f"ERR {in_path}: file not found: {e}", file=sys.stderr)
        except ValueError as e:
            errors += 1
            print(f"ERR {in_path}: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"ERR {in_path}: {type(e).__name__}: {e}", file=sys.stderr)

    print(f"\nDone. Processed: {processed}. Errors: {errors}.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    sys.exit(main(cfg_path))
