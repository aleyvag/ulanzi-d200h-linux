"""Cambio automático de página según el foco de ventana.

Lee `config/focus_rules.yaml` y arranca un watcher que vigila qué
ventana tiene el foco. Cuando el WM_CLASS de la nueva ventana
coincide con una regla, solicita al bridge cambiar de página.

Backend actual: **X11** vía `xprop -spy -root _NET_ACTIVE_WINDOW`
(stream-based, sin polling). Para Wayland habría que añadir backends
por compositor (KDE DBus, GNOME extension, Sway/Hyprland IPC) — fuera
de scope por ahora.

El watcher **sólo emite cambios** cuando la WM_CLASS cambia entre
ventanas consecutivas. Eventos de la misma clase no re-disparan: así
el usuario puede navegar manualmente dentro de la página activa sin
pelearse con el watcher.

Para descubrir el WM_CLASS de una ventana:
    xprop WM_CLASS    # luego clickea la ventana
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

log = logging.getLogger("d200h.focus")


# ---------------------------------------------------------------------------
# Modelo + parser de config
# ---------------------------------------------------------------------------

@dataclass
class FocusRule:
    match: str          # substring case-insensitive contra WM_CLASS
    page: str           # page_id destino


@dataclass
class FocusConfig:
    default: Optional[str] = None        # a dónde volver si ninguna regla matchea
    rules: list[FocusRule] = field(default_factory=list)


def load_rules(path: Path) -> Optional[FocusConfig]:
    """Carga `focus_rules.yaml`. Devuelve None si el archivo no existe
    (feature desactivada). Reglas inválidas se omiten con warning."""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("focus_rules.yaml inválido: %s", exc)
        return None
    rules: list[FocusRule] = []
    for entry in raw.get("rules", []) or []:
        if not isinstance(entry, dict):
            continue
        m = entry.get("match")
        p = entry.get("page")
        if not (isinstance(m, str) and isinstance(p, str)):
            log.warning("focus rule inválida (requiere match+page): %r", entry)
            continue
        rules.append(FocusRule(match=m, page=p))
    default = raw.get("default")
    if default is not None and not isinstance(default, str):
        log.warning("focus_rules.default %r no es string; ignorado", default)
        default = None
    return FocusConfig(default=default, rules=rules)


def resolve(cfg: FocusConfig, wm_class: Optional[str],
            available_pages: set[str]) -> Optional[str]:
    """Resuelve qué page_id corresponde al WM_CLASS actual.

    - Si alguna regla matchea (substring case-insensitive) y la page
      destino existe, la devuelve.
    - Si nada matchea y `cfg.default` existe en `available_pages`, lo
      devuelve.
    - Si nada matchea y no hay default → None (no cambiar de página).
    """
    if wm_class:
        wm_lower = wm_class.lower()
        for r in cfg.rules:
            if r.match.lower() in wm_lower:
                if r.page in available_pages:
                    return r.page
                log.warning("focus rule match=%r apunta a page %r que no existe",
                            r.match, r.page)
                return None
    if cfg.default and cfg.default in available_pages:
        return cfg.default
    if cfg.default and cfg.default not in available_pages:
        log.warning("focus_rules.default %r no existe en las páginas compiladas",
                    cfg.default)
    return None


# ---------------------------------------------------------------------------
# Backend X11
# ---------------------------------------------------------------------------

_WINDOW_RE = re.compile(r"#\s*(0x[0-9a-fA-F]+)")
_WM_CLASS_RE = re.compile(r'WM_CLASS\([^)]*\)\s*=\s*"([^"]*)",\s*"([^"]*)"')


def _query_wm_class(window_id: str) -> Optional[str]:
    """Lee WM_CLASS de un window id X11. Devuelve la 2ª string (CLASS).

    Salida típica de `xprop -id <id> WM_CLASS`:
        WM_CLASS(STRING) = "code", "Code"
    Se devuelve el 2º token ("Code") porque la CLASS es más estable que
    la instance entre lanzamientos.
    """
    if not window_id or window_id == "0x0":
        return None
    try:
        out = subprocess.run(
            ["xprop", "-id", window_id, "WM_CLASS"],
            capture_output=True, text=True, timeout=1.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    m = _WM_CLASS_RE.search(out.stdout)
    if m:
        return m.group(2)
    return None


class X11FocusWatcher:
    """Watcher de _NET_ACTIVE_WINDOW basado en `xprop -spy`.

    Arranca un subproceso `xprop -spy -root _NET_ACTIVE_WINDOW` en un
    thread daemon y llama al callback cada vez que la WM_CLASS de la
    ventana focused **cambia**. El callback recibe la nueva WM_CLASS
    (o None si no hay ventana con foco).
    """

    def __init__(self, callback: Callable[[Optional[str]], None]) -> None:
        self._cb = callback
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Arranca el watcher. Devuelve False si X11/xprop no están listos."""
        if not shutil.which("xprop"):
            log.warning("xprop no encontrado → focus-switch deshabilitado "
                        "(instala paquete `xprop` o `x11-utils`).")
            return False
        if not os.environ.get("DISPLAY"):
            log.warning("DISPLAY no definido → focus-switch deshabilitado.")
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="d200h-focus", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            log.error("focus watcher: no se pudo arrancar xprop: %s", exc)
            return

        # Sentinel distinto de cualquier valor real para forzar el primer
        # callback. Tras eso, sólo dispara cuando cambia la clase.
        last_class: object = object()
        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                m = _WINDOW_RE.search(line)
                if not m:
                    continue
                wm_class = _query_wm_class(m.group(1))
                if wm_class == last_class:
                    continue
                last_class = wm_class
                log.debug("focus → WM_CLASS=%r", wm_class)
                try:
                    self._cb(wm_class)
                except Exception:
                    log.exception("focus callback error")
        finally:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
