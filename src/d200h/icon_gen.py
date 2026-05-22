"""Generador de iconos: PNG 196×196 RGBA con marco + texto centrado.

Mimica el estilo de los botones de `page_home.yaml` (tarjeta con esquinas
redondeadas, fondo azul, texto blanco). Se invoca declarativamente desde
el YAML del slot:

    "0_0":
      icon_generate:
        text: "./run.sh"      # obligatorio
        color: "#1a4f8a"      # opcional, fondo
        fg: "#ffffff"         # opcional, color del texto
      host_action: {type: shell, cmd: "./run.sh"}

El PNG se cachea en `config/icons/__generated__/<hash16>.png`. Si el
usuario cambia `text` o cualquier campo de `icon_generate`, el hash
cambia y se genera un PNG nuevo; el viejo queda como huérfano (limpiable
con `d200h icon-gen --gc`).

El YAML es source of truth: no se muta. El cache es derivable.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("d200h.icon_gen")

ICON_SIZE = 196
DEFAULT_BG = "#1a4f8a"
DEFAULT_FG = "#ffffff"
PADDING = 14
CORNER_RADIUS = 22

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
]
_cached_font_path: Optional[str] = None


def _find_font() -> Optional[str]:
    global _cached_font_path
    if _cached_font_path is not None:
        return _cached_font_path or None
    for c in _FONT_CANDIDATES:
        if Path(c).is_file():
            _cached_font_path = c
            log.debug("fuente seleccionada: %s", c)
            return c
    log.warning("Ninguna fuente TTF estándar encontrada; cayendo a "
                "ImageFont.load_default() (texto pixelado). Instala "
                "fonts-dejavu o fonts-liberation para mejor render.")
    _cached_font_path = ""
    return None


def _load_font(size: int):
    path = _find_font()
    if path:
        return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Wrap por palabras; si una palabra sola excede, se parte por chars."""
    words = re.split(r"\s+", text.strip())
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        candidate = " ".join(cur + [w]) if cur else w
        b = draw.textbbox((0, 0), candidate, font=font)
        if b[2] - b[0] <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    result: list[str] = []
    for line in lines:
        b = draw.textbbox((0, 0), line, font=font)
        if b[2] - b[0] <= max_width:
            result.append(line)
            continue
        # Palabra demasiado larga: chunk por chars.
        chunk = ""
        for ch in line:
            test = chunk + ch
            b = draw.textbbox((0, 0), test, font=font)
            if b[2] - b[0] <= max_width:
                chunk = test
            else:
                if chunk:
                    result.append(chunk)
                chunk = ch
        if chunk:
            result.append(chunk)
    return result


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int):
    """Busca el font_size más grande tal que el texto cabe."""
    for size in range(48, 11, -3):
        font = _load_font(size)
        lines = _wrap(text, font, max_w, draw)
        ref = draw.textbbox((0, 0), "Ay", font=font)
        line_h = int((ref[3] - ref[1]) * 1.15)
        total_h = line_h * len(lines)
        max_w_real = max((draw.textbbox((0, 0), ln, font=font)[2]
                          - draw.textbbox((0, 0), ln, font=font)[0])
                         for ln in lines)
        if total_h <= max_h and max_w_real <= max_w:
            return font, lines, line_h
    # Fallback al más pequeño aunque no quepa.
    font = _load_font(12)
    lines = _wrap(text, font, max_w, draw)
    ref = draw.textbbox((0, 0), "Ay", font=font)
    return font, lines, int((ref[3] - ref[1]) * 1.15)


def render(text: str, color: str = DEFAULT_BG, fg: str = DEFAULT_FG,
           size: int = ICON_SIZE) -> bytes:
    """Devuelve PNG bytes 196×196 RGBA con la tarjeta + texto centrado."""
    if not text:
        text = "?"
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1),
                           radius=CORNER_RADIUS, fill=color)

    inner_w = size - PADDING * 2
    inner_h = size - PADDING * 2
    font, lines, line_h = _fit_text(draw, text, inner_w, inner_h)
    total_h = line_h * len(lines)
    y = (size - total_h) // 2
    for line in lines:
        b = draw.textbbox((0, 0), line, font=font)
        line_w = b[2] - b[0]
        x = (size - line_w) // 2 - b[0]
        draw.text((x, y - b[1]), line, font=font, fill=fg)
        y += line_h

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def spec_hash(spec: dict) -> str:
    """Hash estable y corto de la spec. Sirve como nombre de archivo."""
    norm = {k: v for k, v in spec.items() if v is not None and v != ""}
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def cache_dir(icons_root: Path) -> Path:
    return icons_root / "__generated__"


def cached_path(icons_root: Path, spec: dict) -> Path:
    return cache_dir(icons_root) / f"{spec_hash(spec)}.png"


def ensure_cached(icons_root: Path, spec: dict) -> Path:
    """Garantiza que existe el PNG para `spec`; lo genera si falta."""
    out = cached_path(icons_root, spec)
    if out.is_file():
        return out
    text = str(spec.get("text") or "").strip()
    color = str(spec.get("color") or DEFAULT_BG)
    fg = str(spec.get("fg") or DEFAULT_FG)
    png = render(text=text, color=color, fg=fg)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    log.info("icon_gen → cached %s (%d B): %s", out.name, len(png), text[:40])
    return out


def gc(icons_root: Path, *, used_hashes: set[str]) -> tuple[int, int]:
    """Borra de __generated__/ los PNGs no referenciados.

    Devuelve (kept, deleted).
    """
    cdir = cache_dir(icons_root)
    if not cdir.is_dir():
        return (0, 0)
    kept = 0
    deleted = 0
    for p in cdir.glob("*.png"):
        if p.stem in used_hashes:
            kept += 1
            continue
        try:
            p.unlink()
            deleted += 1
            log.info("icon_gen gc → borrado %s", p.name)
        except OSError as exc:
            log.warning("no pude borrar %s: %s", p, exc)
    return kept, deleted
