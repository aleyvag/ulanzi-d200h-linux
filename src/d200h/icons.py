"""Procesado de iconos: redimensiona PNG a 196x196 RGBA.

196×196 es la resolución oficial de las teclas LCD del D200H (confirmado por
documentación del fabricante). Los iconos de fábrica miden eso y pesan 30-40
KB. Iconos a 64×64 son aceptados por el parser JSON pero la UI los reemplaza
por un cuadrado de color con texto fallback.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

ICON_SIZE = 196


class IconError(RuntimeError):
    pass


def _render(src: Path, size: int) -> Image.Image:
    if not src.exists():
        raise IconError(f"Icono no encontrado: {src}")
    img = Image.open(src).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def _save_kwargs() -> dict:
    # optimize + compress_level=9 para bajar el ZIP final: páginas
    # importadas del software oficial llegan a ~199 KB y el firmware
    # rechaza ZIPs por encima de ~196 KB. Con esto bajan ~30%.
    return {"optimize": True, "compress_level": 9}


def prepare(src: Path, dst: Path, size: int = ICON_SIZE) -> Path:
    """Carga `src`, redimensiona a `size` y guarda PNG RGBA en `dst`."""
    try:
        img = _render(src, size)
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "PNG", **_save_kwargs())
    except IconError:
        raise
    except Exception as exc:
        raise IconError(f"Error procesando {src}: {exc}") from exc
    return dst


def prepare_bytes(src: Path, size: int = ICON_SIZE) -> bytes:
    """Como `prepare` pero devuelve los bytes del PNG sin tocar disco.

    Evita escribir un temporal junto al `src`: así referenciar iconos
    desde carpetas de sólo-lectura (fuera de `config/icons/`) funciona.
    """
    try:
        img = _render(src, size)
        buf = io.BytesIO()
        img.save(buf, "PNG", **_save_kwargs())
        return buf.getvalue()
    except IconError:
        raise
    except Exception as exc:
        raise IconError(f"Error procesando {src}: {exc}") from exc
