"""Flujo OAuth2 (Authorization Code + PKCE) para Spotify.

Levanta un servidor HTTP en `127.0.0.1:30901` para capturar el
`code` del redirect (el mismo puerto y path que usa la app oficial
de Ulanzi en Windows: `http://127.0.0.1:30901/oauth2callback`).

Uso: el subcomando CLI `d200h spotify-auth` lo invoca, le pasa el
`client_id` + `client_secret` desde `config/secrets/spotify.yaml` y
escribe el `refresh_token` resultante al mismo archivo.

Dependencias: stdlib pura (http.server, urllib, secrets, hashlib,
webbrowser) + pyyaml (que ya viene en el proyecto).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger("d200h.spotify_auth")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-library-modify "
    "user-library-read"
)
DEFAULT_PORT = 30901


def _pkce_pair() -> tuple[str, str]:
    """Devuelve (verifier, challenge) según RFC 7636 (S256)."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


# Estado compartido del callback (single-shot: el server se apaga después).
_captured: dict[str, Optional[str]] = {"code": None, "state": None, "error": None}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silenciar el access log
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        if "error" in q:
            _captured["error"] = q["error"][0]
            self.send_response(400)
        else:
            _captured["code"] = q.get("code", [None])[0]
            _captured["state"] = q.get("state", [None])[0]
            self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = _captured["code"] is not None
        msg = ("Autorización recibida. Puedes cerrar esta pestaña."
               if ok else f"Error: {_captured['error']}")
        body = (
            "<!doctype html><html><body style='font-family:sans-serif;"
            "max-width:560px;margin:48px auto;color:#222'>"
            "<h2>d200h — Spotify auth</h2>"
            f"<p>{msg}</p></body></html>"
        )
        self.wfile.write(body.encode("utf-8"))


def run_auth_flow(client_id: str, client_secret: str, *,
                  port: int = DEFAULT_PORT,
                  open_browser: bool = True,
                  timeout_s: int = 300) -> dict[str, Any]:
    """Ejecuta el flujo end-to-end. Bloquea hasta callback o timeout.

    Devuelve el dict raw del token endpoint (campos `access_token`,
    `refresh_token`, `expires_in`, `scope`, …) + `obtained_at`.
    """
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()

    _captured.update({"code": None, "state": None, "error": None})
    try:
        httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        raise RuntimeError(
            f"No pude abrir el servidor de callback en 127.0.0.1:{port} "
            f"({exc}). ¿Hay otro proceso usando ese puerto?"
        ) from exc

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        log.info("Esperando autorización en %s …", redirect_uri)
        print(f"\nAbre esta URL en tu navegador para autorizar:\n  {url}\n")
        if open_browser:
            try:
                webbrowser.open(url, new=1)
            except Exception:
                pass

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _captured["code"] or _captured["error"]:
                break
            time.sleep(0.2)
    finally:
        httpd.shutdown()
        httpd.server_close()

    if _captured["error"]:
        raise RuntimeError(
            f"Spotify rechazó la autorización: {_captured['error']}"
        )
    code = _captured["code"]
    if not code:
        raise RuntimeError(
            f"Timeout esperando el callback de Spotify ({timeout_s} s). "
            f"¿Abriste la URL y autorizaste?"
        )
    if _captured["state"] != state:
        raise RuntimeError("State mismatch en el callback — posible CSRF. Abortado.")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            err = exc.read().decode("utf-8", "replace")
        except Exception:
            err = ""
        raise RuntimeError(
            f"Spotify rechazó el code exchange (HTTP {exc.code}): {err[:200]}"
        ) from exc

    payload["obtained_at"] = int(time.time())
    return payload


def save_credentials(path: Path, *, client_id: str, client_secret: str,
                     refresh_token: str, scope: str,
                     obtained_at: int) -> None:
    """Escribe `config/secrets/spotify.yaml` con permisos 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": scope,
        "obtained_at": obtained_at,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_existing(path: Path) -> dict[str, Any]:
    """Carga credenciales existentes (o {}). Útil para pre-rellenar."""
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
