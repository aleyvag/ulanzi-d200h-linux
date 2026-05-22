"""Compilación de páginas a ZIPs HID.

Las páginas se transmiten al D200H por HID en tiempo real (ver
`bridge.py`); no hay deploy persistente en el dispositivo.

Funciones:
  - `compile_all()` → dict {page_id: ZipBytes} listo para enviar por HID.
  - `compile_page(page_id)` → bytes del ZIP de UNA página.
"""
from __future__ import annotations

import logging
import time

from . import pages, zip_pack
from .config import ConfigError

log = logging.getLogger("d200h.deploy")


def compile_page(page_id: str) -> bytes:
    """Compila UNA página y devuelve los bytes del ZIP HID."""
    all_pages = pages.load_all()
    if page_id not in all_pages:
        raise ConfigError(
            f"page_id '{page_id}' no existe. Disponibles: {sorted(all_pages)}"
        )
    page = all_pages[page_id]
    manifest_dict, icon_blobs = pages.compile_page(page)
    return zip_pack.pack(manifest_dict, icon_blobs)


def compile_all() -> dict[str, bytes]:
    """Compila TODAS las page_*.yaml. Devuelve {page_id: zip_bytes}."""
    started = time.monotonic()
    all_pages = pages.load_all()
    out: dict[str, bytes] = {}
    for pid, page in all_pages.items():
        manifest_dict, icon_blobs = pages.compile_page(page)
        out[pid] = zip_pack.pack(manifest_dict, icon_blobs)
    log.info("compile_all: %d páginas en %.2fs",
             len(out), time.monotonic() - started)
    return out
