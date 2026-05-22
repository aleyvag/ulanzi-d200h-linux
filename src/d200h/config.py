"""Helpers de localización de archivos del proyecto.

La validación y schema de páginas viven en `pages.py`. Aquí sólo se
resuelven rutas (config_root, pages_dir, icons_dir).
"""
from __future__ import annotations

import os
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def config_root() -> Path:
    """Localiza el directorio `config/` del proyecto.

    Prioridad:
    1. Env var `D200H_CONFIG_DIR` (apunta directamente al directorio config/).
    2. `./config` relativo al cwd.
    3. Buscar ascendiendo desde el archivo del paquete.
    """
    env = os.environ.get("D200H_CONFIG_DIR")
    if env:
        path = Path(env).expanduser().resolve()
        if not path.is_dir():
            raise ConfigError(f"D200H_CONFIG_DIR no es un directorio: {path}")
        return path
    cwd_cfg = Path.cwd() / "config"
    if cwd_cfg.is_dir():
        return cwd_cfg
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config").is_dir():
            return parent / "config"
    raise ConfigError(
        "No se encontró el directorio `config/`. Ejecuta el comando desde la "
        "raíz del proyecto o exporta D200H_CONFIG_DIR=/ruta/al/config."
    )


def pages_dir() -> Path:
    return config_root() / "pages"


def icons_dir() -> Path:
    return config_root() / "icons"


def focus_rules_path() -> Path:
    return config_root() / "focus_rules.yaml"


def secrets_dir() -> Path:
    return config_root() / "secrets"


def spotify_credentials_path() -> Path:
    return secrets_dir() / "spotify.yaml"
