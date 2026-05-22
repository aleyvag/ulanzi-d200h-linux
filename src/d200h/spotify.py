"""Cliente Spotify Web API para el bridge.

Activación opcional (patrón análogo a `focus.py`):
- Si existe `config/secrets/spotify.yaml` con `client_id`/`client_secret`
  válidos → activo.
- La var de entorno `D200H_SPOTIFY=0|false|off|no` apaga la integración
  aunque exista el archivo (útil para deshabilitar temporalmente sin
  borrar las credenciales).
- Si falta `refresh_token` el cliente se construye igual pero falla con
  `SpotifyError("no_token", ...)` al primer dispatch, lo que el bridge
  convierte en un popup informativo.

Cliente HTTP: stdlib pura (`urllib.request`). Refresh transparente del
`access_token` cuando expira (con margen de 30s).

Comandos soportados (cfg["cmd"]):
- play, pause, play-pause       (PUT/GET /me/player)
- next, previous                (POST /me/player/next|previous)
- volume-up, volume-down        (lee estado, calcula nuevo %, PUT)
- volume-set     (cfg["value"] 0-100)
- shuffle                       (toggle shuffle_state)
- like                          (toggle save/unsave del track activo)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger("d200h.spotify")

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

SCOPES = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-library-modify "
    "user-library-read"
)


class SpotifyError(RuntimeError):
    """Error de Spotify con clasificación + hint legible para el popup."""

    def __init__(self, kind: str, hint: str):
        super().__init__(hint)
        self.kind = kind     # no_config | no_token | no_device | api_error | network
        self.hint = hint


def env_disabled() -> bool:
    val = os.environ.get("D200H_SPOTIFY", "").strip().lower()
    return val in {"0", "false", "off", "no"}


class SpotifyClient:
    """Cliente API stateful (cache access_token + device).

    Construido vía `from_config(path)` para que la activación sea
    consistente con el patrón de `focus.load_rules()`.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 credentials_path: Optional[Path] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        # Si se construyó vía from_config, recordamos el path para persistir
        # rotaciones de refresh_token (Spotify rota algunos tokens al refrescar).
        self.credentials_path = credentials_path
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._device_id: str = ""
        self._device_expires_at: float = 0.0

    @classmethod
    def from_config(cls, path: Path) -> Optional["SpotifyClient"]:
        if not path.is_file():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.error("spotify credentials YAML inválido: %s", exc)
            return None
        cid = str(data.get("client_id") or "").strip()
        cs = str(data.get("client_secret") or "").strip()
        rt = str(data.get("refresh_token") or "").strip()
        if not cid or not cs:
            log.warning("%s sin client_id/client_secret — Spotify desactivado",
                        path)
            return None
        client = cls(client_id=cid, client_secret=cs, refresh_token=rt,
                     credentials_path=path)
        if not rt:
            log.warning("%s sin refresh_token. Corre: uv run d200h spotify-auth",
                        path)
        else:
            log.info("Spotify habilitado (creds desde %s)", path)
        return client

    # ----- token management -----

    def _refresh(self) -> None:
        if not self.refresh_token:
            raise SpotifyError(
                "no_token",
                "No hay refresh_token. Corre: uv run d200h spotify-auth"
            )
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            err = _read_err(exc)
            log.error("spotify refresh falló: HTTP %d  body=%s",
                      exc.code, err[:300])
            if exc.code == 401 or "invalid_grant" in err:
                raise SpotifyError(
                    "no_token",
                    f"Refresh token rechazado por Spotify "
                    f"(HTTP {exc.code}: {err[:120]}). "
                    f"Re-autoriza: uv run d200h spotify-auth --force"
                ) from exc
            raise SpotifyError(
                "api_error",
                f"Refresh falló (HTTP {exc.code}): {err[:160]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SpotifyError("network", f"Red: {exc.reason}") from exc
        except Exception as exc:
            raise SpotifyError("api_error", f"Refresh error: {exc}") from exc

        self._access_token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - 30
        new_rt = payload.get("refresh_token")
        if new_rt and new_rt != self.refresh_token:
            self.refresh_token = new_rt
            self._persist_refresh_token(new_rt)
        log.info("spotify token refrescado (expires_in=%ds)",
                 payload.get("expires_in", 0))

    def _persist_refresh_token(self, new_rt: str) -> None:
        """Persiste un refresh_token rotado al archivo de credenciales.

        Spotify rota el refresh_token en algunas respuestas del refresh
        endpoint; el viejo queda invalidado en cuanto se confirma el
        nuevo. Si no lo persistimos, al reiniciar el bridge cargamos el
        token revocado y todo falla con HTTP 401.
        """
        if not self.credentials_path:
            log.warning("spotify: refresh_token rotado pero no hay path "
                        "para persistir — al reiniciar el bridge habrá que "
                        "correr spotify-auth de nuevo")
            return
        try:
            data = yaml.safe_load(
                self.credentials_path.read_text(encoding="utf-8")
            ) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.error("spotify: no pude leer %s para persistir token rotado: %s",
                      self.credentials_path, exc)
            return
        data["refresh_token"] = new_rt
        try:
            self.credentials_path.write_text(
                yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
            )
            try:
                os.chmod(self.credentials_path, 0o600)
            except OSError:
                pass
            log.info("spotify: refresh_token rotado y persistido en %s",
                     self.credentials_path)
        except OSError as exc:
            log.error("spotify: no pude escribir %s tras rotación: %s",
                      self.credentials_path, exc)

    def _ensure_token(self) -> None:
        if not self._access_token or time.time() >= self._expires_at:
            self._refresh()

    # ----- HTTP helper -----

    def _api(self, method: str, path: str, *,
             params: Optional[dict] = None,
             body: Any = None,
             _retry: bool = True,
             _transient_attempt: int = 0) -> dict:
        self._ensure_token()
        url = API_BASE + path
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None and v != ""}
            )
            if qs:
                url = f"{url}?{qs}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._access_token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Retry para 5xx y caídas de red: backoff 1-2-4 s, máx 3 reintentos.
        _TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                # Endpoints como POST /me/player/next, PUT volume y shuffle
                # devuelven 204 No Content. El body llega vacío o con sólo
                # whitespace — no intentes parsearlo como JSON.
                if r.status == 204:
                    return {}
                raw = r.read()
                if not raw or not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("spotify %s %s → body no-JSON (%d B); ignorado",
                              method, path, len(raw))
                    return {}
        except urllib.error.HTTPError as exc:
            err = _read_err(exc)
            if exc.code == 401 and _retry:
                # Token caducó entre el check y el request — fuerza refresh y
                # reintenta UNA vez. Si vuelve a 401, no hay token válido.
                log.debug("spotify 401 — forzando refresh y reintento")
                self._access_token = ""
                return self._api(method, path, params=params, body=body,
                                 _retry=False)
            if exc.code == 401:
                raise SpotifyError(
                    "no_token",
                    "Token rechazado. Corre: uv run d200h spotify-auth"
                ) from exc
            if exc.code == 403:
                raise SpotifyError(
                    "api_error",
                    "Spotify rechazó la acción (HTTP 403). Típicamente: "
                    "cuenta sin Premium (la Web API no permite controlar "
                    "playback sin Premium)."
                ) from exc
            if exc.code == 404:
                raise SpotifyError(
                    "no_device",
                    "No hay dispositivo Spotify activo. Abre la app o "
                    "el web player y vuelve a pulsar."
                ) from exc
            if exc.code == 429:
                raise SpotifyError(
                    "api_error",
                    "Rate limit de Spotify (HTTP 429). Espera unos segundos."
                ) from exc
            if 500 <= exc.code < 600 and _transient_attempt < len(_TRANSIENT_BACKOFF):
                wait = _TRANSIENT_BACKOFF[_transient_attempt]
                log.warning("spotify HTTP %d transitorio — reintento %d en %.1fs",
                            exc.code, _transient_attempt + 1, wait)
                time.sleep(wait)
                return self._api(method, path, params=params, body=body,
                                 _retry=_retry,
                                 _transient_attempt=_transient_attempt + 1)
            raise SpotifyError(
                "api_error", f"HTTP {exc.code}: {err[:160]}"
            ) from exc
        except urllib.error.URLError as exc:
            if _transient_attempt < len(_TRANSIENT_BACKOFF):
                wait = _TRANSIENT_BACKOFF[_transient_attempt]
                log.warning("spotify red caída (%s) — reintento %d en %.1fs",
                            exc.reason, _transient_attempt + 1, wait)
                time.sleep(wait)
                return self._api(method, path, params=params, body=body,
                                 _retry=_retry,
                                 _transient_attempt=_transient_attempt + 1)
            raise SpotifyError("network", f"Red: {exc.reason}") from exc
        except Exception as exc:
            raise SpotifyError("api_error", f"Spotify: {exc}") from exc

    # ----- device resolution -----

    def _resolve_device(self, hint: str = "") -> Optional[str]:
        """Si `hint` es un device_id concreto, usar; si `latest`/vacío,
        pedir la lista a Spotify (cache 30s) y elegir el activo (o el
        primero)."""
        hint = (hint or "").strip()
        if hint and hint != "latest":
            return hint
        now = time.time()
        if self._device_id and now < self._device_expires_at:
            return self._device_id
        payload = self._api("GET", "/me/player/devices")
        devices = payload.get("devices") or []
        if not devices:
            self._device_id = ""
            self._device_expires_at = now + 5
            return None
        active = next((d for d in devices if d.get("is_active")), None)
        chosen = (active or devices[0]).get("id") or ""
        self._device_id = chosen
        self._device_expires_at = now + 30
        return chosen or None

    # ----- dispatch -----

    KNOWN_CMDS = {
        "play", "pause", "play-pause",
        "next", "previous",
        "volume-up", "volume-down", "volume-set",
        "shuffle", "like",
    }

    def dispatch(self, cmd: str, cfg: dict) -> None:
        if cmd not in self.KNOWN_CMDS:
            raise SpotifyError(
                "api_error",
                f"Spotify cmd desconocido: {cmd!r}. "
                f"Válidos: {sorted(self.KNOWN_CMDS)}"
            )
        device_id = self._resolve_device(str(cfg.get("device_id") or ""))
        if not device_id:
            raise SpotifyError(
                "no_device",
                "No hay dispositivo Spotify activo. Abre la app de "
                "Spotify (desktop, móvil o web player) y vuelve a pulsar."
            )
        params = {"device_id": device_id}
        log.info("spotify → %s (device=%s)", cmd, device_id[:8])

        if cmd == "play":
            self._api("PUT", "/me/player/play", params=params, body={})
        elif cmd == "pause":
            self._api("PUT", "/me/player/pause", params=params)
        elif cmd == "play-pause":
            state = self._api("GET", "/me/player", params=params)
            if state.get("is_playing"):
                self._api("PUT", "/me/player/pause", params=params)
            else:
                self._api("PUT", "/me/player/play", params=params, body={})
        elif cmd == "next":
            self._api("POST", "/me/player/next", params=params)
        elif cmd == "previous":
            self._api("POST", "/me/player/previous", params=params)
        elif cmd in ("volume-up", "volume-down"):
            step = int(cfg.get("step", 10))
            state = self._api("GET", "/me/player", params=params)
            cur = int((state.get("device") or {}).get("volume_percent") or 50)
            new = cur + step if cmd == "volume-up" else cur - step
            new = max(0, min(100, new))
            self._api("PUT", "/me/player/volume",
                      params={"volume_percent": new, "device_id": device_id})
        elif cmd == "volume-set":
            value = int(cfg.get("value", 50))
            value = max(0, min(100, value))
            self._api("PUT", "/me/player/volume",
                      params={"volume_percent": value, "device_id": device_id})
        elif cmd == "shuffle":
            state = self._api("GET", "/me/player", params=params)
            new_state = "false" if state.get("shuffle_state") else "true"
            self._api("PUT", "/me/player/shuffle",
                      params={"state": new_state, "device_id": device_id})
        elif cmd == "like":
            state = self._api("GET", "/me/player", params=params)
            track_id = ((state.get("item") or {}).get("id") or "").strip()
            if not track_id:
                raise SpotifyError(
                    "api_error",
                    "No hay un track en reproducción para marcar como like."
                )
            saved = self._api("GET", "/me/tracks/contains",
                              params={"ids": track_id})
            already = bool(saved[0]) if isinstance(saved, list) and saved else False
            if already:
                self._api("DELETE", "/me/tracks", params={"ids": track_id})
                log.info("spotify like → removed %s", track_id[:8])
            else:
                self._api("PUT", "/me/tracks", params={"ids": track_id})
                log.info("spotify like → saved %s", track_id[:8])

    # ----- status / debug -----

    def describe(self) -> dict:
        """Devuelve un dict con el estado actual (útil para spotify-status)."""
        info: dict = {
            "client_id": self.client_id,
            "has_refresh_token": bool(self.refresh_token),
            "access_token_valid": bool(
                self._access_token and time.time() < self._expires_at
            ),
        }
        try:
            self._ensure_token()
            info["access_token_valid"] = True
            devices = self._api("GET", "/me/player/devices").get("devices") or []
            info["devices"] = [
                {"id": d.get("id"), "name": d.get("name"),
                 "type": d.get("type"), "is_active": d.get("is_active")}
                for d in devices
            ]
            player = self._api("GET", "/me/player")
            info["is_playing"] = player.get("is_playing")
            item = player.get("item") or {}
            if item:
                artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []))
                info["current_track"] = f"{artists} — {item.get('name', '')}"
        except SpotifyError as exc:
            info["error"] = f"{exc.kind}: {exc.hint}"
        return info


def _read_err(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")
    except Exception:
        return ""
