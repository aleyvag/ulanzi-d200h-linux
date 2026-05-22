"""Conversor `.ulanziDeckProfile` → YAML del bridge.

El software oficial de Ulanzi exporta perfiles como archivos
`.ulanziDeckProfile`: un header ASCII de 12 bytes (`#Version: 2\\n`)
seguido de un ZIP. Cada ZIP contiene UN profile entero:

    <profile-uuid>.ulanziProfile/
    ├── manifest.json                  # metadata (Name, Pages.Pages list)
    ├── icon_<profile>.png
    └── Profiles/
        └── <page-uuid>/
            ├── manifest.json          # 13 slots de UNA página
            ├── Images/
            └── Files/

Los slots referencian otros profiles por `ProfileUUID` (page.switch /
page.folder). Para reconstruir la navegación cross-profile el conversor
opera en **dos pases**:

  1. Discovery: descomprimir todos los inputs, indexar UUIDs y construir
     el mapping global  `page_uuid → page_id` (con namespacing por slug
     del Name del profile).
  2. Translation: traducir cada slot resolviendo las referencias contra
     el mapping. Lo no traducible se materializa como `host_action: stub`
     con una línea YAML comentada arriba con el dato original.

Comando público: `d200h convert <fichero-o-folder>` (ver cli.py).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("d200h.convert")


HEADER_BYTES = b"#Version: 2\n"
PREFIX = "com.ulanzi.ulanzideck."


# ---------------------------------------------------------------------------
# Modelo en memoria
# ---------------------------------------------------------------------------

@dataclass
class ProfileInfo:
    uuid: str
    name: str
    slug: str
    page_uuids: list[str]          # orden tal como aparece en Pages.Pages
    current_page_uuid: str         # Pages.Current
    root_path: Path                # /tmp/.../<uuid>.ulanziProfile/
    source: Path                   # archivo .ulanziDeckProfile original
    page_ids: dict[str, str] = field(default_factory=dict)  # page_uuid → page_id


@dataclass
class ConvertReport:
    profiles: int = 0
    pages: int = 0
    slots: int = 0
    todos: int = 0
    icons_copied: int = 0
    icons_missing: int = 0
    unresolved_refs: int = 0     # page.switch a UUIDs fuera del set
    written: list[Path] = field(default_factory=list)
    # Primeras credenciales Spotify vistas en algún slot spotify.* del
    # profile original. Las usa `convert()` para pre-rellenar
    # `config/secrets/spotify.yaml` (sin pisar uno existente).
    spotify_creds: Optional[tuple[str, str]] = None
    spotify_secrets_written: Optional[Path] = None

    def summary(self) -> str:
        return (f"profiles={self.profiles} pages={self.pages} slots={self.slots} "
                f"todos={self.todos} icons={self.icons_copied}+{self.icons_missing}miss "
                f"unresolved_switch={self.unresolved_refs}")


# ---------------------------------------------------------------------------
# Slug / sanitización
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "profile"


def _safe_filename(name: str) -> str:
    """Sanea un nombre de archivo para `config/icons/`."""
    keep = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return re.sub(r"_+", "_", keep).strip("._-") or "icon"


# ---------------------------------------------------------------------------
# Tabla de mapeo de hotkeys Ulanzi → xdotool/ydotool
# ---------------------------------------------------------------------------

_MOD_MAP = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "win": "super", "super": "super", "meta": "super", "cmd": "super", "command": "super",
}

# Teclas no alfanuméricas que mantenemos como nombres X11.
_KEY_MAP = {
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "space": "space", "tab": "Tab", "enter": "Return", "return": "Return",
    "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "home": "Home", "end": "End",
    "pageup": "Page_Up", "page_up": "Page_Up", "pgup": "Page_Up",
    "pagedown": "Page_Down", "page_down": "Page_Down", "pgdn": "Page_Down",
    "insert": "Insert", "ins": "Insert",
    "capslock": "Caps_Lock",
    "printscreen": "Print", "prtsc": "Print",
}

# Símbolos cuyo basename literal no es válido como nombre xdotool/ydotool.
_SYM_MAP = {
    "]": "bracketright", "[": "bracketleft",
    "`": "grave",
    "/": "slash", "\\": "backslash",
    "'": "apostrophe", '"': "quotedbl",
    ";": "semicolon", ",": "comma", ".": "period",
    "=": "equal", "-": "minus",
    "+": "plus",
}

# Multimedia (`com.ulanzi.ulanzideck.system.multimedia.Hotkey`).
# Los valores del firmware vienen con typo "Volumn_*" (sic). Aceptamos
# ambas grafías para evitar caer a stub por una letra. El lookup
# (`_lookup_media`) es case-insensitive sobre las claves canónicas.
_MEDIA_MAP = {
    "volumn_up":      ("volume", "up"),     # typo del firmware (sic)
    "volumn_down":    ("volume", "down"),
    "volumn_mute":    ("volume", "mute"),    # typo del firmware (sic)
    "volume_up":      ("volume", "up"),
    "volume_down":    ("volume", "down"),
    "volume_mute":    ("volume", "mute"),
    "mute":           ("volume", "mute"),
    "playpause":      ("media", "play-pause"),
    "play_pause":     ("media", "play-pause"),
    "play":           ("media", "play"),
    "pause":          ("media", "pause"),
    "next":           ("media", "next"),
    "next_track":     ("media", "next"),
    "previous":       ("media", "previous"),
    "previous_track": ("media", "previous"),
    "stop":           ("media", "stop"),
    "stop_track":     ("media", "stop"),
}


def _lookup_media(raw: str) -> Optional[tuple[str, str]]:
    return _MEDIA_MAP.get((raw or "").strip().lower())


def _normalize_hotkey(s: str) -> tuple[str, bool]:
    """Traduce un hotkey de Ulanzi al formato que entiende xdotool/ydotool.

    Devuelve `(traducido, ok)`. `ok=False` si alguna parte quedó sin traducir
    (el caller marcará el slot como TODO).
    """
    if not s:
        return "", False
    parts = [p for p in re.split(r"[+\s]+", s) if p]
    out: list[str] = []
    ok = True
    for raw in parts:
        low = raw.lower()
        if low in _MOD_MAP:
            out.append(_MOD_MAP[low]); continue
        if low in _KEY_MAP:
            out.append(_KEY_MAP[low]); continue
        if len(raw) == 1:
            if raw in _SYM_MAP:
                out.append(_SYM_MAP[raw]); continue
            if raw.isalnum():
                out.append(raw.lower()); continue
        # F1..F24
        m = re.match(r"f(\d{1,2})$", low)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 24:
                out.append(f"F{n}"); continue
        # No reconocido — lo dejamos tal cual pero marcamos como TODO.
        out.append(raw); ok = False
    return "+".join(out), ok


# ---------------------------------------------------------------------------
# Catálogo de apps Windows → Linux (heurístico por basename)
# ---------------------------------------------------------------------------

_KNOWN_APPS: dict[str, dict[str, str]] = {
    "firefox.exe":      {"cmd": "firefox",        "match": "Firefox"},
    "chrome.exe":       {"cmd": "google-chrome",  "match": "Chrome"},
    "msedge.exe":       {"cmd": "microsoft-edge", "match": "Edge"},
    "brave.exe":        {"cmd": "brave",          "match": "Brave"},
    "code.exe":         {"cmd": "code",           "match": "Visual Studio Code"},
    "cursor.exe":       {"cmd": "cursor",         "match": "Cursor"},
    "spotify.exe":      {"cmd": "spotify",        "match": "Spotify"},
    "discord.exe":      {"cmd": "discord",        "match": "Discord"},
    "slack.exe":        {"cmd": "slack",          "match": "Slack"},
    "telegram.exe":     {"cmd": "telegram-desktop", "match": "Telegram"},
    "obs64.exe":        {"cmd": "obs",            "match": "OBS"},
    "obs32.exe":        {"cmd": "obs",            "match": "OBS"},
    "obs.exe":          {"cmd": "obs",            "match": "OBS"},
    "steam.exe":        {"cmd": "steam",          "match": "Steam"},
    "vlc.exe":          {"cmd": "vlc",            "match": "VLC"},
    "blender.exe":      {"cmd": "blender",        "match": "Blender"},
    "gimp.exe":         {"cmd": "gimp",           "match": "GIMP"},
    "inkscape.exe":     {"cmd": "inkscape",       "match": "Inkscape"},
    "explorer.exe":     {"cmd": "xdg-open ~",     "match": ""},
    "calc.exe":         {"cmd": "gnome-calculator", "match": "Calculator"},
    "notepad.exe":      {"cmd": "gedit",          "match": ""},
    "cmd.exe":          {"cmd": "x-terminal-emulator", "match": ""},
    "powershell.exe":   {"cmd": "x-terminal-emulator", "match": ""},
    "wt.exe":           {"cmd": "x-terminal-emulator", "match": ""},
}


def _lookup_app(path: str) -> Optional[dict[str, str]]:
    base = Path(path.replace("\\", "/")).name.lower()
    return _KNOWN_APPS.get(base)


# Pistas concretas para el usuario cuando un .exe/.bat no está en _KNOWN_APPS.
# NO son traducciones automáticas (el conversor sigue emitiendo stub) — sólo
# texto orientativo en el comentario YAML para acelerar la edición a mano.
_PATH_HINTS = (
    ("taskmgr.exe",        "type: shell, cmd: \"gnome-system-monitor\" (o ksysguard / htop en terminal)"),
    ("powershell_ise.exe", "type: shell, cmd: \"x-terminal-emulator -e pwsh\" (instala powershell antes)"),
    ("powershell.exe",     "type: shell, cmd: \"x-terminal-emulator -e pwsh\""),
    ("cmd.exe",            "type: shell, cmd: \"x-terminal-emulator\""),
    ("notepad.exe",        "type: app, cmd: \"gedit\""),
    ("explorer.exe",       "type: shell, cmd: \"xdg-open ~\""),
    ("control.exe",        "type: shell, cmd: \"gnome-control-center\""),
    ("snippingtool.exe",   "type: keys, keys: \"shift+super+s\" (o type: shell con flameshot/spectacle)"),
    ("magnify.exe",        "type: keys, keys: \"super+plus\""),
    ("osk.exe",            "type: shell, cmd: \"onboard\""),
)
_BAT_HINT = ("type: shell, cmd: \"<comando linux equivalente>\" — los .bat de "
             "Windows no se ejecutan en Linux")
_PS1_HINT = ("type: shell, cmd: \"pwsh -File /ruta/script.ps1\" (requiere "
             "powershell instalado) o reescribe el script en bash")
_EXE_HINT = ("type: app con cmd Linux equivalente — los .exe de Windows no se "
             "ejecutan en Linux salvo con wine")


def _suggest_linux_for_path(path: str) -> str:
    """Devuelve una pista textual de qué `host_action` Linux usar para un path
    Windows. Sólo se usa para enriquecer el comentario YAML; el conversor
    sigue emitiendo un stub para que el usuario tome la decisión final."""
    # Algunos paths llevan args pegados al exe: "C:\...\Taskmgr.exe /7" → el
    # basename útil es "taskmgr.exe". Quedarnos con la primera palabra.
    head = path.split()[0] if path.split() else path
    base = Path(head.replace("\\", "/")).name.lower()
    for needle, hint in _PATH_HINTS:
        if base == needle:
            return hint
    suffix = Path(base).suffix
    if suffix == ".bat" or suffix == ".cmd":
        return _BAT_HINT
    if suffix == ".ps1":
        return _PS1_HINT
    if suffix == ".exe":
        return _EXE_HINT
    return "edita el slot a mano (revisa docs/user/pages-guide.md)"


# ---------------------------------------------------------------------------
# Desempaquetado
# ---------------------------------------------------------------------------

def _unpack(deck_profile: Path, tmpdir: Path) -> Path:
    """Extrae un .ulanziDeckProfile. Devuelve la carpeta `<uuid>.ulanziProfile/`."""
    raw = deck_profile.read_bytes()
    if not raw.startswith(HEADER_BYTES):
        log.warning("%s: header inesperado (no '#Version: 2'); intento como ZIP igual",
                    deck_profile.name)
        body = raw
    else:
        body = raw[len(HEADER_BYTES):]
    dest = tmpdir / deck_profile.stem
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(__import__("io").BytesIO(body)) as zf:
        zf.extractall(dest)
    # Buscar el único subdirectorio *.ulanziProfile
    candidates = [p for p in dest.iterdir() if p.is_dir() and p.name.endswith(".ulanziProfile")]
    if not candidates:
        raise RuntimeError(f"{deck_profile.name}: no contiene <uuid>.ulanziProfile/")
    return candidates[0]


def _read_root_manifest(profile_root: Path, source: Path) -> ProfileInfo:
    mpath = profile_root / "manifest.json"
    data = json.loads(mpath.read_text(encoding="utf-8"))
    uuid = profile_root.name.replace(".ulanziProfile", "")
    name = str(data.get("Name") or uuid)
    pages_block = data.get("Pages") or {}
    page_uuids = list(pages_block.get("Pages") or [])
    current = str(pages_block.get("Current") or (page_uuids[0] if page_uuids else ""))
    return ProfileInfo(
        uuid=uuid,
        name=name,
        slug=_slugify(name),
        page_uuids=page_uuids,
        current_page_uuid=current,
        root_path=profile_root,
        source=source,
    )


# ---------------------------------------------------------------------------
# Pase 1: discovery
# ---------------------------------------------------------------------------

def _discover(inputs: list[Path], tmpdir: Path) -> dict[str, ProfileInfo]:
    """Devuelve mapping `profile_uuid → ProfileInfo`."""
    profiles: dict[str, ProfileInfo] = {}
    files: list[Path] = []
    for p in inputs:
        if p.is_dir():
            files.extend(sorted(p.glob("*.ulanziDeckProfile")))
        elif p.is_file():
            files.append(p)
        else:
            log.warning("Input no existe: %s", p)
    if not files:
        raise RuntimeError("Sin archivos .ulanziDeckProfile que convertir.")
    for f in files:
        try:
            root = _unpack(f, tmpdir)
            info = _read_root_manifest(root, source=f)
        except Exception as exc:
            log.error("Saltando %s: %s", f.name, exc)
            continue
        if info.uuid in profiles:
            log.warning("UUID duplicado %s (ignorando duplicado de %s)",
                        info.uuid[:8], f.name)
            continue
        profiles[info.uuid] = info
    return profiles


def _assign_page_ids(profiles: dict[str, ProfileInfo],
                     default_profile_name: Optional[str]) -> None:
    """Decide el page_id final de cada page_uuid y muta `profiles[*].page_ids`."""
    # 1. Slugs únicos entre profiles.
    used_slugs: set[str] = set()
    for info in sorted(profiles.values(), key=lambda p: p.name.lower()):
        base = info.slug
        slug = base
        n = 2
        while slug in used_slugs:
            slug = f"{base}_{n}"; n += 1
        info.slug = slug
        used_slugs.add(slug)

    # 2. Decidir el slug "home" (entrada del bridge).
    home_uuid: Optional[str] = None
    if default_profile_name:
        target = default_profile_name.lower()
        for info in profiles.values():
            if info.name.lower() == target:
                home_uuid = info.uuid; break
        if home_uuid is None:
            log.warning("--default-profile=%r no encontrado entre los convertidos",
                        default_profile_name)
    if home_uuid is None:
        for info in profiles.values():
            if info.name.strip().lower() == "default profile":
                home_uuid = info.uuid; break

    # 3. Para cada profile, asignar page_ids a sus page_uuids.
    for info in profiles.values():
        if not info.page_uuids:
            continue
        slug = "home" if info.uuid == home_uuid else info.slug
        # entry = current_page_uuid; va sin sufijo. Resto, sufijo _2, _3 ...
        # Preservamos el ORDEN de page_uuids para next/prev resolution.
        ordered = info.page_uuids
        entry = info.current_page_uuid if info.current_page_uuid in ordered else ordered[0]
        suffix_n = 2
        for puuid in ordered:
            if puuid == entry:
                info.page_ids[puuid] = slug
            else:
                info.page_ids[puuid] = f"{slug}_{suffix_n}"; suffix_n += 1


# ---------------------------------------------------------------------------
# Pase 2: traducción de slots
# ---------------------------------------------------------------------------

# Un slot resultado. Construimos manualmente texto YAML (con comentarios)
# en vez de yaml.dump porque PyYAML no preserva comentarios y los necesitamos
# para señalar TODOs.
@dataclass
class SlotOut:
    slot_key: str                  # "row_col"
    body_yaml: str                 # texto YAML del slot ya formateado (sin la clave)
    comments: list[str] = field(default_factory=list)
    has_todo: bool = False


def _yaml_inline(v: Any) -> str:
    """Render JSON-like compacto válido como YAML flow."""
    return json.dumps(v, ensure_ascii=False)


def _yaml_scalar(s: str) -> str:
    """String YAML siempre entre comillas dobles, escapando lo mínimo."""
    return json.dumps(s, ensure_ascii=False)


def _icon_dest_name(profile_slug: str, raw_basename: str) -> str:
    safe = _safe_filename(raw_basename)
    return f"{profile_slug}__{safe}"


# Iconos factory bundled en config/icons/_firmware/ — mapear a su nombre
# canónico cuando aparece el original.
_FACTORY_ICON_ALIASES = {
    "btn_nextpage.png":      "btn_nextPage",
    "btn_nextpage.svg":      "btn_nextPage",
    "btn_previouspage.png":  "btn_previousPage",
    "btn_previouspage.svg":  "btn_previousPage",
    "btn_gotopage.png":      "btn_goToPage",
    "btn_backtoparent.png":  "btn_backToParent",
    "btn_folder.png":        "btn_folder",
    "btn_pageindicator.png": "btn_pageIndicator",
    "btn_switchprofile.png": "btn_switchProfile",
    "btn_playaudio.png":     "btn_playAudio",
    "btn_stopaudio.png":     "btn_stopAudio",
}


def _resolve_icon(view_param: dict[str, Any], page_dir: Path,
                  profile: ProfileInfo, icons_out: Path,
                  report: ConvertReport) -> tuple[Optional[str], list[str]]:
    """Resuelve el icono del slot.

    Devuelve `(icon_name_for_yaml, extra_comments)`. `icon_name_for_yaml`
    puede ser None si no hay icono utilizable (el bridge inserta un PNG
    transparente en ese caso).
    """
    comments: list[str] = []
    if not view_param:
        return None, comments
    # Preferir Icon (lo que la app oficial pinta) sobre IconDef (el original
    # del autor — a veces es path Windows muerto).
    icon_field = view_param.get("Icon") or view_param.get("IconDef") or ""
    icon_field = str(icon_field).strip()
    if not icon_field:
        return None, comments

    # Caso A: ruta relativa dentro del propio Profile (e.g. "Images/foo.png").
    if not re.match(r"^[a-zA-Z]:[\\/]", icon_field) and not icon_field.startswith("C:/"):
        # path relativo al page_dir
        rel = icon_field.replace("\\", "/")
        # Algunas referencias vienen como "Images/<name>" (sin extensión clara).
        src = page_dir / rel
        if not src.is_file():
            # Probar buscando por basename en Images/ del propio profile.
            src = _find_by_basename(profile.root_path, Path(rel).name)
        if src and src.is_file():
            base = src.name.lower()
            if base in _FACTORY_ICON_ALIASES:
                # Es un icono factory; el bridge ya tiene la versión PNG bundleada.
                # Devolvemos sólo el nombre lógico, sin copiar.
                return _FACTORY_ICON_ALIASES[base], comments
            if src.suffix.lower() == ".svg":
                # El bridge espera PNG. Copiamos el SVG igualmente como
                # "evidencia" pero NO lo usamos como icono (resolve_icon
                # del bridge fallaría). Dejamos un comentario.
                dest = icons_out / _icon_dest_name(profile.slug, src.name)
                _safe_copy(src, dest)
                report.icons_copied += 1
                comments.append(f"icono original era SVG ({dest.name}); "
                                f"el bridge necesita PNG — convierte manualmente")
                return None, comments
            dest = icons_out / _icon_dest_name(profile.slug, src.name)
            _safe_copy(src, dest)
            report.icons_copied += 1
            return dest.stem, comments
        # No encontrado
        report.icons_missing += 1
        comments.append(f"icono no encontrado en el ZIP: {icon_field}")
        return None, comments

    # Caso B: ruta absoluta Windows. Intentar matchear por basename dentro
    # del Profile.
    base = Path(icon_field.replace("\\", "/")).name
    src = _find_by_basename(profile.root_path, base)
    if src and src.is_file() and src.suffix.lower() != ".svg":
        dest = icons_out / _icon_dest_name(profile.slug, src.name)
        _safe_copy(src, dest)
        report.icons_copied += 1
        return dest.stem, comments
    report.icons_missing += 1
    comments.append(f"icono original (no copiable): {icon_field}")
    return None, comments


def _find_by_basename(root: Path, basename: str) -> Optional[Path]:
    if not basename:
        return None
    for candidate in root.rglob(basename):
        if candidate.is_file():
            return candidate
    return None


def _safe_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return
    shutil.copyfile(src, dest)


# ---------------------------------------------------------------------------
# Traducción de Actions
# ---------------------------------------------------------------------------

def _translate_subaction(sub: dict[str, Any], comments: list[str]) -> dict[str, Any]:
    """Traduce un sub-action dentro de un multiactions.routine.

    Devuelve un dict listo para meter en `host_action.actions[]`. NO genera
    iconos/text (esos son del slot padre). Si el sub-action no se reconoce,
    devuelve un sub-action `stub` (lo gestionará _h_stub al pulsar el slot).
    """
    action = str(sub.get("Action", ""))
    ap = sub.get("ActionParam") or {}
    if action == PREFIX + "system.hotkey":
        hk, ok = _normalize_hotkey(str(ap.get("Hotkey", "")))
        if not ok:
            comments.append(f"sub-action: hotkey {ap.get('Hotkey')!r} sin traducción completa")
        return {"type": "keys", "keys": hk}
    if action == PREFIX + "system.multimedia":
        raw = str(ap.get("Hotkey", ""))
        mm = _lookup_media(raw)
        if mm:
            kind, cmd = mm
            return {"type": kind, "cmd": cmd}
        comments.append(f"sub-action: system.multimedia hotkey {raw!r} desconocido")
        return {"type": "stub", "command": "system.multimedia", "args": dict(ap),
                "hint": "Hotkey multimedia no reconocido — sustituye por type: volume/media."}
    if action == PREFIX + "multiactions.delay":
        # `delay` es un sub-handler de primera clase en el bridge: lo
        # emitimos como `{type: delay, ms: N}` directo dentro del multi.
        return {"type": "delay", "ms": int(ap.get("Value", 0))}
    if action == PREFIX + "system.text":
        # `system.text` = "tipear este texto en la ventana activa".
        # Equivalente directo del bridge: handler `text` (xdotool/ydotool).
        return {"type": "text", "text": str(ap.get("Text", ""))}
    if action == PREFIX + "system.open":
        path = str(ap.get("Path", ""))
        if path.startswith(("http://", "https://")):
            return {"type": "url", "url": path}
        app = _lookup_app(path)
        if app:
            return {"type": "app", "match": app.get("match", ""), "cmd": app["cmd"]}
        comments.append(f"sub-action: system.open path {path!r} no traducido")
        return {"type": "stub", "command": "app.open", "args": {"path": path},
                "hint": "Path Windows sin equivalente Linux automático — "
                        "sustituye por host_action: type: app (con cmd Linux), "
                        "type: shell, o type: keys según corresponda."}
    if action == PREFIX + "system.website":
        return {"type": "url", "url": str(ap.get("Url", ""))}
    comments.append(f"sub-action: {action} no traducido")
    return {"type": "stub", "command": action.replace(PREFIX, ""), "args": dict(ap),
            "hint": "Sub-action del software Ulanzi de Windows sin equivalente "
                    "directo en este bridge — edita este YAML a mano."}


def _translate_multi(routine_param: dict[str, Any],
                     comments: list[str]) -> dict[str, Any]:
    """Traduce un multiactions.routine a `{type: multi, actions: [...]}`.

    Los delays se emiten como sub-acciones de primera clase
    `{type: delay, ms: N}` intercaladas en `actions`, lo que hace los
    YAML más legibles cuando se serializan en bloque.
    """
    raw_subs = routine_param.get("Actions") or []
    out: list[dict[str, Any]] = []
    for sub in raw_subs:
        tr = _translate_subaction(sub, comments)
        if tr.get("type") == "delay" and tr.get("ms", 0) <= 0:
            continue
        out.append(tr)
    return {"type": "multi", "actions": out}


def _resolve_nav(direction: str, current_page_uuid: str,
                 profile: ProfileInfo) -> Optional[str]:
    """Resuelve page.next/page.prev a un page_id concreto (con wrap)."""
    pages = profile.page_uuids
    if current_page_uuid not in pages or len(pages) < 2:
        return None
    idx = pages.index(current_page_uuid)
    if direction == "next":
        target = pages[(idx + 1) % len(pages)]
    else:
        target = pages[(idx - 1) % len(pages)]
    return profile.page_ids.get(target)


def _resolve_page_ref(ap: dict[str, Any],
                      profile: ProfileInfo,
                      profiles: dict[str, ProfileInfo]) -> Optional[str]:
    """Resuelve un ActionParam con ProfileUUID y/o Page (int) → page_id."""
    target_uuid = ap.get("ProfileUUID")
    page_idx = ap.get("Page")
    if target_uuid and target_uuid in profiles:
        target_profile = profiles[target_uuid]
        if isinstance(page_idx, int) and 1 <= page_idx <= len(target_profile.page_uuids):
            return target_profile.page_ids.get(target_profile.page_uuids[page_idx - 1])
        return target_profile.page_ids.get(target_profile.current_page_uuid)
    if isinstance(page_idx, int) and 1 <= page_idx <= len(profile.page_uuids):
        return profile.page_ids.get(profile.page_uuids[page_idx - 1])
    return None


def _translate_slot(slot_key: str, slot: dict[str, Any],
                    page_uuid: str, profile: ProfileInfo,
                    profiles: dict[str, ProfileInfo],
                    page_dir: Path, icons_out: Path,
                    report: ConvertReport) -> Optional[SlotOut]:
    # Ojo: el .ulanziDeckProfile usa convención "col_row" (col primero).
    # Detectamos los slots del widget reloj en esa convención.
    if slot_key in {"3_2", "4_2"}:
        # Reservados al firmware (reloj) — los saltamos.
        return None

    # Traducimos la clave a la convención row_col (row primero) que usa
    # el bridge — alineada con `[fila][columna]` (CSS Grid, NumPy, etc.).
    try:
        col_s, row_s = slot_key.split("_")
        out_key = f"{int(row_s)}_{int(col_s)}"
    except (ValueError, AttributeError):
        out_key = slot_key

    action = str(slot.get("Action", ""))
    ap = slot.get("ActionParam") or {}
    view = (slot.get("ViewParam") or [{}])[0] or {}
    text = str(view.get("Text") or "").strip()

    comments: list[str] = []
    icon_name, icon_comments = _resolve_icon(view, page_dir, profile, icons_out, report)
    comments.extend(icon_comments)

    fields: dict[str, Any] = {}     # → emitidas como YAML key: value
    host_action: Optional[dict[str, Any]] = None
    fw_action: Optional[str] = None
    fw_param: Optional[dict[str, Any]] = None
    has_todo = False

    if action == PREFIX + "system.hotkey":
        hk, ok = _normalize_hotkey(str(ap.get("Hotkey", "")))
        host_action = {"type": "keys", "keys": hk}
        if not ok:
            comments.append(f"hotkey {ap.get('Hotkey')!r} sin traducción completa")
            has_todo = True

    elif action == PREFIX + "system.multimedia":
        raw = str(ap.get("Hotkey", ""))
        mm = _lookup_media(raw)
        if mm:
            kind, cmd = mm
            host_action = {"type": kind, "cmd": cmd}
        else:
            host_action = {"type": "stub", "command": "system.multimedia",
                           "args": dict(ap),
                           "hint": "Hotkey multimedia no reconocido — sustituye "
                                   "por host_action: type: volume (cmd: up/down/mute) "
                                   "o type: media (cmd: play-pause/next/previous/stop)."}
            comments.append(f"system.multimedia hotkey {raw!r} desconocido "
                            f"— sugerencia: type: volume o type: media")
            has_todo = True

    elif action == PREFIX + "system.text":
        # Equivalente directo del handler `text` (xdotool/ydotool type).
        host_action = {"type": "text", "text": str(ap.get("Text", ""))}

    elif action == PREFIX + "system.switchhotkey":
        # Acción Windows-only: toggle entre dos hotkeys distintos por
        # pulsación. No tenemos forma genérica de mantener estado por slot
        # en el bridge actual (cada press dispara la misma acción). Lo
        # dejamos como stub explícito en lugar de inventar comandos.
        hotkeys = ap.get("Hotkey") if isinstance(ap.get("Hotkey"), list) else [ap.get("Hotkey")]
        host_action = {"type": "stub", "command": "system.switchhotkey",
                       "args": dict(ap),
                       "hint": "Toggle de dos hotkeys (acción Windows). "
                               "En Linux: usa type: multi con varios type: keys "
                               "(envía ambas en secuencia), o crea dos slots "
                               "separados con type: keys cada uno."}
        comments.append(f"system.switchhotkey {hotkeys!r} — "
                        f"sin equivalente directo Linux; ver hint del stub")
        has_todo = True

    elif action == PREFIX + "multiactions.routine":
        host_action = _translate_multi(ap, comments)

    elif action == PREFIX + "page.next":
        target = _resolve_nav("next", page_uuid, profile)
        if target:
            fw_action = "page.goto"; fw_param = {"Page": target}
        else:
            comments.append("page.next: profile con sólo 1 página, no hay destino")
            host_action = {"type": "notify", "summary": "Sólo hay una página"}
            has_todo = True

    elif action == PREFIX + "page.prev":
        target = _resolve_nav("prev", page_uuid, profile)
        if target:
            fw_action = "page.goto"; fw_param = {"Page": target}
        else:
            comments.append("page.prev: profile con sólo 1 página, no hay destino")
            host_action = {"type": "notify", "summary": "Sólo hay una página"}
            has_todo = True

    elif action in (PREFIX + "page.goto", PREFIX + "page.folder",
                    PREFIX + "page.switch"):
        target_id = _resolve_page_ref(ap, profile, profiles)
        if target_id:
            # Preservamos el matiz semántico cuando el profile original
            # usaba page.folder (entrar a sub-profile). page.switch en
            # cambio sí se degrada a page.goto: el bridge no las
            # diferencia y page.switch está deprecado en el formato.
            if action == PREFIX + "page.folder":
                fw_action = "page.folder"; fw_param = {"ProfileUUID": target_id}
            else:
                fw_action = "page.goto"; fw_param = {"Page": target_id}
        else:
            ref_name = ap.get("Profile") or ap.get("ProfileUUID") or ap.get("Page") or "?"
            comments.append(
                f"{action.replace(PREFIX,'')} a {ref_name!r} no resuelto "
                f"(perfil destino no convertido en esta corrida)"
            )
            host_action = {"type": "stub", "command": action.replace(PREFIX, ""),
                           "args": dict(ap)}
            has_todo = True
            report.unresolved_refs += 1

    elif action == PREFIX + "page.back":
        fw_action = "page.back"

    elif action == PREFIX + "page.indicator":
        # En la app oficial es un slot decorativo (sólo muestra "Page N").
        # Política de este bridge: TODO slot del profile debe dar feedback al
        # pulsarse — emitimos stub para que el usuario sepa que la acción se
        # reconoció pero no tiene equivalente ejecutable en Linux.
        host_action = {"type": "stub", "command": "page.indicator",
                       "args": dict(ap),
                       "hint": "Slot decorativo del software Ulanzi de Windows "
                               "(\"page indicator\"). Bórralo si no lo necesitas "
                               "o sustituye host_action por una acción real."}
        comments.append("page.indicator: slot decorativo del software oficial "
                        "— pulsar abrirá popup Tk informativo")
        has_todo = True

    elif action == PREFIX + "system.website":
        host_action = {"type": "url", "url": str(ap.get("Url", ""))}

    elif action == PREFIX + "system.open":
        path = str(ap.get("Path", ""))
        if not path:
            # En la app oficial es un slot pasivo (Path vacío). Política
            # del bridge: dar feedback al pulsarse, no silencio.
            host_action = {"type": "stub", "command": "system.open",
                           "args": {"path": ""},
                           "hint": "Slot pasivo del software Ulanzi de Windows "
                                   "(system.open con Path vacío). Configura un "
                                   "host_action propio o borra el slot."}
            comments.append("system.open Path vacío: slot pasivo del software "
                            "oficial — sin equivalente automático")
            has_todo = True
        elif path.startswith(("http://", "https://")):
            host_action = {"type": "url", "url": path}
        else:
            app = _lookup_app(path)
            if app:
                host_action = {"type": "app",
                               "match": app.get("match", ""),
                               "cmd": app["cmd"]}
                comments.append(f"path Win original: {path}")
            else:
                host_action = {"type": "stub", "command": "app.open",
                               "args": {"path": path},
                               "hint": "Path Windows sin equivalente Linux "
                                       "automático — sustituye por host_action: "
                                       "type: app (cmd Linux), type: shell, o "
                                       "type: keys según corresponda."}
                suggestion = _suggest_linux_for_path(path)
                comments.append(f"system.open path {path!r} sin app conocida "
                                f"— sugerencia: {suggestion}")
                has_todo = True

    elif action in (PREFIX + "sound.play", PREFIX + "sound.stop"):
        host_action = {"type": "stub", "command": action.replace(PREFIX, ""),
                       "args": dict(ap),
                       "hint": "El bridge aún no implementa audio dentro del ZIP. "
                               "Para reproducir un WAV, usa host_action: type: shell "
                               "con `aplay /ruta/al/audio.wav` o paplay."}
        comments.append(f"{action.replace(PREFIX,'')} no implementado en el bridge "
                        f"— sugerencia: type: shell con aplay/paplay")
        has_todo = True

    elif action == PREFIX + "smallwindow.window":
        host_action = {"type": "stub", "command": "smallwindow.window",
                       "args": dict(ap),
                       "hint": "Widget interno del firmware del D200H "
                               "(reloj/clima/CPU). No es controlable desde el "
                               "host con el protocolo conocido — el bridge no "
                               "puede replicar la acción. Sustituye o borra."}
        comments.append("smallwindow.window: widget del firmware no controlable "
                        "desde el host — pulsar abrirá popup Tk informativo")
        has_todo = True

    elif action.startswith(PREFIX + "spotify."):
        # Spotify Web API nativa. El bridge implementa el handler `type: spotify`
        # que toma credenciales de `config/secrets/spotify.yaml` (escrito por
        # `d200h convert` al ver creds, completado por `d200h spotify-auth`).
        # Mapeo Action Windows → cmd del handler:
        sub = action[len(PREFIX) + len("spotify."):]
        cmd_map = {
            "play":       "play-pause",  # el botón "Play" del software es toggle
            "pause":      "pause",
            "next":       "next",
            "previous":   "previous",
            "volumeup":   "volume-up",
            "volumedown": "volume-down",
            "volumeset":  "volume-set",
            "shuffle":    "shuffle",
            "tracklike":  "like",
        }
        cmd = cmd_map.get(sub)
        if cmd:
            host_action = {"type": "spotify", "cmd": cmd}
            device_id = ap.get("deviceId")
            if isinstance(device_id, str) and device_id:
                host_action["device_id"] = device_id
            if cmd == "volume-set":
                try:
                    host_action["value"] = int(ap.get("volumeValue", 50))
                except (TypeError, ValueError):
                    host_action["value"] = 50
            # Capturamos credenciales para escribirlas más adelante a
            # config/secrets/spotify.yaml. La extracción real la hace
            # `convert()` al final (un único write); aquí sólo dejamos
            # el rastro vía report.
            cid = str(ap.get("ClientId") or "").strip()
            cs = str(ap.get("ClientSecret") or "").strip()
            if cid and cs and not getattr(report, "spotify_creds", None):
                report.spotify_creds = (cid, cs)
            comments.append(f"spotify.{sub} → type: spotify cmd={cmd}; "
                            f"corre `d200h spotify-auth` si aún no tienes "
                            f"refresh_token")
        else:
            host_action = {"type": "stub", "command": f"spotify.{sub}",
                           "args": dict(ap),
                           "hint": "Subacción Spotify no mapeada todavía. "
                                   "Reemplaza por host_action: type: spotify "
                                   "con un cmd válido (play, pause, play-pause, "
                                   "next, previous, volume-up, volume-down, "
                                   "volume-set, shuffle, like)."}
            comments.append(f"spotify.{sub}: sin mapping a cmd Spotify — "
                            f"edita a mano")
            has_todo = True

    else:
        # Fallback total.
        host_action = {"type": "stub", "command": action.replace(PREFIX, "") or action,
                       "args": dict(ap),
                       "hint": "Acción del software Ulanzi de Windows sin "
                               "equivalente directo en este bridge Linux. "
                               "Edita este YAML a mano: revisa los host_action "
                               "soportados en docs/user/pages-guide.md."}
        if action:
            comments.append(f"Action {action!r} sin traducción Linux conocida "
                            f"— ver docs/user/pages-guide.md")
        has_todo = True

    # Construir YAML del cuerpo del slot.
    body_lines: list[str] = []
    if fw_action:
        body_lines.append(f"    fw_action: {fw_action}")
    if fw_param:
        body_lines.append(f"    fw_param: {_yaml_inline(fw_param)}")
    if icon_name:
        body_lines.append(f"    icon: {icon_name}")
    if text:
        body_lines.append(f"    text: {_yaml_scalar(text)}")
    if host_action:
        body_lines.append("    host_action:")
        for k, v in host_action.items():
            if k == "actions" and isinstance(v, list):
                # Block-style: una sub-acción por línea (flow inline) — más
                # legible que un único `actions: [{...}, {...}]` enorme.
                body_lines.append(f"      {k}:")
                for sub in v:
                    body_lines.append(f"        - {_yaml_inline(sub)}")
            elif isinstance(v, (dict, list)):
                body_lines.append(f"      {k}: {_yaml_inline(v)}")
            else:
                body_lines.append(f"      {k}: {_yaml_scalar(v) if isinstance(v, str) else v}")

    if not body_lines:
        # Slot completamente vacío después de traducir — no escribir.
        return None

    report.slots += 1
    if has_todo:
        report.todos += 1
    return SlotOut(slot_key=out_key, body_yaml="\n".join(body_lines),
                   comments=comments, has_todo=has_todo)


# ---------------------------------------------------------------------------
# Escritura del YAML de una página
# ---------------------------------------------------------------------------

def _write_page_yaml(out_path: Path, *, title: str,
                     source_info: str, slot_outs: list[SlotOut]) -> None:
    todo_count = sum(1 for s in slot_outs if s.has_todo)
    lines: list[str] = []
    lines.append(f"# Generado por `d200h convert` desde {source_info}.")
    if todo_count:
        lines.append(f"# {todo_count} slots con TODO — revisa los comentarios y ajusta.")
    lines.append(f"title: {_yaml_scalar(title)}")
    lines.append("slots:")
    for s in slot_outs:
        for c in s.comments:
            lines.append(f"  # {c}")
        lines.append(f"  \"{s.slot_key}\":")
        lines.append(s.body_yaml)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sort_slots(actions: dict[str, Any]) -> list[tuple[str, dict]]:
    """Ordena los slots del manifest Ulanzi (col_row) por (row, col) para
    que el YAML salga en orden visual: fila 0 (cols 0→4), fila 1, fila 2."""
    def key(item):
        k = item[0]
        try:
            c, r = k.split("_")
            return (int(r), int(c))
        except Exception:
            return (99, 99)
    return sorted(actions.items(), key=key)


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def convert(inputs: list[Path], out_pages: Path, out_icons: Path,
            *, default_profile: Optional[str] = None,
            dry_run: bool = False,
            keep_existing: bool = False) -> ConvertReport:
    report = ConvertReport()
    with tempfile.TemporaryDirectory(prefix="d200h-convert-") as td:
        tmp = Path(td)
        profiles = _discover(inputs, tmp)
        report.profiles = len(profiles)

        _assign_page_ids(profiles, default_profile)

        # Resumen previo (visible aún en dry-run).
        log.info("Profiles descubiertos:")
        for info in profiles.values():
            entry_id = info.page_ids.get(info.current_page_uuid, "?")
            extras = [info.page_ids[u] for u in info.page_uuids
                      if u != info.current_page_uuid]
            log.info("  %-30s slug=%-25s entry=%-15s extras=%s",
                     info.name, info.slug, entry_id, extras or "(ninguna)")

        out_pages.mkdir(parents=True, exist_ok=True)
        out_icons.mkdir(parents=True, exist_ok=True)

        for info in profiles.values():
            for page_idx, page_uuid in enumerate(info.page_uuids, start=1):
                page_dir = info.root_path / "Profiles" / page_uuid
                mfile = page_dir / "manifest.json"
                if not mfile.is_file():
                    log.warning("Saltando page %s/%s: sin manifest", info.name, page_uuid[:8])
                    continue
                pdata = json.loads(mfile.read_text(encoding="utf-8"))
                slot_outs: list[SlotOut] = []
                for ctrl in pdata.get("Controllers", []):
                    if ctrl.get("Type") != "Keypad":
                        continue
                    for slot_key, slot in _sort_slots(ctrl.get("Actions") or {}):
                        out = _translate_slot(slot_key, slot, page_uuid, info,
                                              profiles, page_dir, out_icons, report)
                        if out:
                            slot_outs.append(out)

                page_id = info.page_ids[page_uuid]
                out_file = out_pages / f"page_{page_id}.yaml"
                source_info = (f"{info.source.name} (profile {info.name!r}, "
                               f"página {page_idx}/{len(info.page_uuids)}, "
                               f"uuid={page_uuid[:8]})")
                report.pages += 1
                if dry_run:
                    log.info("[dry-run] escribiría %s (%d slots, %d TODOs)",
                             out_file, len(slot_outs),
                             sum(1 for s in slot_outs if s.has_todo))
                    continue
                if out_file.exists() and keep_existing:
                    log.info("Existe y --keep-existing: %s (no sobrescribo)", out_file)
                    continue
                title = f"{info.name}" if len(info.page_uuids) == 1 else f"{info.name} {page_idx}"
                _write_page_yaml(out_file, title=title,
                                 source_info=source_info, slot_outs=slot_outs)
                report.written.append(out_file)
                log.info("Escrito: %s", out_file)

    # Auto-extracción de credenciales Spotify: si vimos un slot spotify.*
    # con ClientId/Secret en el profile original y NO hay todavía un
    # archivo `config/secrets/spotify.yaml`, lo pre-rellenamos con
    # `refresh_token: ""` para que el usuario sólo tenga que correr
    # `d200h spotify-auth` para completar el OAuth.
    if not dry_run and report.spotify_creds:
        try:
            from . import config as _cfg
            spath = _cfg.spotify_credentials_path()
            if not spath.is_file():
                cid, cs = report.spotify_creds
                spath.parent.mkdir(parents=True, exist_ok=True)
                spath.write_text(
                    "# Generado por `d200h convert` desde un .ulanziDeckProfile\n"
                    "# con slots spotify.*. Completa el `refresh_token` corriendo:\n"
                    "#   uv run d200h spotify-auth\n"
                    f"client_id: \"{cid}\"\n"
                    f"client_secret: \"{cs}\"\n"
                    "refresh_token: \"\"\n",
                    encoding="utf-8",
                )
                try:
                    import os as _os
                    _os.chmod(spath, 0o600)
                except OSError:
                    pass
                report.spotify_secrets_written = spath
                log.info("Credenciales Spotify pre-rellenadas en %s — "
                         "corre `d200h spotify-auth` para autorizar.", spath)
        except Exception as exc:
            log.warning("No pude escribir spotify secrets template: %s", exc)

    return report
