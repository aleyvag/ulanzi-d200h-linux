"""
Utilidades compartidas para el procesamiento de iconos.

Este módulo NO contiene lógica de negocio propia; solo funciones
reutilizables por `frame.py` y `compose.py`:
    - Detección de tipo de archivo (SVG vs PNG/WebP).
    - Conversión SVG -> PNG rasterizada a alta resolución desde el vector.
    - Autocrop (recorte del espacio transparente alrededor).
    - Encuadre "contain" (escalado proporcional dentro de una caja).
    - Generación de máscara con gradiente lineal de dos colores.
    - Carga y guardado de PNG con transparencia.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw

try:
    import cairosvg  # Conversión SVG -> PNG vectorial
except ImportError as e:  # pragma: no cover
    cairosvg = None
    _CAIROSVG_ERROR = e
else:
    _CAIROSVG_ERROR = None


# Extensiones admitidas
SVG_EXTS = {".svg"}
RASTER_EXTS = {".png", ".webp"}


def is_svg(path: Path) -> bool:
    """Devuelve True si la ruta apunta a un archivo SVG por extensión."""
    return path.suffix.lower() in SVG_EXTS


def is_raster(path: Path) -> bool:
    """Devuelve True si la ruta apunta a un raster soportado (PNG o WebP)."""
    return path.suffix.lower() in RASTER_EXTS


def is_supported_image(path: Path) -> bool:
    """True si el archivo es SVG, PNG o WebP."""
    return is_svg(path) or is_raster(path)


def svg_to_png(svg_path: Path, target_size: int) -> Image.Image:
    """
    Convierte un SVG a PIL.Image RGBA rasterizando DESDE EL VECTOR a alta
    resolución. Nunca rasterizar pequeño y luego escalar: cairosvg recibe
    el tamaño final deseado y dibuja directamente a esa resolución.

    `target_size` es el lado mayor en píxeles al que se quiere rasterizar.
    """
    if cairosvg is None:
        raise RuntimeError(
            "cairosvg is not available. Install it and make sure libcairo "
            "is present on the system. Original error: %s" % _CAIROSVG_ERROR
        )
    if not svg_path.exists():
        raise FileNotFoundError(f"SVG not found: {svg_path}")

    # Rasteriza al tamaño objetivo (lado mayor). cairosvg conserva proporciones.
    png_bytes = cairosvg.svg2png(
        url=str(svg_path),
        output_width=target_size,
        output_height=target_size,
    )
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def load_image(path: Path, raster_size: int = 2048) -> Image.Image:
    """
    Carga una imagen como RGBA. Si es SVG, la rasteriza a alta resolución
    (`raster_size` lado mayor) directamente desde el vector.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if is_svg(path):
        return svg_to_png(path, raster_size)
    if is_raster(path):
        return Image.open(path).convert("RGBA")
    raise ValueError(f"Unsupported format: {path.suffix} ({path})")


def save_png(img: Image.Image, path: Path) -> None:
    """Guarda un PNG con transparencia. Crea el directorio si no existe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def autocrop(img: Image.Image) -> Image.Image:
    """
    Recorta el espacio transparente alrededor de la imagen para que el
    bounding box del contenido visible coincida con los bordes.

    Si la imagen no tiene canal alfa o está totalmente vacía, se devuelve
    sin cambios.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if bbox is None:
        return img
    return img.crop(bbox)


def fit_contain(img: Image.Image, box_width: int, box_height: int) -> Image.Image:
    """
    Escala `img` proporcionalmente para que QUEPA dentro de una caja de
    (box_width, box_height) sin deformar ni recortar (modo "contain"),
    y la devuelve como un nuevo RGBA del tamaño de la caja con el icono
    centrado sobre fondo transparente.

    Maneja iconos más anchos que altos y viceversa.
    """
    if img.width == 0 or img.height == 0:
        raise ValueError("Image has invalid dimensions (0).")

    factor = min(box_width / img.width, box_height / img.height)
    new_width = max(1, int(round(img.width * factor)))
    new_height = max(1, int(round(img.height * factor)))

    scaled = img.resize((new_width, new_height), Image.LANCZOS)
    canvas = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    off_x = (box_width - new_width) // 2
    off_y = (box_height - new_height) // 2
    canvas.paste(scaled, (off_x, off_y), scaled)
    return canvas


# -----------------------------------------------------------------------------
# Gradiente
# -----------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convierte '#RRGGBB' (o 'RRGGBB') a tupla (R, G, B)."""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}. Use #RRGGBB.")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _linear_gradient(
    width: int, height: int, color1_hex: str, color2_hex: str, angle_degrees: float
) -> Image.Image:
    """
    Genera una imagen RGB con un gradiente lineal de `color1` -> `color2`
    siguiendo la convención de ángulos del proyecto:

        0°   = color1 a la izquierda, viaja a la derecha
        90°  = color1 arriba, viaja hacia abajo
        180° = color1 a la derecha
        270° = color1 abajo, sube
        45°  = diagonal de esquina a esquina

    Si color1 == color2, devuelve un color plano (sin gradiente).
    """
    c1 = _hex_to_rgb(color1_hex)
    c2 = _hex_to_rgb(color2_hex)

    if c1 == c2:
        # Color plano: no hace falta calcular gradiente.
        return Image.new("RGB", (width, height), c1)

    # Vector de dirección del gradiente (dx, dy) en coordenadas de imagen
    # (y crece hacia abajo). Con esta convención:
    #   0°   -> (1, 0)
    #   90°  -> (0, 1)
    #   180° -> (-1, 0)
    #   270° -> (0, -1)
    rad = math.radians(angle_degrees)
    dx = math.cos(rad)
    dy = math.sin(rad)

    # Proyectamos cada píxel sobre el vector de dirección y normalizamos
    # entre [0, 1] usando los valores mínimo y máximo de la proyección
    # sobre el rectángulo, para que el gradiente vaya de borde a borde.
    import numpy as np

    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    xv, yv = np.meshgrid(xs, ys)
    proj = xv * dx + yv * dy

    pmin = proj.min()
    pmax = proj.max()
    if pmax - pmin == 0:
        t = np.zeros_like(proj)
    else:
        t = (proj - pmin) / (pmax - pmin)

    r = (c1[0] * (1 - t) + c2[0] * t).astype(np.uint8)
    g = (c1[1] * (1 - t) + c2[1] * t).astype(np.uint8)
    b = (c1[2] * (1 - t) + c2[2] * t).astype(np.uint8)
    arr = np.stack([r, g, b], axis=-1)
    return Image.fromarray(arr, mode="RGB")


def apply_gradient_to_silhouette(
    icon: Image.Image, color1_hex: str, color2_hex: str, angle_degrees: float
) -> Image.Image:
    """
    Toma `icon` (RGBA) y devuelve una nueva imagen RGBA donde la silueta
    del icono (su canal alfa) se rellena con un gradiente lineal de
    `color1` -> `color2` según `angle_degrees`. La forma/silueta del icono
    se conserva exactamente (mismo canal alfa).

    Si los dos colores son iguales, queda color plano.
    """
    if icon.mode != "RGBA":
        icon = icon.convert("RGBA")

    grad = _linear_gradient(
        icon.width, icon.height, color1_hex, color2_hex, angle_degrees
    ).convert("RGBA")

    # Forzamos el alfa del gradiente a coincidir con la silueta del icono.
    alpha = icon.split()[-1]
    grad.putalpha(alpha)
    return grad


def build_frame_silhouette(
    width: int, height: int, stroke: int, radius: int
) -> Image.Image:
    """
    Construye la silueta RGBA de un marco rectangular redondeado HUECO:
    rectángulo redondeado exterior menos rectángulo redondeado interior.

    El alfa marca el trazo del marco (grosor `stroke`, esquinas con radio
    exterior `radius`). El RGB queda en blanco (se reemplaza luego al
    aplicar el gradiente sobre la silueta).
    """
    if stroke <= 0:
        raise ValueError(f"frame stroke must be > 0, got {stroke}")
    if stroke * 2 >= min(width, height):
        raise ValueError(
            f"frame stroke ({stroke}) is too large for canvas {width}x{height}"
        )
    if radius < 0:
        raise ValueError(f"frame radius must be >= 0, got {radius}")

    # Trabajamos sobre un canal alfa puro (modo "L").
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    # Rectángulo exterior relleno.
    draw.rounded_rectangle(
        [(0, 0), (width - 1, height - 1)], radius=radius, fill=255,
    )

    # Rectángulo interior vaciado. El radio interior se reduce en `stroke`
    # para mantener un trazo de grosor uniforme en las esquinas.
    inner_radius = max(0, radius - stroke)
    draw.rounded_rectangle(
        [(stroke, stroke), (width - 1 - stroke, height - 1 - stroke)],
        radius=inner_radius, fill=0,
    )

    rgba = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    rgba.putalpha(mask)
    return rgba


def compute_inner_box(
    canvas_width: int, canvas_height: int, padding_percent: float
) -> Tuple[int, int, int, int]:
    """
    Calcula la caja útil interior (x, y, width, height) dentro del lienzo
    a partir de un padding porcentual POR LADO.

    Por ejemplo, padding_percent=25 deja una caja útil del 50% central.
    """
    if not 0 <= padding_percent < 50:
        raise ValueError(
            f"padding_percent must be in [0, 50). Got: {padding_percent}"
        )
    px = int(round(canvas_width * padding_percent / 100.0))
    py = int(round(canvas_height * padding_percent / 100.0))
    return px, py, canvas_width - 2 * px, canvas_height - 2 * py
