"""Instalación del bridge como systemd user service.

Escribe `~/.config/systemd/user/d200h.service` con paths resueltos a
absolutos. Diseñado para usuarios con sesión gráfica (la unit usa
`WantedBy=graphical-session.target`, que arranca el servicio después
de que el entorno gráfico esté listo y comparte DISPLAY/XAUTHORITY).

Suspend/resume: el bridge maneja la reconexión por sí mismo
(`reconnect_delays` en bridge.py). No se necesita listener DBus para
`PrepareForSleep` salvo que se confirme que el reconnect natural falla.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

UNIT_NAME = "d200h.service"


def user_unit_dir() -> Path:
    """Directorio donde systemd busca units de usuario."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def user_unit_path() -> Path:
    return user_unit_dir() / UNIT_NAME


def _resolve_uv() -> str:
    """Devuelve la ruta absoluta de `uv` para usar en ExecStart."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "`uv` no está en PATH. Instálalo (https://github.com/astral-sh/uv) "
            "o pasa --exec-start con un comando alternativo "
            "(p.ej. `--exec-start /ruta/a/.venv/bin/d200h bridge`)."
        )
    return uv


def render_unit(project_dir: Path, exec_start: str) -> str:
    return dedent(f"""\
        [Unit]
        Description=Ulanzi D200H bridge (HID + focus-aware page switching)
        Documentation=file://{project_dir}/README.md
        After=graphical-session.target
        PartOf=graphical-session.target

        [Service]
        Type=simple
        WorkingDirectory={project_dir}
        ExecStart={exec_start}
        # Salida sin buffering → logs en tiempo real en `journalctl --user -u d200h`.
        Environment=PYTHONUNBUFFERED=1
        Restart=on-failure
        RestartSec=2
        # SIGTERM = parada limpia (logout, suspend gestionado, systemctl stop).
        # No reiniciar en ese caso.
        RestartPreventExitStatus=SIGTERM

        [Install]
        WantedBy=graphical-session.target
        """)


def install(project_dir: Path | None = None, *,
            exec_start: str | None = None,
            force: bool = False) -> Path:
    """Escribe la unit file con paths resueltos. Devuelve la ruta final.

    `project_dir`: raíz del repo (donde está `pyproject.toml`). Default = cwd.
    `exec_start`: comando completo del ExecStart. Default = `uv run d200h bridge`.
    `force`: sobreescribe si ya existe.
    """
    if project_dir is None:
        project_dir = Path.cwd().resolve()
    if not (project_dir / "pyproject.toml").is_file():
        raise RuntimeError(
            f"{project_dir} no parece la raíz del proyecto "
            "(no encuentro pyproject.toml). Pasa --project-dir explícito."
        )
    if exec_start is None:
        exec_start = f"{_resolve_uv()} run d200h bridge"
    target = user_unit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        raise FileExistsError(
            f"{target} ya existe. Usa --force para sobreescribir."
        )
    target.write_text(render_unit(project_dir, exec_start), encoding="utf-8")
    return target


def uninstall() -> bool:
    """Elimina la unit file. Devuelve True si existía."""
    target = user_unit_path()
    if not target.exists():
        return False
    target.unlink()
    return True


# ---------------------------------------------------------------------------
# Operaciones de ciclo de vida (systemctl --user / loginctl).
#
# Línea dura de diseño: estas funciones NUNCA llaman a `sudo` ni matan
# procesos. `enable_linger` lo intenta sin privilegios y, si no puede,
# devuelve False para que el caller imprima el comando manual — no escala
# por su cuenta. La escalada a sudo y el matar bridges sueltos viven en
# scripts/persist-daemon.sh, que es el wrapper "hazlo funcionar a la fuerza".
# ---------------------------------------------------------------------------

class SystemctlError(RuntimeError):
    """Falló un comando systemctl/loginctl (o no existe el binario)."""


def current_user() -> str:
    return os.environ.get("USER") or getpass.getuser()


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=check, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SystemctlError(
            f"`{cmd[0]}` no está disponible: ¿una distro sin systemd?"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise SystemctlError(
            f"`{' '.join(cmd)}` falló (rc={exc.returncode}): {detail}"
        ) from exc


def daemon_reload() -> None:
    _run(["systemctl", "--user", "daemon-reload"])


def enable_now() -> None:
    """`systemctl --user enable --now d200h.service` (instala symlink + arranca)."""
    _run(["systemctl", "--user", "enable", "--now", UNIT_NAME])


def disable_now() -> None:
    """`disable --now`. check=False: si no estaba activo no es error fatal."""
    _run(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)


def linger_enabled(user: str | None = None) -> bool:
    user = user or current_user()
    proc = _run(
        ["loginctl", "show-user", user, "--property=Linger"], check=False
    )
    return "Linger=yes" in (proc.stdout or "")


def enable_linger(user: str | None = None) -> bool:
    """Intenta activar linger SIN sudo. Devuelve True si quedó activo.

    Si falla (típicamente por requerir privilegios) NO escala a sudo: el
    caller debe imprimir `sudo loginctl enable-linger <user>` para que el
    usuario decida. Idempotente: si ya estaba activo devuelve True.
    """
    user = user or current_user()
    if linger_enabled(user):
        return True
    _run(["loginctl", "enable-linger", user], check=False)
    return linger_enabled(user)
