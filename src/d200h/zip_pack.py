"""Construye los ZIPs que se envían al D200H por HID.

Cada ZIP contiene:
  - `manifest.json` (descripción de los 13 slots)
  - PNGs de los iconos referenciados desde el manifest, con la misma
    ruta dentro del ZIP que la que aparece en `ViewParam[0].Icon`.

Convenciones observadas en los ZIPs de la app oficial:
  - Iconos de botones del sistema (next/prev/goto/folder/back/indicator/
    switchProfile): `com.ulanzi.deck.page/Images/<nombre>.png`.
  - Iconos de audio:                                  `com.ulanzi.deck.sound/Images/<nombre>.png`.
  - Iconos custom (usuario, websites, etc.):          `Images/<nombre>.png`.

Aquí usamos siempre `Images/<nombre>.png` para iconos de usuario y
`com.ulanzi.deck.page/Images/btn_*.png` para los del sistema (los
extraídos del pack factory). Compatible con la nomenclatura factory.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


SYSTEM_ICONS_PREFIX = "com.ulanzi.deck.page/Images/"
USER_ICONS_PREFIX = "Images/"


@dataclass
class PageZip:
    """Material listo para meter en un ZIP de página.

    `manifest` es el dict completo del `manifest.json` (claves
    `"row_col"` → entry). `icons` mapea cada `arcname` (path dentro del
    ZIP, p.ej. `"Images/firefox.png"`) a los bytes del PNG.
    """
    manifest: dict
    icons: dict[str, bytes] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        """Serializa a ZIP en memoria. Reproducible (sin timestamps)."""
        buf = io.BytesIO()
        # ZIP_STORED para evitar overhead de deflate en PNG ya comprimido;
        # los ZIPs factory tampoco usan compresión sobre PNG.
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Carpetas (entradas size=0 con barra final) — la app oficial
            # las incluye, así que las reproducimos para minimizar
            # diferencias de comportamiento.
            seen_dirs: set[str] = set()
            for arcname in sorted(self.icons.keys()):
                parts = arcname.split("/")
                for i in range(1, len(parts)):
                    d = "/".join(parts[:i]) + "/"
                    if d in seen_dirs:
                        continue
                    seen_dirs.add(d)
                    info = zipfile.ZipInfo(d)
                    info.external_attr = 0o40755 << 16
                    zf.writestr(info, b"")
            for arcname, png_bytes in sorted(self.icons.items()):
                zf.writestr(arcname, png_bytes)
            manifest_text = json.dumps(self.manifest, indent=2,
                                       ensure_ascii=False)
            zf.writestr("manifest.json", manifest_text.encode("utf-8"))
        return buf.getvalue()


def pack(manifest: dict, icons: Mapping[str, bytes]) -> bytes:
    """Atajo: construye un ZIP listo para enviar por HID."""
    return PageZip(manifest=dict(manifest), icons=dict(icons)).to_bytes()


def load_png(path: Path) -> bytes:
    """Lee bytes de un PNG ya preparado (196×196 esperado). Sin re-procesar.

    Si necesitas redimensionar/convertir RGBA, usa `d200h.icons.prepare`.
    """
    if not path.is_file():
        raise FileNotFoundError(f"icono no encontrado: {path}")
    return path.read_bytes()
