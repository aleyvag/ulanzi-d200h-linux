"""Wrapper minimalista alrededor de `adb` (subprocess)."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ADB_BIN = "adb"


class AdbError(RuntimeError):
    pass


@dataclass
class AdbDevice:
    serial: str
    state: str  # "device", "unauthorized", "offline", ...

    @property
    def ready(self) -> bool:
        return self.state == "device"


def _ensure_adb() -> str:
    path = shutil.which(ADB_BIN)
    if not path:
        raise AdbError(
            "No se encontró el binario 'adb'. Instálalo con `sudo apt install adb` "
            "(o el equivalente en tu distro) y vuelve a intentarlo."
        )
    return path


def _run(args: list[str], *, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    bin_ = _ensure_adb()
    try:
        result = subprocess.run(
            [bin_, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb {' '.join(args)} agotó el tiempo ({timeout}s)") from exc

    if check and result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise AdbError(f"adb {' '.join(args)} falló: {stderr}")
    return result


def devices() -> list[AdbDevice]:
    out = _run(["devices"]).stdout.splitlines()
    found: list[AdbDevice] = []
    for line in out[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            found.append(AdbDevice(serial=parts[0], state=parts[1]))
    return found


def require_device() -> AdbDevice:
    devs = [d for d in devices() if d.ready]
    if not devs:
        raise AdbError(
            "Ningún D200H conectado por ADB. Comprueba con `adb devices` "
            "y revisa la regla udev (ver README)."
        )
    if len(devs) > 1:
        serials = ", ".join(d.serial for d in devs)
        raise AdbError(
            f"Hay {len(devs)} dispositivos ADB en estado 'device': {serials}. "
            "Desconecta los que no sean el D200H."
        )
    return devs[0]


def push(local: Path, remote: str, *, serial: Optional[str] = None) -> None:
    args = ["push", str(local), remote]
    if serial:
        args = ["-s", serial, *args]
    _run(args, timeout=60)


def shell(cmd: str, *, serial: Optional[str] = None, check: bool = True) -> str:
    args = ["shell", cmd]
    if serial:
        args = ["-s", serial, *args]
    return _run(args, check=check).stdout


# `reload_ui` (killall UlanziDeckKey) eliminado intencionalmente. Causaba
# el "logo flash" del modelo viejo. Con el bridge HID nuevo, NUNCA hay
# que matar el firmware — el cambio de pantalla va por HID OUT. Si por
# alguna razón el firmware se cuelga, reiniciar el device físicamente.
