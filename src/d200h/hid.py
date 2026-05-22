"""Cliente HID vendor del Ulanzi D200H.

Habla directamente con la interface 0 del device (`/dev/hidraw*`),
reports de 1024 bytes. Protocolo descubierto en sesión 2026-05-11
y documentado en `docs/developer/firmware-protocol.md`.

Resumen del protocolo:
  - Marker `7c 7c` al inicio de cada report.
  - Bytes [2..3] = tipo, [4..7] = length LE, [8..] = payload.
  - OUT 0x0001  → ZIP de página (chunked, sin header en chunks de
                  continuación).
  - OUT 0x0006  → texto delimitado por `|` (sync de reloj / keepalive).
  - IN  0x0101  → pulsación de slot (byte[8]=categoría, byte[9]=slot_id,
                  byte[11]=press/release).
  - IN  0x0103  → ACK / heartbeat del device.
  - IN  0x010b  → ACK tras recibir un ZIP.
  - IN  0x0303  → device info JSON (sólo una vez por sesión).
"""
from __future__ import annotations

import errno
import logging
import os
import select
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger("d200h.hid")

REPORT_SIZE = 1024
HEADER_LEN = 8                # 7c7c + type(2) + length_le(4)
PAYLOAD_OFFSET = 8

VID = 0x2207
PID = 0x0019

# Tipos de mensaje (bytes [2..3], BE para legibilidad).
T_OUT_ZIP = 0x0001
T_OUT_TEXT = 0x0006
T_IN_KEY = 0x0101
T_IN_HEARTBEAT = 0x0103
T_IN_ZIP_ACK = 0x010b
T_IN_INFO = 0x0303

# Categoría del slot en byte[8] del IN 0x0101.
SLOT_CAT_GRID = 0x01          # 13 slots configurables (id 0..12)
SLOT_CAT_CLOCK = 0x00         # slot doble del reloj (id=13)

CLOCK_SLOT_ID = 13


class HidError(RuntimeError):
    """Errores genéricos del canal HID (apertura, write, etc.)."""


# ---------------------------------------------------------------------------
# Descubrimiento del /dev/hidraw correcto
# ---------------------------------------------------------------------------

def find_hidraw(vid: int = VID, pid: int = PID, *,
                interface: int = 0) -> Optional[Path]:
    """Localiza el /dev/hidraw* asociado a (vid, pid, interface).

    Sin reportar interface en /sys directamente; lo deducimos de la ruta
    del device padre (`<bus>:<addr>.<config>:<iface>`).
    """
    # El kernel formatea HID_ID como `<bus>:<vid 8 hex>:<pid 8 hex>` en mayúsculas,
    # con padding a 8 chars (ej. `0003:00002207:00000019`).
    target = f":{vid:08X}:{pid:08X}".upper()
    base = Path("/sys/class/hidraw")
    if not base.is_dir():
        return None
    for hidraw in sorted(base.iterdir()):
        uevent = hidraw / "device" / "uevent"
        if not uevent.is_file():
            continue
        try:
            text = uevent.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if target not in text.upper():
            continue
        # Ruta canónica del device padre -> .../<bus>:1.<iface>/...
        dev_link = hidraw / "device"
        try:
            iface_dir = Path(os.path.realpath(dev_link)).parent
            iface_no = (iface_dir / "bInterfaceNumber").read_text().strip()
        except OSError:
            iface_no = ""
        if iface_no != f"{interface:02d}":
            continue
        return Path(f"/dev/{hidraw.name}")
    return None


# ---------------------------------------------------------------------------
# Construcción de reports OUT
# ---------------------------------------------------------------------------

def _pad(buf: bytes) -> bytes:
    """Rellena con ceros hasta REPORT_SIZE bytes."""
    if len(buf) > REPORT_SIZE:
        raise HidError(f"report demasiado grande ({len(buf)} > {REPORT_SIZE})")
    if len(buf) < REPORT_SIZE:
        return buf + b"\x00" * (REPORT_SIZE - len(buf))
    return buf


def build_clock_payload(now: Optional[datetime] = None,
                        fmt_12h: bool = True) -> bytes:
    """Payload textual del 0x0006: `1|2|9|HH:MM:SS|1|{12H|24H}`.

    Campos 0, 1, 2 y 4 siguen sin decodificarse (ver §12 PENDIENTES del
    doc); replicamos lo observado en la captura USB de la app oficial.
    """
    when = (now or datetime.now()).strftime("%H:%M:%S")
    fmt = "12H" if fmt_12h else "24H"
    return f"1|2|9|{when}|1|{fmt}".encode("ascii")


def build_text_report(payload: bytes) -> bytes:
    body = b"\x7c\x7c" + struct.pack(">H", T_OUT_TEXT) + struct.pack("<I", len(payload)) + payload
    return _pad(body)


def build_clock_report(now: Optional[datetime] = None,
                       fmt_12h: bool = True) -> bytes:
    return build_text_report(build_clock_payload(now, fmt_12h))


def chunk_zip_reports(zip_bytes: bytes) -> list[bytes]:
    """Parte un ZIP en reports HID listos para enviar.

    Layout:
      report 0: 7c7c 0001 <len LE 4B> <primeros (REPORT_SIZE-8) bytes>
      report N: <REPORT_SIZE bytes RAW del ZIP, sin header>
    """
    length = len(zip_bytes)
    if length == 0:
        raise HidError("ZIP vacío")
    reports: list[bytes] = []
    first_payload = zip_bytes[: REPORT_SIZE - HEADER_LEN]
    first = b"\x7c\x7c" + struct.pack(">H", T_OUT_ZIP) + struct.pack("<I", length) + first_payload
    reports.append(_pad(first))
    pos = REPORT_SIZE - HEADER_LEN
    while pos < length:
        chunk = zip_bytes[pos: pos + REPORT_SIZE]
        reports.append(_pad(chunk))
        pos += REPORT_SIZE
    return reports


# ---------------------------------------------------------------------------
# Parsing de IN
# ---------------------------------------------------------------------------

@dataclass
class HidMessage:
    """Mensaje recibido del device."""
    type_: int                # bytes [2..3] como uint16 BE
    declared_len: int         # bytes [4..7] como uint32 LE
    payload: bytes            # bytes [8..8+declared_len]
    raw: bytes                # report completo (1024 B)

    @property
    def type_hex(self) -> str:
        return f"{self.type_ >> 8:02x}{self.type_ & 0xff:02x}"


@dataclass
class KeyEvent:
    """Pulsación/liberación de un slot."""
    slot_id: int              # 0..12 para slots regulares, 13 para el reloj
    is_press: bool            # True = press, False = release
    is_clock: bool            # True si byte[8]==SLOT_CAT_CLOCK (slot reloj)

    @property
    def is_release(self) -> bool:
        return not self.is_press


def parse_report(buf: bytes) -> Optional[HidMessage]:
    """Parsea un report bruto. Devuelve None si no tiene el magic 7c7c."""
    if len(buf) < HEADER_LEN or buf[0] != 0x7c or buf[1] != 0x7c:
        return None
    type_ = (buf[2] << 8) | buf[3]
    declared_len = struct.unpack("<I", buf[4:8])[0]
    payload_end = min(PAYLOAD_OFFSET + declared_len, len(buf))
    payload = bytes(buf[PAYLOAD_OFFSET:payload_end])
    return HidMessage(type_=type_, declared_len=declared_len,
                      payload=payload, raw=bytes(buf))


def parse_key_event(msg: HidMessage) -> Optional[KeyEvent]:
    """Si el mensaje es un 0x0101 válido, devuelve un KeyEvent."""
    if msg.type_ != T_IN_KEY or len(msg.raw) < 12:
        return None
    cat = msg.raw[8]
    slot_id = msg.raw[9]
    state = msg.raw[11]
    is_clock = cat == SLOT_CAT_CLOCK
    # En slots de la grilla el firmware reporta slot_id 0..12; en el reloj id=13.
    return KeyEvent(slot_id=slot_id, is_press=state == 0x01, is_clock=is_clock)


# ---------------------------------------------------------------------------
# Cliente HID
# ---------------------------------------------------------------------------

class HidClient:
    """Wrapper alrededor de un fd de /dev/hidraw* del D200H.

    Uso típico:

        with HidClient.open() as hid:
            hid.send_clock()                 # handshake
            hid.send_zip(zip_bytes)
            while True:
                msg = hid.read(timeout=1.0)
                if msg and msg.type_ == T_IN_KEY:
                    ev = parse_key_event(msg)
                    ...
                if time.monotonic() - last > 2.0:
                    hid.send_clock()
                    last = time.monotonic()
    """

    def __init__(self, path: Path, fd: int):
        self.path = path
        self._fd = fd

    @classmethod
    def open(cls, path: Optional[Path] = None, *,
             vid: int = VID, pid: int = PID, interface: int = 0) -> "HidClient":
        target = path or find_hidraw(vid, pid, interface=interface)
        if target is None:
            raise HidError(
                f"No se encontró /dev/hidraw* para VID:PID {vid:04x}:{pid:04x} "
                f"interface {interface}. ¿Está conectado el D200H? "
                f"¿udev permite acceso al device? Ver §11 del doc v3."
            )
        try:
            fd = os.open(str(target), os.O_RDWR)
        except OSError as exc:
            raise HidError(
                f"No se pudo abrir {target} ({exc.strerror}). "
                f"Revisa permisos: la regla udev de este repo debería dejar "
                f"hidraw del D200H en 0666 (con TAG+=uaccess)."
            ) from exc
        log.info("HID conectado: %s (%04x:%04x iface=%d)",
                 target, vid, pid, interface)
        return cls(target, fd)

    def __enter__(self) -> "HidClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None  # type: ignore[assignment]

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise HidError("HidClient cerrado")
        return self._fd

    # ----- escritura -----

    def write_raw(self, report: bytes) -> int:
        # Linux hidraw con descriptor unnumbered (D200H iface 0 no tiene
        # tag Report ID 0x85) exige que el write empiece con byte 0x00 de
        # report ID, seguido del report. Sin esto, el kernel interpreta
        # byte[0] del report como ID, lo que provoca pérdidas de byte
        # intermitentes dependientes de contenido. Ver Test B sesión 2026-05-18.
        buf = b"\x00" + report
        try:
            n = os.write(self.fd, buf)
        except OSError as exc:
            raise HidError(f"write fallo: {exc}") from exc
        if n != len(buf):
            raise HidError(f"short write: {n}/{len(buf)} bytes")
        return n

    def send_clock(self, *, now: Optional[datetime] = None,
                   fmt_12h: bool = True) -> None:
        self.write_raw(build_clock_report(now=now, fmt_12h=fmt_12h))

    def send_zip(self, zip_bytes: bytes, *, retries: int = 0) -> int:
        """Envía un ZIP fragmentado. Devuelve el número de reports enviados.

        `retries=0` (default) envía el ZIP una sola vez. Para volver a
        intentar tras fallo de ACK pasa `retries=N` (envía N+1 veces y
        espera el ACK `010b` entre intentos). Ya no es necesario por
        defecto: el byte-drop histórico se debía al Report ID omitido —
        ver docs/developer/firmware-protocol.md §4.4.3.
        """
        reports = chunk_zip_reports(zip_bytes)
        log.debug("send_zip: %d reports (%d bytes) [x%d]",
                  len(reports), len(zip_bytes), retries + 1)
        for attempt in range(retries + 1):
            for r in reports:
                self.write_raw(r)
            # Esperar el ACK 010b del firmware (timeout corto).
            deadline = self._monotonic_now() + 0.5
            got_ack = False
            while self._monotonic_now() < deadline:
                msg = self.read(timeout=0.05)
                if msg is None:
                    continue
                if msg.type_ == T_IN_ZIP_ACK:
                    got_ack = True
                    break
            if not got_ack:
                log.debug("send_zip: no ACK 010b tras intento %d", attempt + 1)
        return len(reports)

    @staticmethod
    def _monotonic_now() -> float:
        import time as _t
        return _t.monotonic()

    # ----- lectura -----

    def read(self, timeout: Optional[float] = 0.1) -> Optional[HidMessage]:
        """Lee un report (1024 B) y devuelve el HidMessage parseado.

        timeout en segundos; None = bloqueante; 0 = no-bloqueante.
        Devuelve None si no había nada listo, o si el magic no encaja.
        """
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        try:
            buf = os.read(self.fd, REPORT_SIZE)
        except OSError as exc:
            if exc.errno in (errno.ENODEV, errno.EIO):
                raise HidError(f"device desconectado: {exc}") from exc
            raise HidError(f"read fallo: {exc}") from exc
        return parse_report(buf)

    def iter_messages(self, *, poll_timeout: float = 0.05
                      ) -> Iterator[HidMessage]:
        """Generador infinito de mensajes. El consumidor decide cuándo parar."""
        while True:
            msg = self.read(timeout=poll_timeout)
            if msg is not None:
                yield msg
