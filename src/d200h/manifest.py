"""Construcción del `manifest.json` que va dentro del ZIP HID.

Cada slot tiene una `fw_action` (Action que entiende el firmware) y un
icono. El firmware del D200H acepta más Actions de las que este bridge
expone — para detalle completo ver §5.1 del
[doc RE](../../docs/developer/firmware-protocol.md). El conversor resuelve
las acciones "frágiles" (page.next, page.prev, page.switch) a
`page.goto` con `Page:` explícito, así que aquí sólo aceptamos las que
de verdad tienen comportamiento útil:

  - com.ulanzi.ulanzideck.system.open       Path: token HID / launcher / ""
  - com.ulanzi.ulanzideck.page.goto         {Page: "<page_id>"}
  - com.ulanzi.ulanzideck.page.folder       {ProfileUUID: "<page_id>"}
  - com.ulanzi.ulanzideck.page.back         {}
  - com.ulanzi.ulanzideck.page.indicator    {} + ViewParam[0].Text="<n>"

`page.folder` se comporta igual que `page.goto` en este bridge (mismo
salto absoluto a page_id) pero se conserva por semántica — deja la
puerta abierta a darle pila propia en el futuro (que `page.back` salga
del folder, no de la navegación general).

Las dos que aparecen en docs antiguas (`keyboard.send`, `shell.exec`)
están NO implementadas en 2.0.3 — slot ignorado al pulsar. Para emitir
teclas usar tokens HID puros vía `system.open` (ctrlc, ctrls, …) o que
el bridge del host lo haga al recibir el IN.
"""
from __future__ import annotations

import uuid
from typing import Any

GRID_COLS = 5
GRID_ROWS = 3
# 5×3 = 15 celdas, pero el slot doble (firmware "3_2"+"4_2", YAML "2_3"+"2_4")
# está reservado al firmware (reloj/logo). 13 slots configurables, id = row*5+col.
#
# Hay DOS convenciones de clave:
#  - **YAML del bridge**: `"row_col"` (fila primero), id = row*5+col.
#    Ejemplos: 0=0_0, 1=0_1, 2=0_2, 3=0_3, 4=0_4, 5=1_0, ..., 12=2_2.
#    Es la que ve el usuario al editar `config/pages/user/*.yaml`.
#  - **Wire / manifest.json al firmware**: `"col_row"` (col primero) —
#    convención original del software oficial Ulanzi. El firmware parsea
#    las claves en este formato. El bridge convierte de row_col a col_row
#    al construir el manifest.
SLOTS = 13

# Claves reservadas tal cual aparecen en el YAML del bridge (row_col).
RESERVED_KEYS = {"2_3", "2_4"}

# Claves reservadas tal cual viajan al firmware en el manifest.json (col_row).
RESERVED_WIRE_KEYS = {"3_2", "4_2"}

PREFIX = "com.ulanzi.ulanzideck."

# Catálogo de fw_actions soportadas (sin prefijo). Cada valor describe
# qué `ActionParam` se espera; lo usa `validate_slot` en el loader.
FW_ACTIONS: dict[str, set[str]] = {
    "system.open":      {"Path"},
    "page.goto":        {"Page"},
    "page.folder":      {"ProfileUUID"},
    "page.back":        set(),
    "page.indicator":   set(),
}

# Acciones que el firmware acepta pero el bridge ya no expone:
# - page.switch/folder se resuelven en el conversor como page.goto
#   (mismo destino, sin la indirección por ProfileUUID).
# - page.next/page.prev dependen del orden alfabético global, lo que
#   resulta frágil cuando se añaden/quitan páginas; el conversor las
#   resuelve a page.goto con Page: explícito.
# Si un YAML usa una de estas, el loader devuelve un PageError con un
# apunte a la equivalente moderna.
DEPRECATED_FW_ACTIONS: dict[str, str] = {
    "page.next":   "page.goto con Page: explícito (el conversor ya lo resuelve)",
    "page.prev":   "page.goto con Page: explícito (el conversor ya lo resuelve)",
    "page.switch": "page.goto con Page: explícito (el bridge no diferencia switch de goto)",
}


def slot_key(slot_id: int) -> str:
    """`id` → clave `"col_row"` (formato wire, el que entiende el firmware).

    Esta clave es la que viaja al device en `manifest.json`. NO confundir
    con la clave de YAML del usuario: ésa es `"row_col"` (fila primero) —
    ver `yaml_key()`. La traducción se hace al construir el manifest.
    """
    if not 0 <= slot_id < SLOTS:
        raise ValueError(f"id fuera de rango (0-{SLOTS - 1}): {slot_id}")
    row = slot_id // GRID_COLS
    col = slot_id % GRID_COLS
    key = f"{col}_{row}"
    if key in RESERVED_WIRE_KEYS:
        raise ValueError(f"slot {key} reservado al firmware (wire)")
    return key


def yaml_key(slot_id: int) -> str:
    """`id` → clave `"row_col"` (formato YAML del bridge, fila primero).

    Sólo se usa para mensajes/diagnóstico orientados al usuario. La I/O
    real con el firmware pasa por `slot_key()` (col_row).
    """
    if not 0 <= slot_id < SLOTS:
        raise ValueError(f"id fuera de rango (0-{SLOTS - 1}): {slot_id}")
    row = slot_id // GRID_COLS
    col = slot_id % GRID_COLS
    key = f"{row}_{col}"
    if key in RESERVED_KEYS:
        raise ValueError(f"slot {key} reservado al firmware")
    return key


def id_from_key(key: str) -> int:
    """`"row_col"` (YAML del usuario, fila primero) → id físico.

    Útil para parsear `config/pages/user/*.yaml`. Rechaza los slots
    reservados al firmware (`2_3`, `2_4` — donde vive el reloj).
    """
    row_s, col_s = key.split("_")
    row, col = int(row_s), int(col_s)
    if not (0 <= col < GRID_COLS and 0 <= row < GRID_ROWS):
        raise ValueError(f"clave fuera de rango: {key!r}")
    if key in RESERVED_KEYS:
        raise ValueError(f"slot {key} reservado al firmware")
    return row * GRID_COLS + col


def _make_view_param(icon_arcname: str = "", text: str = "",
                     font: dict | None = None) -> list[dict]:
    vp: dict[str, Any] = {"Icon": icon_arcname, "Text": text}
    if font:
        vp["Font"] = font
    return [vp]


def slot_entry(fw_action: str, *,
               action_param: dict | None = None,
               icon_arcname: str = "",
               text: str = "",
               font: dict | None = None,
               name: str = "",
               state: int = 0) -> dict:
    """Construye el dict de UN slot listo para meter en el manifest.

    `fw_action` se da SIN prefijo (`page.next`, `system.open`, …);
    aquí lo prefijamos al valor con `com.ulanzi.ulanzideck.`.
    """
    if fw_action not in FW_ACTIONS:
        raise ValueError(
            f"fw_action desconocido: {fw_action!r}. "
            f"Válidos: {sorted(FW_ACTIONS)}"
        )
    needed = FW_ACTIONS[fw_action]
    params = dict(action_param or {})
    missing = needed - set(params)
    if missing:
        raise ValueError(
            f"fw_action={fw_action} requiere {sorted(needed)}, "
            f"faltan {sorted(missing)}"
        )
    entry: dict[str, Any] = {
        "Action": PREFIX + fw_action,
        "ActionID": str(uuid.uuid4()),
        "ActionParam": params,
        "LinkedTitle": True,
        "Name": name or fw_action,
        "State": state,
        "ViewParam": _make_view_param(icon_arcname, text, font),
    }
    return entry


def empty_slot() -> dict:
    """Slot 'vacío visual': system.open con Path vacío, sin icono."""
    return slot_entry("system.open", action_param={"Path": ""})


def build(slots: dict[int, dict]) -> dict[str, dict]:
    """Compone el manifest a partir de `{slot_id: <dict del slot>}`.

    Sólo se incluyen los slots presentes. La app oficial omite los
    slots sin uso (verificado en captura cluster03: 4 slots de 13).
    Rellenar con `empty_slot()` (Icon: "") rompe el render en
    page-changes — el firmware acepta un slot con icon vacío en el
    handshake inicial pero lo rechaza tras un press.
    """
    manifest: dict[str, dict] = {}
    for sid, entry in slots.items():
        manifest[slot_key(sid)] = entry
    return manifest
