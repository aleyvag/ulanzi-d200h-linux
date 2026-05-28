"""Bridge: cliente HID + dispatcher de host_actions.

Flujo:
  1. Cargar todas las páginas YAML, compilar a ZIPs (cache en memoria).
  2. Abrir `/dev/hidraw*` del D200H (interface 0).
  3. Handshake: enviar clock OUT.
  4. Enviar el ZIP de la página HOME (por defecto la página `home` o,
     si no existe, la primera alfabéticamente).
  5. Loop:
     - Cada ~2s reenviar clock OUT (keepalive).
     - Leer reports IN. Si llega `0x0101 press` con slot_id<13:
       - Resolver fw_action y host_action de la página actual.
       - Si fw_action ∈ page.*: cambiar de página (enviar nuevo ZIP).
       - Si hay host_action: ejecutarla.
     - Si llega `0x0101` slot_id=13: ignorar (slot reloj) o llamar a un
       handler especial si está definido en la config global (futuro).
  6. Al recibir SIGINT/SIGTERM: cerrar fd y salir.

El bridge **no** desconecta el firmware ni restaura nada. La pantalla
queda donde se dejó hasta el próximo handshake o reboot del device.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import config, focus, hid, pages, zip_pack
from .pages import Page, Slot, PageError

log = logging.getLogger("d200h.bridge")


# ---------------------------------------------------------------------------
# Detección del entorno gráfico (igual que antes; sirve a host_actions)
# ---------------------------------------------------------------------------

def _detect_display_server() -> str:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _find_key_tool() -> Optional[str]:
    """xdotool en X11, ydotool en Wayland; fallback al otro si está."""
    display = _detect_display_server()
    if display == "wayland":
        return ("ydotool" if shutil.which("ydotool") else
                ("xdotool" if shutil.which("xdotool") else None))
    return ("xdotool" if shutil.which("xdotool") else
            ("ydotool" if shutil.which("ydotool") else None))


def check_tools() -> None:
    log.info("Entorno gráfico: %s (WAYLAND_DISPLAY=%r DISPLAY=%r)",
             _detect_display_server(),
             os.environ.get("WAYLAND_DISPLAY"), os.environ.get("DISPLAY"))
    tool = _find_key_tool()
    log.info("  teclas/texto: %s", tool or "(falta xdotool/ydotool)")
    log.info("  media:        %s", "playerctl" if shutil.which("playerctl")
             else "(no playerctl; usaré keysyms XF86Audio* vía %s)" % (tool or "?"))
    log.info("  volumen:      %s", "wpctl" if shutil.which("wpctl")
             else "pactl" if shutil.which("pactl") else "(falta wpctl/pactl)")
    log.info("  brillo host:  %s",
             "brightnessctl" if shutil.which("brightnessctl")
             else "light" if shutil.which("light") else "(falta)")
    log.info("  apps focus:   %s",
             "wmctrl" if shutil.which("wmctrl") else "(falta wmctrl)")


# ---------------------------------------------------------------------------
# Host action handlers
# ---------------------------------------------------------------------------

_MEDIA_KEYSYMS = {
    "play-pause": "XF86AudioPlay", "play": "XF86AudioPlay",
    "pause": "XF86AudioPause",
    "next": "XF86AudioNext", "previous": "XF86AudioPrev",
    "stop": "XF86AudioStop",
}


def _h_shell(cfg, ctx):
    cmd = cfg.get("cmd", "")
    if not cmd:
        log.warning("shell sin cmd"); return
    log.info("shell → %s", cmd)
    subprocess.Popen(cmd, shell=True, stdin=subprocess.DEVNULL)


def _h_keys(cfg, ctx):
    keys = cfg.get("keys", "")
    tool = _find_key_tool()
    if not keys or not tool:
        log.error("keys: keys=%r tool=%r", keys, tool); return
    log.info("keys[%s] → %s", tool, keys)
    subprocess.Popen([tool, "key", keys], stdin=subprocess.DEVNULL)


def _h_text(cfg, ctx):
    text = cfg.get("text", "")
    delay_ms = int(cfg.get("delay_ms", 0))
    tool = _find_key_tool()
    if not text or not tool:
        log.error("text: text=%r tool=%r", text, tool); return
    log.info("text[%s, delay=%dms] → %r", tool, delay_ms, text[:40])
    if tool == "xdotool":
        args = ["xdotool", "type", "--clearmodifiers"]
        if delay_ms:
            args += ["--delay", str(delay_ms)]
        args.append(text)
    else:
        args = ["ydotool", "type"]
        if delay_ms:
            args += ["--key-delay", str(delay_ms)]
        args.append(text)
    subprocess.Popen(args, stdin=subprocess.DEVNULL)


def _h_media(cfg, ctx):
    cmd = cfg.get("cmd", "play-pause")
    keysym = _MEDIA_KEYSYMS.get(cmd)
    tool = _find_key_tool()
    if keysym and tool:
        log.info("media[%s] → %s", tool, keysym)
        subprocess.Popen([tool, "key", keysym], stdin=subprocess.DEVNULL); return
    if shutil.which("playerctl"):
        log.info("media[playerctl] → %s", cmd)
        subprocess.Popen(["playerctl", cmd], stdin=subprocess.DEVNULL); return
    log.error("media: ni xdotool/ydotool ni playerctl disponibles")


def _h_volume(cfg, ctx):
    cmd = cfg.get("cmd", "")
    step = int(cfg.get("step", 5))
    if shutil.which("wpctl"):
        sink = "@DEFAULT_AUDIO_SINK@"
        sh = {"up":   f"wpctl set-volume {sink} {step}%+",
              "down": f"wpctl set-volume {sink} {step}%-",
              "mute": f"wpctl set-mute {sink} toggle"}.get(cmd)
    elif shutil.which("pactl"):
        sink = "@DEFAULT_SINK@"
        sh = {"up":   f"pactl set-sink-volume {sink} +{step}%",
              "down": f"pactl set-sink-volume {sink} -{step}%",
              "mute": f"pactl set-sink-mute {sink} toggle"}.get(cmd)
    else:
        log.error("volume: wpctl/pactl no disponible"); return
    if not sh:
        log.error("volume cmd %r inválido", cmd); return
    log.info("volume → %s", cmd)
    subprocess.Popen(sh, shell=True, stdin=subprocess.DEVNULL)


def _h_brightness_host(cfg, ctx):
    cmd = cfg.get("cmd", "up")
    step = int(cfg.get("step", 10))
    if shutil.which("brightnessctl"):
        sign = "+" if cmd == "up" else "-"
        sh = (f"brightnessctl set +{step}%" if sign == "+"
              else f"brightnessctl set {step}%-")
    elif shutil.which("light"):
        flag = "-A" if cmd == "up" else "-U"
        sh = f"light {flag} {step}"
    else:
        log.error("brightness_host: brightnessctl/light no disponible"); return
    log.info("brightness_host → %s %d%%", cmd, step)
    subprocess.Popen(sh, shell=True, stdin=subprocess.DEVNULL)


# Brillo del LCD del D200H vía sysfs por ADB. La alternativa "nativa"
# (BrightnessMessage por HID) sigue sin decodificarse — ver §12 del doc v3.
DEVICE_BACKLIGHT = "/sys/class/backlight/backlight/brightness"
DEVICE_BACKLIGHT_MAX = "/sys/class/backlight/backlight/max_brightness"


# Floor mínimo en % del máximo: nunca permitimos bajar de aquí en `down`,
# para evitar quedar con la pantalla negra (estado indistinguible de
# "device muerto"). El usuario puede sobrescribirlo por slot con
# `floor_pct` en el host_action. `cmd: zero` salta el floor a propósito
# (ahorro de energía).
_DEVICE_BACKLIGHT_FLOOR_PCT_DEFAULT = 10

# Persistencia del último brillo conocido. Se guarda el valor RAW (no %)
# para no perder precisión y evitar depender del `max_brightness` actual
# al restaurar. Restauración: en _session() tras el primer ZIP.
_BRIGHTNESS_STATE_FILE = pathlib.Path(
    os.environ.get("D200H_CACHE_DIR")
    or os.path.expanduser("~/.cache/d200h")
) / "brightness"

_BRIGHTNESS_OUT_RE = re.compile(r"brightness=(\d+)/(\d+)")


def _save_brightness_raw(raw: int) -> None:
    try:
        _BRIGHTNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BRIGHTNESS_STATE_FILE.write_text(f"{raw}\n")
    except OSError as exc:
        log.debug("no pude persistir brillo (%s): %s",
                  _BRIGHTNESS_STATE_FILE, exc)


def _load_brightness_raw() -> Optional[int]:
    try:
        txt = _BRIGHTNESS_STATE_FILE.read_text().strip()
        return int(txt)
    except (OSError, ValueError):
        return None


_STANDBY_WAKE_FALLBACK_RAW = 180  # ~70% si el cache estuviera vacío al despertar


def _h_brightness_device(cfg, ctx):
    from . import adb
    cmd = cfg.get("cmd", "up")
    step = int(cfg.get("step", 10))
    floor_pct = int(cfg.get("floor_pct", _DEVICE_BACKLIGHT_FLOOR_PCT_DEFAULT))
    if cmd == "zero":
        # Salta el floor a propósito: LCD apagado para ahorrar energía.
        # El firmware sigue procesando HID; el siguiente press despierta el
        # bridge a "standby mode": restaura el brillo previo y descarta la
        # acción de esa tecla, para que el usuario nunca dispare comandos a
        # ciegas. NO persistimos `0` en cache: el archivo conserva el último
        # valor visible — así un crash en standby deja al siguiente handshake
        # con un brillo útil.
        remote = (
            f"max=$(cat {DEVICE_BACKLIGHT_MAX}); "
            f"echo 0 > {DEVICE_BACKLIGHT} && echo brightness=0/$max"
        )
    else:
        sign = "+" if cmd == "up" else "-"
        # `floor` se calcula remoto a partir de max para no asumir el valor max
        # (varía entre revisiones de firmware). En `down` clamping a max(floor, new).
        remote = (
            f"max=$(cat {DEVICE_BACKLIGHT_MAX}); cur=$(cat {DEVICE_BACKLIGHT}); "
            f"delta=$(( max * {step} / 100 )); "
            f"floor=$(( max * {floor_pct} / 100 )); "
            f"new=$(( cur {sign} delta )); "
            f"[ $new -lt $floor ] && new=$floor; "
            f"[ $new -gt $max ] && new=$max; "
            f"echo $new > {DEVICE_BACKLIGHT} && echo brightness=$new/$max"
        )
    try:
        out = adb.shell(remote, check=False).strip()
        m = _BRIGHTNESS_OUT_RE.search(out or "")
        if cmd == "zero":
            if ctx is not None:
                ctx.standby_mode = True
            log.info("brightness_device → ZERO (standby mode ON, %s)",
                     out or "?")
        else:
            if m:
                _save_brightness_raw(int(m.group(1)))
            log.info("brightness_device → %s step=%d%% floor=%d%% (%s)",
                     cmd, step, floor_pct, out or "?")
    except adb.AdbError as exc:
        log.error("brightness_device: %s", exc)


def _restore_brightness_device(use_fallback: bool = False) -> None:
    """Restaura el último brillo conocido vía ADB. Best-effort.

    - Si no hay cache: no hace nada (a menos que `use_fallback`, en cuyo caso
      escribe `_STANDBY_WAKE_FALLBACK_RAW`).
    - Si el cache es 0 (raro: el handler `zero` no debería persistir 0, pero
      por seguridad): también cae al fallback si `use_fallback`.
    """
    raw = _load_brightness_raw()
    if (raw is None or raw == 0) and use_fallback:
        raw = _STANDBY_WAKE_FALLBACK_RAW
    if raw is None or raw == 0:
        return
    from . import adb
    try:
        out = adb.shell(
            f"max=$(cat {DEVICE_BACKLIGHT_MAX}); "
            f"v={raw}; [ $v -gt $max ] && v=$max; "
            f"echo $v > {DEVICE_BACKLIGHT} && echo brightness=$v/$max",
            check=False,
        ).strip()
        log.info("brightness_device restaurado: %s", out or f"raw={raw}")
    except adb.AdbError as exc:
        log.debug("restore brightness: ADB no disponible (%s)", exc)


def _h_app(cfg, ctx):
    cmd = cfg.get("cmd", "")
    match = cfg.get("match") or (cmd.split()[0] if cmd else "")
    if not cmd or not match:
        log.warning("app sin cmd/match"); return
    if shutil.which("wmctrl"):
        sh = f"wmctrl -a {shlex.quote(match)} 2>/dev/null || ({cmd}) &"
    else:
        sh = f"({cmd}) &"
    log.info("app → focus %r or launch %r", match, cmd)
    subprocess.Popen(sh, shell=True, stdin=subprocess.DEVNULL,
                     start_new_session=True)


def _h_close(cfg, ctx):
    name = cfg.get("name", "")
    if not name:
        log.warning("close sin name"); return
    log.info("close → pkill -f %s", name)
    subprocess.Popen(["pkill", "-f", name], stdin=subprocess.DEVNULL)


def _focus_match_from_url(url: str) -> str:
    """Heurística: deriva un substring de match razonable desde la URL.

    Ej:  https://outlook.cloud.microsoft/mail/inbox → "outlook"
         https://chat.qwen.ai/  → "qwen"
         https://web.whatsapp.com/ → "whatsapp"
    Si la URL es rara, devuelve "" y el caller cae a fallback `xdg-open`.
    """
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc
    except Exception:
        return ""
    if not host:
        return ""
    parts = [p for p in host.split(".") if p not in ("www", "com", "org",
                                                       "net", "io", "ai", "mx",
                                                       "co", "us", "app", "dev")]
    return parts[-1] if parts else host.split(".")[0]


def _h_url(cfg, ctx):
    url = cfg.get("url", "")
    if not url:
        log.warning("url sin url"); return
    focus_flag = bool(cfg.get("focus", False))
    if focus_flag:
        match = cfg.get("match") or _focus_match_from_url(url)
        display = _detect_display_server()
        if display == "wayland":
            log.info("url focus: Wayland sin soporte wmctrl, abro nueva")
        elif not shutil.which("wmctrl"):
            log.info("url focus: wmctrl no instalado, abro nueva")
        elif not match:
            log.info("url focus: no pude derivar match desde %s, abro nueva", url)
        else:
            log.info("url focus[%s] → wmctrl -a", match)
            r = subprocess.run(["wmctrl", "-a", match],
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return
            log.info("url focus: no encontré ventana %r, abro nueva", match)
    log.info("url → %s", url)
    subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL)


_SYSTEM_CMDS = {
    "lock":     "loginctl lock-session 2>/dev/null || xdg-screensaver lock",
    "suspend":  "systemctl suspend",
    "shutdown": "systemctl poweroff",
    "reboot":   "systemctl reboot",
    "logout":   "loginctl terminate-user $USER 2>/dev/null || gnome-session-quit --logout --no-prompt",
}


def _h_system(cfg, ctx):
    cmd = cfg.get("cmd", "")
    sh = _SYSTEM_CMDS.get(cmd)
    if not sh:
        log.error("system cmd %r inválido. Válidos: %s",
                  cmd, sorted(_SYSTEM_CMDS)); return
    log.info("system → %s", cmd)
    subprocess.Popen(sh, shell=True, stdin=subprocess.DEVNULL)


def _h_notify(cfg, ctx):
    summary = cfg.get("summary") or "D200H"
    body = cfg.get("body") or ""
    log.info("notify → %r %r", summary, body)
    subprocess.Popen(["notify-send", str(summary), str(body)],
                     stdin=subprocess.DEVNULL)


def _show_info_popup(title: str, lines: list[tuple[str, str]],
                     hint: str, ctx) -> None:
    """Popup Tk modal con N filas etiquetadas + un hint.

    `lines` es una lista de (label, valor). Si no hay display o Tk no
    está disponible, degrada a `notify-send`.

    Compartido entre `_h_stub` (acciones del conversor sin equivalente)
    y `_h_spotify` cuando faltan credenciales / device / etc.
    """
    body_pairs = "\n".join(f"{lbl:9s} {val}" for lbl, val in lines)
    body = f"{body_pairs}\n\n{hint}" if hint else body_pairs

    if _detect_display_server() == "unknown":
        _h_notify({"summary": title, "body": body}, ctx)
        return

    try:
        import tkinter as tk  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        log.debug("Tk no disponible (%s); fallback notify-send", exc)
        _h_notify({"summary": title, "body": body}, ctx)
        return

    def _show():
        try:
            root = tk.Tk()
            root.title(title)
            root.geometry("480x240")
            root.attributes("-topmost", True)
            frame = tk.Frame(root, padx=14, pady=12)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=title, font=("TkDefaultFont", 11, "bold"),
                     anchor="w", justify="left").pack(fill="x")
            for lbl, val in lines:
                tk.Label(frame, text=f"{lbl:9s} {val}", anchor="w",
                         justify="left", wraplength=440
                         ).pack(fill="x", pady=(4, 0))
            if hint:
                tk.Label(frame, text=hint, anchor="w", justify="left",
                         wraplength=440, fg="#666"
                         ).pack(fill="x", pady=(10, 0))
            tk.Button(frame, text="Cerrar", command=root.destroy,
                      width=10).pack(pady=(12, 0))
            root.after(8000, root.destroy)
            root.mainloop()
        except Exception as exc:  # pragma: no cover
            log.debug("Tk popup falló (%s); usando notify-send", exc)
            subprocess.Popen(["notify-send", title, body],
                             stdin=subprocess.DEVNULL)

    threading.Thread(target=_show, daemon=True).start()


def _h_stub(cfg, ctx):
    """Acción no traducida por el conversor.

    Abre una ventana Tk mínima con el comando + args para que el usuario
    sepa que el slot está reconocido pero pendiente de configurar a mano.
    Si no hay Tk o no hay display server → fallback a notify-send + log.
    """
    command = str(cfg.get("command", "(sin comando)"))
    raw_args = cfg.get("args", {})
    try:
        args_str = json.dumps(raw_args, ensure_ascii=False)
    except (TypeError, ValueError):
        args_str = repr(raw_args)
    hint = str(cfg.get("hint", "Edita el YAML del slot para configurar la acción real."))
    log.info("stub → command=%s args=%s", command, args_str)

    _show_info_popup(
        title="[stub] D200H — acción no implementada",
        lines=[("Comando:", command), ("Args:", args_str)],
        hint=hint,
        ctx=ctx,
    )


def _h_spotify(cfg, ctx):
    """Comandos Spotify Web API (play, pause, next, volume, like, …).

    Activación de la feature: se controla en `run()` durante el arranque
    del bridge — si está deshabilitada `ctx.spotify` es None y aquí
    abrimos un popup informativo. Si está habilitada pero falla el
    API (sin device, token caducado, red), se atrapa `SpotifyError` y
    se muestra un popup con el hint clasificado.
    """
    from . import spotify as _spotify  # noqa: PLC0415

    cmd = str(cfg.get("cmd", "")).strip()
    if not cmd:
        log.warning("spotify host_action sin `cmd`")
        return

    client = getattr(ctx, "spotify", None)
    if client is None:
        _show_info_popup(
            title="Spotify desactivado",
            lines=[("Comando:", cmd)],
            hint=(
                "La integración de Spotify está apagada. Para activarla:\n"
                "  1) Corre `uv run d200h spotify-auth` para obtener "
                "credenciales OAuth.\n"
                "  2) Asegúrate de que `D200H_SPOTIFY` NO esté en "
                "`0|false|off|no`."
            ),
            ctx=ctx,
        )
        return

    try:
        client.dispatch(cmd, cfg)
    except _spotify.SpotifyError as exc:
        log.warning("spotify %s → %s: %s", cmd, exc.kind, exc.hint)
        _show_info_popup(
            title=f"Spotify — {exc.kind}",
            lines=[("Comando:", cmd)],
            hint=exc.hint,
            ctx=ctx,
        )


def _h_delay(cfg, ctx):
    """Pausa la ejecución `ms` milisegundos. Sólo útil dentro de `multi`.

    Fuera de `multi` también funciona pero bloquea el dispatcher del bridge
    hasta que termine — no recomendado para slots independientes.
    """
    ms = int(cfg.get("ms", 0))
    if ms <= 0:
        return
    log.info("delay → %dms", ms)
    time.sleep(ms / 1000.0)


def _h_multi(cfg, ctx):
    actions = cfg.get("actions", [])
    log.info("multi → %d acciones", len(actions))
    # Las pausas se expresan como sub-acciones `{type: delay, ms: N}`.
    for sub in actions:
        _dispatch_host(sub, ctx)


# `type: page` host_action: cambia la página activa. Se procesa como un
# camino especial dentro del bridge (no es un Popen). Sirve para slots
# que en el manifest están como `system.open` (no nav firmware) pero
# que QUEREMOS que cambien de página vía el bridge.
def _h_page(cfg, ctx: "BridgeContext"):
    target = cfg.get("target")
    if target is None:
        log.warning("page sin target"); return
    ctx.request_page_change(str(target))


_HOST_HANDLERS = {
    "shell":             _h_shell,
    "keys":              _h_keys,
    "text":              _h_text,
    "media":             _h_media,
    "volume":            _h_volume,
    "brightness_host":   _h_brightness_host,
    "brightness_device": _h_brightness_device,
    "app":               _h_app,
    "close":             _h_close,
    "url":               _h_url,
    "system":            _h_system,
    "notify":            _h_notify,
    "page":              _h_page,
    "multi":             _h_multi,
    "delay":             _h_delay,
    "stub":              _h_stub,
    "spotify":           _h_spotify,
}


def _dispatch_host(cfg: dict[str, Any], ctx: "BridgeContext") -> None:
    type_ = cfg.get("type", "")
    handler = _HOST_HANDLERS.get(type_)
    if handler is None:
        log.warning("host_action.type %r desconocido. Tipos: %s",
                    type_, sorted(_HOST_HANDLERS)); return
    try:
        handler(cfg, ctx)
    except Exception as exc:
        log.error("host_action error: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Resolución de nav firmware → cambio de página
# ---------------------------------------------------------------------------

def _resolve_page_nav(slot: Slot, ctx: "BridgeContext") -> Optional[str]:
    """Si el slot tiene un fw_action de nav, devuelve el page_id destino.

    - page.goto   → ActionParam.Page tomado como page_id (string)
    - page.folder → ActionParam.ProfileUUID tomado como page_id; misma
                    semántica que page.goto en este bridge — se conserva
                    para poder darle pila propia más adelante
    - page.back   → ctx.previous_page (apilada al entrar)
    - page.indicator / system.* → None (no es nav firmware)

    `page.next`, `page.prev` y `page.switch` están deprecadas (ver
    `DEPRECATED_FW_ACTIONS` en manifest.py); el loader las rechaza, así
    que aquí nunca deberían llegar.
    """
    fa = slot.fw_action
    if fa == "page.goto":
        return str(slot.fw_param.get("Page", "")) or None
    if fa == "page.folder":
        return str(slot.fw_param.get("ProfileUUID", "")) or None
    if fa == "page.back":
        return ctx.previous_page
    return None


# ---------------------------------------------------------------------------
# Contexto del bridge
# ---------------------------------------------------------------------------

@dataclass
class BridgeContext:
    pages: dict[str, Page]
    zips: dict[str, bytes]
    active: str
    previous_page: Optional[str] = None
    requested_page: Optional[str] = None
    spotify: Any = None  # Optional[spotify.SpotifyClient]
    # "standby": el LCD está a brillo 0 (forzado por brightness_device cmd:zero).
    # Mientras está activo, el bridge intercepta el próximo press, restaura el
    # brillo previo (cache en disco) y NO ejecuta la acción del slot pulsado.
    # Objetivo: el usuario nunca dispara comandos a ciegas.
    standby_mode: bool = False

    def request_page_change(self, page_id: str) -> None:
        if page_id not in self.pages:
            log.warning("page_id %r no existe; ignorado", page_id)
            return
        self.requested_page = page_id

    def consume_page_request(self) -> Optional[str]:
        p = self.requested_page
        self.requested_page = None
        return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

DEFAULT_HOME_CANDIDATES = ("home", "page_0", "0")
KEEPALIVE_INTERVAL = 2.0
POLL_TIMEOUT = 0.1


def _pick_home(page_order: list[str]) -> str:
    for cand in DEFAULT_HOME_CANDIDATES:
        if cand in page_order:
            return cand
    return page_order[0]


def _change_page(client: hid.HidClient, ctx: BridgeContext,
                 new_id: str) -> None:
    if new_id == ctx.active:
        log.debug("page change → mismo page_id (%s); skip", new_id)
        return
    blob = ctx.zips.get(new_id)
    if blob is None:
        log.error("page change → %r sin ZIP compilado", new_id)
        return
    log.info("page change %s → %s (%d B)", ctx.active, new_id, len(blob))
    # Debug opcional: dump del ZIP enviado a /tmp/d200h_sent/<page>.zip
    # para comparar con `/tmp/temp.zip` del device (`adb pull`).
    # Activar con `D200H_DUMP_ZIPS=1` en el entorno.
    if os.environ.get("D200H_DUMP_ZIPS"):
        try:
            os.makedirs("/tmp/d200h_sent", exist_ok=True)
            with open(f"/tmp/d200h_sent/{new_id}.zip", "wb") as f:
                f.write(blob)
        except Exception:
            pass
    client.send_zip(blob)
    ctx.previous_page = ctx.active
    ctx.active = new_id


def _maybe_banner_examples_loaded(page_map: dict[str, "Page"]) -> None:
    """If the bridge is running with the shipped examples (no user pages),
    print a visible tip about the focus-pages feature so first-time users
    discover it. Detected by inspecting any loaded page's path.
    """
    if not page_map:
        return
    sample_path = next(iter(page_map.values())).path
    if "examples" not in sample_path.parts:
        return

    rules_path = config.config_root() / "focus_rules.yaml"
    example_path = config.config_root() / "focus_rules.yaml.example"
    if rules_path.is_file() or not example_path.is_file():
        return

    for line in (
        "",
        "  ┌─────────────────────────────────────────────────────────────────┐",
        "  │ Running with shipped EXAMPLE pages (config/pages/examples/).    │",
        "  │                                                                 │",
        "  │ TIP — auto-switch pages by focused window (X11 only):           │",
        "  │   cp config/focus_rules.yaml.example config/focus_rules.yaml    │",
        "  │   then restart the bridge. Focusing Chrome/Firefox jumps to     │",
        "  │   the 'browser' page; focusing Nautilus to 'files'.             │",
        "  │                                                                 │",
        "  │ Build your own pages in config/pages/user/ to replace these.    │",
        "  └─────────────────────────────────────────────────────────────────┘",
        "",
    ):
        log.info(line)


def run(*, home: Optional[str] = None) -> int:
    log.info("=== d200h bridge (HID) ===")

    # El daemon suele arrancar con `uv run d200h bridge`, y `uv run` exporta
    # VIRTUAL_ENV al entorno del proceso. Todos los host_action se lanzan con
    # subprocess.Popen heredando este os.environ, así que sin esto las apps y
    # terminales que abre el deck (p.ej. wezterm) nacerían "dentro" del venv
    # del bridge y arrastrarían el warning de uv (VIRTUAL_ENV no coincide) en
    # otros proyectos. Lo quitamos una vez: el intérprete ya en marcha no se
    # ve afectado, sólo dejamos de propagarlo a los procesos hijos.
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ.pop("VIRTUAL_ENV_PROMPT", None)

    check_tools()

    # 1. Compilar páginas
    try:
        page_map = pages.load_all()
        if not page_map:
            log.error("No hay páginas en config/pages/")
            return 1
        compiled: dict[str, bytes] = {}
        for pid, page in page_map.items():
            md, icons = pages.compile_page(page)
            compiled[pid] = zip_pack.pack(md, icons)
            log.info("  compilada %s: %d slots, %d iconos, %d B",
                     pid, len(page.slots), len(icons), len(compiled[pid]))
    except PageError as exc:
        log.error("Error compilando páginas: %s", exc); return 1

    # Visible tip when running with the shipped examples (no user pages).
    _maybe_banner_examples_loaded(page_map)

    page_order = sorted(page_map.keys())
    active = home or _pick_home(page_order)
    if active not in page_map:
        log.error("Home %r no existe. Páginas: %s", active, page_order); return 1

    ctx = BridgeContext(pages=page_map, zips=compiled, active=active)

    # 2. Spotify (opcional). Activado si existe `config/secrets/spotify.yaml`
    #    con credenciales válidas y D200H_SPOTIFY NO está apagado por env.
    ctx.spotify = _maybe_init_spotify()

    # 3. Focus watcher (cambio automático de página según ventana activa).
    #    Si no hay focus_rules.yaml → feature off, no hace nada.
    focus_watcher = _maybe_start_focus_watcher(ctx, set(compiled.keys()))

    # 4. Loop principal con reconexión
    stopping = False

    def _stop(signum, frame):
        nonlocal stopping
        log.info("Señal %d → cerrando", signum)
        stopping = True
        if focus_watcher is not None:
            focus_watcher.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    reconnect_delays = [1, 2, 4, 8, 16, 30]
    attempt = 0
    while not stopping:
        try:
            client = hid.HidClient.open()
        except hid.HidError as exc:
            d = reconnect_delays[min(attempt, len(reconnect_delays) - 1)]
            log.warning("HID no disponible: %s. Reintento en %ds…", exc, d)
            attempt += 1
            time.sleep(d)
            continue

        attempt = 0
        try:
            _session(client, ctx, lambda: stopping)
        except hid.HidError as exc:
            log.warning("Sesión cortada: %s. Reconectando…", exc)
        except Exception as exc:
            log.error("Error inesperado: %s", exc, exc_info=True)
        finally:
            client.close()

        if stopping:
            break
        d = reconnect_delays[min(attempt, len(reconnect_delays) - 1)]
        log.info("Reconectando en %ds…", d)
        attempt += 1
        time.sleep(d)

    if focus_watcher is not None:
        focus_watcher.stop()
    log.info("Bridge terminado.")
    return 0


def _maybe_init_spotify():
    """Activa el cliente Spotify si la config existe y el env lo permite.

    Reglas (ver docs/user/pages-guide.md y docs/user/spotify-setup.md):
    - `D200H_SPOTIFY=0|false|off|no` apaga la feature aunque exista el archivo.
    - Si `config/secrets/spotify.yaml` no existe → feature off.
    - Si existe pero no tiene client_id/client_secret válidos → feature off.

    En cualquier caso de "off", `_h_spotify` muestra un popup al primer
    press explicando cómo activarla; el bridge sigue arrancando.
    """
    from . import spotify as _spotify  # noqa: PLC0415
    if _spotify.env_disabled():
        log.info("Spotify: deshabilitado por D200H_SPOTIFY env")
        return None
    path = config.spotify_credentials_path()
    if not path.is_file():
        log.info("Spotify: deshabilitado (no hay %s)", path)
        return None
    return _spotify.SpotifyClient.from_config(path)


def _maybe_start_focus_watcher(ctx: "BridgeContext",
                               available: set[str]
                               ) -> Optional[focus.X11FocusWatcher]:
    """Carga focus_rules.yaml y arranca el watcher si aplica.

    El callback resuelve la regla y pide cambio de página al ctx. El
    bridge loop lo procesa en `consume_page_request()`.
    """
    cfg = focus.load_rules(config.focus_rules_path())
    if cfg is None:
        log.info("focus rules: deshabilitado (no hay config/focus_rules.yaml)")
        return None
    log.info("focus rules: %d reglas, default=%r", len(cfg.rules), cfg.default)
    for r in cfg.rules:
        log.info("  match=%r → page=%r%s",
                 r.match, r.page,
                 "" if r.page in available else "  [page no compilada]")

    def _on_focus(wm_class: Optional[str]) -> None:
        target = focus.resolve(cfg, wm_class, available)
        if target is None:
            return
        if target == ctx.active:
            log.debug("focus[%s]: ya estamos en %s", wm_class, target)
            return
        log.info("focus[%s] → %s", wm_class, target)
        ctx.request_page_change(target)

    watcher = focus.X11FocusWatcher(_on_focus)
    if not watcher.start():
        return None
    log.info("focus watcher arrancado (X11)")
    return watcher


def _session(client: hid.HidClient, ctx: BridgeContext,
             should_stop) -> None:
    """Una sesión completa contra un dispositivo conectado."""
    # Handshake + primer ZIP (incondicional — el firmware acaba de
    # entrar en modo host-managed y necesita el ZIP para pintar algo).
    #
    # Crítico: tras enviar el clock, el firmware emite varios IN reports
    # (0303 device-info + 0103 heartbeats). Si enviamos el ZIP antes de
    # drenarlos, el render falla silenciosamente (el firmware responde
    # con `010b` ACK a nivel HID pero NO renderiza la página). Drenar
    # ~250 ms — patrón del script RE que sí cambia pantalla.
    client.send_clock()
    drain_until = time.monotonic() + 0.25
    while time.monotonic() < drain_until:
        msg = client.read(timeout=0.05)
        if msg is not None and log.isEnabledFor(logging.DEBUG):
            log.debug("init drain IN: %s", msg.type_hex)
    initial_zip = ctx.zips[ctx.active]
    log.info("envío inicial: %s (%d B)", ctx.active, len(initial_zip))
    client.send_zip(initial_zip)

    # Restaura el último brillo conocido (best-effort, vía ADB). El firmware
    # no persiste el brillo entre reboots/handshakes; lo guardamos en
    # ~/.cache/d200h/brightness cada vez que `brightness_device` cambia el
    # valor. El handshake también limpia standby_mode: si el bridge volvió
    # de suspend con standby_mode=True y el handshake ya re-encendió el LCD,
    # no tiene sentido descartar el primer press post-resume.
    _restore_brightness_device()
    ctx.standby_mode = False

    last_keepalive = time.monotonic()

    while not should_stop():
        now = time.monotonic()
        if now - last_keepalive >= KEEPALIVE_INTERVAL:
            client.send_clock()
            last_keepalive = now

        msg = client.read(timeout=POLL_TIMEOUT)
        if msg is None:
            # Atender cambios pedidos por host_action: page mientras
            # esperamos input.
            pending = ctx.consume_page_request()
            if pending is not None:
                _change_page(client, ctx, pending)
            continue

        if msg.type_ == hid.T_IN_KEY:
            ev = hid.parse_key_event(msg)
            if ev is None:
                continue
            if ev.is_clock:
                log.debug("press slot reloj (id=13) — ignorado")
                continue
            if not ev.is_press:
                continue
            _on_press(client, ctx, ev.slot_id)
        elif msg.type_ == hid.T_IN_INFO:
            try:
                txt = msg.payload.rstrip(b"\x00").decode("utf-8")
                log.info("device info: %s", txt)
            except UnicodeDecodeError:
                log.debug("device info (non-utf8): %r", msg.payload[:80])
        elif msg.type_ in (hid.T_IN_HEARTBEAT, hid.T_IN_ZIP_ACK):
            log.debug("%s recibido", msg.type_hex)
        else:
            log.debug("IN type=%s declared_len=%d", msg.type_hex, msg.declared_len)

        pending = ctx.consume_page_request()
        if pending is not None:
            _change_page(client, ctx, pending)


def _on_press(client: hid.HidClient, ctx: BridgeContext, slot_id: int) -> None:
    # Standby mode: LCD a 0 desde el último `brightness_device cmd:zero`.
    # Cualquier press despierta (restaura brillo previo) y NO ejecuta el
    # slot, para que el usuario no dispare acciones a ciegas.
    if ctx.standby_mode:
        log.info("WAKE: standby mode, press slot_id=%d → restauro brillo y descarto acción",
                 slot_id)
        _restore_brightness_device(use_fallback=True)
        ctx.standby_mode = False
        return

    page = ctx.pages[ctx.active]
    slot = page.slots.get(slot_id)
    if slot is None:
        log.debug("press slot=%d en %s — slot vacío, ignorado",
                  slot_id, ctx.active)
        return

    log.info("PRESS page=%s slot_id=%d (fw=%s host=%s)",
             ctx.active, slot_id, slot.fw_action,
             slot.host_action.get("type") if slot.host_action else "-")

    # 1) Nav firmware
    target = _resolve_page_nav(slot, ctx)
    if target is not None:
        _change_page(client, ctx, target)
        return

    # 2) host_action arbitraria
    if slot.host_action:
        _dispatch_host(dict(slot.host_action), ctx)
