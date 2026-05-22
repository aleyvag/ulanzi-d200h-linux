"""Carga y compilación de páginas YAML al modelo HID.

Schema del page YAML:

```yaml
# config/pages/page_NAME.yaml
title: "HOME"                 # opcional, descriptivo
brightness: 60                # opcional, 0-100 (aplicado por bridge si != None)
slots:
  "0_0":                      # clave "row_col" (fila primero, columna después)
    fw_action: page.goto      # uno de los Actions del firmware (ver manifest.FW_ACTIONS)
    fw_param: {Page: 1}       # ActionParam para esa Action; opcional
    icon: btn_goToPage        # nombre lógico, ver §icons
    text: ""                  # texto bajo el icono (opcional)
    font: {Align: center, ...}# opcional, mismo dict que la app oficial
    name: "HOTKEYS"           # opcional, descriptivo
    host_action:              # opcional. Lo que el bridge ejecuta al pulsar
      type: page
      target: hotkeys
  "0_1":
    icon: ctrl_s
    host_action:
      type: keys
      keys: "ctrl+s"
```

Reglas:
  - `fw_action` por defecto = `system.open` con Path vacío (slot pasivo
    que sólo pinta). El bridge se encarga de actuar al recibir el IN.
  - Si `fw_action` ∈ {page.goto, page.folder, page.back}, el host_action
    se ignora — el bridge resuelve la nav usando el `fw_param`. Para
    navegación recomendada: `page.goto` con `Page: "<page_id>"` explícito.
    `page.next`, `page.prev` y `page.switch` están deprecadas; el loader
    las rechaza apuntando al equivalente moderno.
  - `icon`: el loader busca primero en `config/icons/<nombre>.png`,
    luego en `config/icons/_firmware/<nombre>.png` (los btn_*.png que
    extrajimos del pack factory).
  - Slots `2_3` y `2_4` están prohibidos (reservados al firmware — reloj).

Slots no listados quedan como vacíos (no pintan).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import config, manifest

log = logging.getLogger("d200h.pages")


# Tipos de host_action válidos (uno por tarea, sin alias).
VALID_HOST_TYPES = {
    "shell", "keys", "text",
    "media", "volume", "brightness_host", "brightness_device",
    "app", "close", "url", "system",
    "page", "multi", "notify", "stub", "spotify",
    "delay",
}


class PageError(RuntimeError):
    pass


@dataclass
class Slot:
    """Slot completo, listo para compilar a manifest y mapear a host_action."""
    slot_id: int                            # 0..12
    fw_action: str = "system.open"          # sin prefijo
    fw_param: dict[str, Any] = field(default_factory=dict)
    icon: Optional[str] = None              # nombre lógico
    icon_generate: Optional[dict[str, Any]] = None  # spec del generador (text, color, fg)
    text: str = ""
    font: Optional[dict[str, Any]] = None
    name: str = ""
    host_action: Optional[dict[str, Any]] = None

    @property
    def has_default_fw_param(self) -> bool:
        return self.fw_action == "system.open" and not self.fw_param

    def effective_fw_param(self) -> dict[str, Any]:
        # `system.open` necesita `Path` para no fallar — relleno con vacío.
        if self.fw_action == "system.open" and "Path" not in self.fw_param:
            return {**self.fw_param, "Path": ""}
        return dict(self.fw_param)


@dataclass
class Page:
    """Página resuelta: identidad + brightness + slots."""
    page_id: str                            # ej. "home", "hotkeys" (stem del yaml)
    path: Path                              # ubicación del yaml
    title: str = ""
    brightness: Optional[int] = None
    slots: dict[int, Slot] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resolución de iconos
# ---------------------------------------------------------------------------

def _firmware_icons_dir() -> Path:
    """Pack factory `btn_*` que se sirve con el repo: config/icons/_firmware/."""
    return config.icons_dir() / "_firmware"


def resolve_icon(name: Optional[str]) -> Optional[Path]:
    """Devuelve la ruta absoluta del PNG referenciado por `name`.

    Búsqueda:
      1. `config/icons/<name>.png` (iconos del usuario).
      2. `config/icons/_firmware/<name>.png` (pack factory `btn_*`).

    Acepta `name` con o sin sufijo `.png`. Devuelve None si name es None.
    Lanza PageError si no se encuentra.
    """
    if not name:
        return None
    base = name if name.endswith(".png") else f"{name}.png"
    candidates = [
        config.icons_dir() / base,
        _firmware_icons_dir() / base,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise PageError(
        f"icono '{name}' no encontrado. Buscado en: "
        + ", ".join(str(c) for c in candidates)
    )


def icon_arcname(name: str, icon_path: Path) -> str:
    """Path dentro del ZIP que usaremos para el icono.

    - Si el archivo viene del pack `config/icons/_firmware/`, se mete bajo
      `com.ulanzi.deck.page/Images/` para imitar la convención factory.
    - Si viene de `config/icons/` (icono del usuario), bajo `Images/`.
    """
    if icon_path.parent == _firmware_icons_dir():
        return f"com.ulanzi.deck.page/Images/{icon_path.name}"
    return f"Images/{icon_path.name}"


# ---------------------------------------------------------------------------
# Carga YAML
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PageError(f"page yaml no encontrado: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PageError(f"YAML inválido en {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PageError(f"{path}: raíz debe ser mapping")
    return data


def _parse_slot(slot_key: str, raw: dict[str, Any], where: str) -> Slot:
    if not isinstance(raw, dict):
        raise PageError(f"{where}: cada slot debe ser un mapping")
    try:
        sid = manifest.id_from_key(slot_key)
    except ValueError as exc:
        raise PageError(f"{where}: {exc}") from exc

    fw_action = str(raw.get("fw_action", "system.open"))
    if fw_action in manifest.DEPRECATED_FW_ACTIONS:
        raise PageError(
            f"{where}: fw_action {fw_action!r} está deprecado. "
            f"Usa: {manifest.DEPRECATED_FW_ACTIONS[fw_action]}"
        )
    if fw_action not in manifest.FW_ACTIONS:
        raise PageError(
            f"{where}: fw_action {fw_action!r} desconocido. "
            f"Válidos: {sorted(manifest.FW_ACTIONS)}"
        )

    fw_param = dict(raw.get("fw_param") or {})

    host_action = raw.get("host_action")
    if host_action is not None:
        if not isinstance(host_action, dict):
            raise PageError(f"{where}: host_action debe ser mapping")
        h_type = host_action.get("type")
        if h_type not in VALID_HOST_TYPES:
            raise PageError(
                f"{where}: host_action.type {h_type!r} inválido. "
                f"Válidos: {sorted(VALID_HOST_TYPES)}"
            )

    icon_raw = raw.get("icon")
    icon_generate = raw.get("icon_generate")
    if icon_generate is not None:
        if not isinstance(icon_generate, dict):
            raise PageError(f"{where}: icon_generate debe ser mapping")
        if icon_raw is not None:
            raise PageError(
                f"{where}: usa `icon` o `icon_generate`, no ambos en el mismo slot"
            )
        text_spec = icon_generate.get("text")
        if not isinstance(text_spec, str) or not text_spec.strip():
            raise PageError(
                f"{where}: icon_generate.text es obligatorio y no puede estar vacío"
            )

    return Slot(
        slot_id=sid,
        fw_action=fw_action,
        fw_param=fw_param,
        icon=icon_raw,
        icon_generate=icon_generate,
        text=str(raw.get("text", "")),
        font=raw.get("font"),
        name=str(raw.get("name", "")),
        host_action=host_action,
    )


def load_page(path: Path) -> Page:
    data = _load_yaml(path)
    page_id = path.stem
    if page_id.startswith("page_"):
        page_id = page_id[len("page_"):]

    title = str(data.get("title", page_id))
    brightness = data.get("brightness")
    if brightness is not None and not (
        isinstance(brightness, int) and 0 <= brightness <= 100
    ):
        raise PageError(f"{path}: brightness debe ser int 0-100, no {brightness!r}")

    slots_raw = data.get("slots") or {}
    if not isinstance(slots_raw, dict):
        raise PageError(f"{path}: `slots` debe ser mapping")

    slots: dict[int, Slot] = {}
    for k, v in slots_raw.items():
        slot = _parse_slot(str(k), v, f"{path}[slots][{k}]")
        if slot.slot_id in slots:
            raise PageError(f"{path}: slot duplicado {k}")
        slots[slot.slot_id] = slot

    return Page(page_id=page_id, path=path, title=title,
                brightness=brightness, slots=slots)


def list_page_files() -> list[Path]:
    """Lista los `page_*.yaml` activos.

    Prioridad:
      1. `config/pages/user/`   — manifests del usuario (cargados por el bridge).
      2. `config/pages/examples/` — fallback si user/ está vacío.
      3. `config/pages/` plano   — compat con instalaciones viejas; emite warning.
    """
    pdir = config.pages_dir()
    if not pdir.is_dir():
        return []

    user_dir = pdir / "user"
    user_pages = sorted(user_dir.glob("page_*.yaml")) if user_dir.is_dir() else []
    if user_pages:
        return user_pages

    examples_dir = pdir / "examples"
    examples_pages = sorted(examples_dir.glob("page_*.yaml")) if examples_dir.is_dir() else []

    flat_pages = sorted(p for p in pdir.glob("page_*.yaml"))
    if flat_pages:
        log.warning(
            "Hay page_*.yaml directamente en %s; muévelos a %s/ para silenciar este aviso.",
            pdir, user_dir,
        )

    return flat_pages or examples_pages


def load_all() -> dict[str, Page]:
    """Carga todas las páginas. Devuelve dict {page_id: Page}."""
    result: dict[str, Page] = {}
    for path in list_page_files():
        page = load_page(path)
        if page.page_id in result:
            raise PageError(f"page_id duplicado: {page.page_id}")
        result[page.page_id] = page
    return result


# ---------------------------------------------------------------------------
# Compilación Page → (manifest dict, icons map)
# ---------------------------------------------------------------------------

def _blank_icon_bytes() -> bytes:
    """PNG 196×196 totalmente transparente.

    Sirve como placeholder cuando un slot tiene `text` pero ningún
    `icon`: el firmware sólo pinta el `Text` si hay un `Icon` presente
    en el ZIP, así que le metemos uno invisible y dejamos que el texto
    sea lo único visible.

    Crítico (confirmado experimentalmente 2026-05-17): el firmware
    **rechaza silenciosamente** el render de una página si dos o más
    slots referencian exactamente la misma ruta de Icon. Por eso cada
    slot text-only recibe su propia copia del placeholder bajo una
    ruta única (`Images/_blank_<slot_id>.png`). El contenido del PNG
    es idéntico — sólo el path difiere.
    """
    from PIL import Image
    img = Image.new("RGBA", (196, 196), (0, 0, 0, 0))
    import io as _io
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _blank_arcname(slot_id: int) -> str:
    return f"Images/_blank_{slot_id}.png"


def compile_page(page: Page) -> tuple[dict, dict[str, bytes]]:
    """Genera el dict de manifest.json y el dict {arcname: png_bytes}.

    Reutiliza `icons.prepare_bytes` para redimensionar a 196x196 RGBA en
    memoria cuando sea necesario (si el PNG ya es del pack factory lo
    pasamos tal cual).

    Si un slot tiene `text` pero ningún `icon`, se le asocia un
    placeholder transparente único por slot — el firmware no pinta el
    `Text` solo, así que el icono "invisible" hace que el texto se muestre.

    Si un slot trae `icon_generate`, el bridge genera (o reutiliza desde
    cache) un PNG con marco + texto y lo usa como icono. Cache en
    `config/icons/__generated__/<hash>.png`.

    La regla "no duplicados de Icon en una misma página" la enforza el
    loader (`load_page`) emitiendo `PageError`. Aquí se asume que la
    página ya cumple esa precondición.
    """
    from . import icons

    entries: dict[int, dict] = {}
    icon_blobs: dict[str, bytes] = {}

    for sid, slot in page.slots.items():
        icon_path: Optional[Path] = None
        if slot.icon_generate is not None:
            from . import icon_gen
            icon_path = icon_gen.ensure_cached(config.icons_dir(),
                                               slot.icon_generate)
        elif slot.icon:
            icon_path = resolve_icon(slot.icon)
        arcname = ""
        if icon_path is not None:
            if slot.icon_generate is not None:
                # Arcname incluye slot_id para evitar colisiones cuando
                # dos slots comparten el mismo hash (mismo text+color).
                arcname = f"Images/__gen_{icon_path.stem}_{sid}.png"
                icon_blobs[arcname] = icon_path.read_bytes()
            else:
                arc = icon_arcname(slot.icon or icon_path.stem, icon_path)
                # Pack factory: aceptar tal cual (ya está a 196×196).
                # Iconos del usuario: redimensionar/convertir en memoria
                # (sin temporal en disco, así carpetas de sólo-lectura valen).
                if icon_path.parent == _firmware_icons_dir():
                    icon_blobs[arc] = icon_path.read_bytes()
                else:
                    icon_blobs[arc] = icons.prepare_bytes(icon_path)
                arcname = arc
        elif slot.text:
            # Sin icono explícito pero con texto → placeholder transparente
            # único por slot.
            arcname = _blank_arcname(sid)
            icon_blobs[arcname] = _blank_icon_bytes()

        entry = manifest.slot_entry(
            slot.fw_action,
            action_param=slot.effective_fw_param(),
            icon_arcname=arcname,
            text=slot.text,
            font=slot.font,
            name=slot.name,
        )
        entries[sid] = entry

    return manifest.build(entries), icon_blobs
