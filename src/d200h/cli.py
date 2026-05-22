"""CLI entry-point para `d200h` — versión HID nativa."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from . import __version__


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# list / validate / compile
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    from . import pages
    files = pages.list_page_files()
    print("Pages:")
    for p in files:
        print(f"  - {p.stem}  ({p})")
    if not files:
        print("  (ninguna)")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from . import pages
    errors: list[str] = []
    for path in pages.list_page_files():
        try:
            page = pages.load_page(path)
            # compile_page hace toda la validación pesada (icon resolution, etc.)
            pages.compile_page(page)
        except (pages.PageError, FileNotFoundError) as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    n = len(pages.list_page_files())
    print(f"OK: {n} páginas")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    """Compila páginas a ZIPs y, si --out, los guarda en disco."""
    from . import deploy
    try:
        if args.page:
            data = deploy.compile_page(args.page)
            blobs = {args.page: data}
        else:
            blobs = deploy.compile_all()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1

    if args.out:
        from pathlib import Path
        out_dir = Path(args.out).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        for pid, data in blobs.items():
            f = out_dir / f"{pid}.zip"
            f.write_bytes(data)
            print(f"  {pid:>20s}  → {f}  ({len(data)} B)")
    else:
        for pid, data in blobs.items():
            print(f"  {pid:>20s}  {len(data):8d} B")
    return 0


# ---------------------------------------------------------------------------
# bridge
# ---------------------------------------------------------------------------

def _cmd_bridge(args: argparse.Namespace) -> int:
    from . import bridge
    return bridge.run(home=args.home)


# ---------------------------------------------------------------------------
# status / utilidades device (ADB)
# ---------------------------------------------------------------------------

def _cmd_status(args: argparse.Namespace) -> int:
    from . import adb, hid
    devs = adb.devices()
    if devs:
        for d in devs:
            print(f"ADB:    {d.serial}  state={d.state}")
    else:
        print("ADB:    (sin dispositivos)")

    path = hid.find_hidraw()
    if path is None:
        print("HID:    (no encontrado — ¿conectado? ¿udev?)")
        return 1
    print(f"HID:    {path}")
    return 0


# ---------------------------------------------------------------------------
# convert (.ulanziDeckProfile → YAML del bridge)
# ---------------------------------------------------------------------------

def _cmd_convert(args: argparse.Namespace) -> int:
    from pathlib import Path
    from . import config, convert
    inputs = [Path(p).expanduser() for p in args.inputs]
    out_pages = (Path(args.out).expanduser() if args.out
                 else config.pages_dir() / "user")
    out_icons = (Path(args.icons_out).expanduser() if args.icons_out
                 else config.icons_dir())
    try:
        report = convert.convert(
            inputs, out_pages, out_icons,
            default_profile=args.default_profile,
            dry_run=args.dry_run,
            keep_existing=args.keep_existing,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    print(f"\n{report.summary()}")
    if not args.dry_run and report.written:
        print(f"Escritos {len(report.written)} archivos en {out_pages}")
    if report.spotify_secrets_written:
        print(f"\nCredenciales Spotify pre-rellenadas en "
              f"{report.spotify_secrets_written}.")
        print("Para completar la autorización corre:")
        print("  uv run d200h spotify-auth")
    return 0


# ---------------------------------------------------------------------------
# install / uninstall (systemd user service)
# ---------------------------------------------------------------------------

def _cmd_install(args: argparse.Namespace) -> int:
    from pathlib import Path
    from . import service
    project_dir = (Path(args.project_dir).expanduser().resolve()
                   if args.project_dir else None)
    # --linger sólo tiene sentido si además arrancamos: lo implica.
    if args.linger and not args.now:
        args.now = True
        print("(--linger implica --now)")
    try:
        target = service.install(
            project_dir=project_dir,
            exec_start=args.exec_start,
            force=args.force,
        )
    except (FileExistsError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    print(f"Escrito: {target}")

    # Sin --now: primitiva pura, sólo se escribió la unit. Imprime el resto.
    if not args.now:
        print()
        print("Próximos pasos (o re-ejecuta con --now para hacerlos por ti):")
        print("  systemctl --user daemon-reload")
        print("  systemctl --user enable --now d200h.service")
        print("  loginctl enable-linger $USER   # arranca sin login gráfico")
        print()
        print("Verificar:   systemctl --user status d200h.service")
        print("Desinstalar: d200h uninstall --now")
        return 0

    # --now: daemon-reload + enable --now. Sin sudo, sin matar procesos.
    try:
        service.daemon_reload()
        service.enable_now()
    except service.SystemctlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    print("Habilitado y arrancado (systemctl --user enable --now).")

    if args.linger:
        try:
            ok = service.enable_linger()
        except service.SystemctlError as exc:
            print(f"ERROR: {exc}", file=sys.stderr); return 1
        if ok:
            print("linger activo: arrancará al encender el equipo (sin login).")
        else:
            print("! No pude activar linger sin privilegios. Córrelo a mano:")
            print(f"    sudo loginctl enable-linger {service.current_user()}")

    print()
    print("Verificar: systemctl --user status d200h.service")
    print("Logs:      journalctl --user -u d200h.service -f")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from . import service
    # --now: para y deshabilita ANTES de borrar la unit (disable limpia el
    # symlink y para el servicio mientras la unit todavía existe).
    if args.now:
        try:
            service.disable_now()
        except service.SystemctlError as exc:
            print(f"WARN: {exc}", file=sys.stderr)
    removed = service.uninstall()
    if removed:
        print(f"Eliminado: {service.user_unit_path()}")
    else:
        print("No había unit file instalado.")
    if args.now:
        try:
            service.daemon_reload()
        except service.SystemctlError as exc:
            print(f"WARN: {exc}", file=sys.stderr)
        print("Parado y deshabilitado.")
    else:
        print("Recuerda: systemctl --user daemon-reload")
    # linger NO es nuestro: puede usarlo otro servicio. No lo tocamos nunca.
    print("(linger se deja como estaba; para quitarlo: "
          "loginctl disable-linger $USER)")
    return 0


# ---------------------------------------------------------------------------
# spotify-auth / spotify-status
# ---------------------------------------------------------------------------

def _cmd_spotify_auth(args: argparse.Namespace) -> int:
    """Flujo OAuth2 PKCE para Spotify. Escribe config/secrets/spotify.yaml."""
    from . import config, spotify_auth
    path = config.spotify_credentials_path()
    existing = spotify_auth.load_existing(path)

    client_id = args.client_id or existing.get("client_id") or ""
    client_secret = args.client_secret or existing.get("client_secret") or ""

    if not client_id or not client_secret:
        print("ERROR: faltan client_id/client_secret.", file=sys.stderr)
        print(
            "Opciones:\n"
            "  1) Pasa --client-id/--client-secret.\n"
            "  2) Pre-rellena config/secrets/spotify.yaml con esos campos "
            "(p.ej. lo hace `d200h convert` al ver credenciales en un "
            ".ulanziDeckProfile) y vuelve a correr este comando.",
            file=sys.stderr,
        )
        return 2

    if existing.get("refresh_token") and not args.force:
        print(f"Ya hay refresh_token en {path}. Usa --force para sobrescribir.",
              file=sys.stderr)
        return 2

    try:
        payload = spotify_auth.run_auth_flow(
            client_id=client_id,
            client_secret=client_secret,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    spotify_auth.save_credentials(
        path,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=payload.get("refresh_token", ""),
        scope=payload.get("scope", spotify_auth.SCOPES),
        obtained_at=payload["obtained_at"],
    )
    print(f"OK — credenciales escritas en {path}")
    print("Asegúrate de que Spotify esté abierto en algún reproductor antes "
          "de pulsar slots Spotify.")
    return 0


def _cmd_spotify_status(args: argparse.Namespace) -> int:
    """Muestra el estado actual del cliente (token + devices + track)."""
    from . import config, spotify as _spotify
    if _spotify.env_disabled():
        print("Spotify: deshabilitado por D200H_SPOTIFY env")
        return 0
    path = config.spotify_credentials_path()
    client = _spotify.SpotifyClient.from_config(path)
    if client is None:
        print(f"Spotify: no hay credenciales válidas en {path}.")
        print("Corre: uv run d200h spotify-auth")
        return 1
    info = client.describe()
    for k, v in info.items():
        print(f"  {k:22s} {v}")
    return 0 if "error" not in info else 1


# ---------------------------------------------------------------------------
# icon-gen (generador declarativo de iconos)
# ---------------------------------------------------------------------------

def _cmd_icon_gen(args: argparse.Namespace) -> int:
    """Tester standalone + garbage collector del cache `__generated__/`."""
    from pathlib import Path
    from . import config, icon_gen, pages

    if args.gc:
        used: set[str] = set()
        for path in pages.list_page_files():
            try:
                page = pages.load_page(path)
            except pages.PageError as exc:
                print(f"WARN: no pude cargar {path}: {exc}", file=sys.stderr)
                continue
            for slot in page.slots.values():
                if slot.icon_generate:
                    used.add(icon_gen.spec_hash(slot.icon_generate))
        kept, deleted = icon_gen.gc(config.icons_dir(), used_hashes=used)
        print(f"icon_gen gc: {kept} cacheados activos, {deleted} borrados")
        return 0

    if not args.text:
        print("ERROR: --text es obligatorio (o usa --gc).", file=sys.stderr)
        return 2

    png = icon_gen.render(text=args.text, color=args.color, fg=args.fg)
    out = Path(args.out).expanduser() if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(f"Escrito: {out} ({len(png)} B)")
    else:
        # Sin --out: guarda en el cache canónico (mismo path que usaría
        # un slot YAML con esa misma spec) — útil para iterar diseño.
        spec = {"text": args.text, "color": args.color, "fg": args.fg}
        cached = icon_gen.ensure_cached(config.icons_dir(), spec)
        print(f"Cacheado: {cached} ({len(png)} B)")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="d200h",
                                description="CLI para Ulanzi D200H (Linux, HID nativo).")
    p.add_argument("--version", action="version", version=f"d200h {__version__}")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Logging DEBUG")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="Lista page_*.yaml")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("validate", help="Valida y compila las páginas en seco")
    sp.set_defaults(func=_cmd_validate)

    sp = sub.add_parser("compile", help="Compila las páginas a ZIPs HID")
    sp.add_argument("--page", help="Sólo esta página (page_id)")
    sp.add_argument("--out", metavar="DIR",
                    help="Si se da, guarda los ZIPs en DIR/<page_id>.zip")
    sp.set_defaults(func=_cmd_compile)

    sp = sub.add_parser("bridge",
                        help="Conecta por HID, envía ZIPs y escucha pulsaciones.")
    sp.add_argument("--home", help="page_id inicial (default: home / page_0 / primera)")
    sp.set_defaults(func=_cmd_bridge)

    sp = sub.add_parser("status", help="Estado del device (ADB + HID)")
    sp.set_defaults(func=_cmd_status)

    sp = sub.add_parser("convert",
                        help="Traduce .ulanziDeckProfile (export del software "
                             "oficial Windows) a YAML del bridge.")
    sp.add_argument("inputs", nargs="+",
                    help="Uno o más archivos .ulanziDeckProfile, o un directorio "
                         "que los contenga.")
    sp.add_argument("--out", metavar="DIR",
                    help="Directorio destino para los page_*.yaml "
                         "(default: config/pages/user/).")
    sp.add_argument("--icons-out", metavar="DIR", dest="icons_out",
                    help="Directorio destino para los iconos copiados "
                         "(default: config/icons/).")
    sp.add_argument("--default-profile", metavar="NAME", dest="default_profile",
                    help="Nombre del profile que se renombra a `home` (entrada "
                         "del bridge). Auto-detecta 'Default Profile' si se omite.")
    sp.add_argument("--dry-run", action="store_true",
                    help="No escribe nada; sólo reporta el plan de conversión.")
    sp.add_argument("--keep-existing", action="store_true",
                    help="Respeta los YAML destino que ya existen (no sobrescribe).")
    sp.set_defaults(func=_cmd_convert)

    sp = sub.add_parser("install",
                        help="Instala el bridge como systemd user service. "
                             "Sin flags sólo escribe la unit; con --now la "
                             "habilita y arranca.")
    sp.add_argument("--project-dir", metavar="DIR", dest="project_dir",
                    help="Raíz del repo (default: cwd).")
    sp.add_argument("--exec-start", metavar="CMD", dest="exec_start",
                    help="Comando completo del ExecStart "
                         "(default: `uv run d200h bridge`).")
    sp.add_argument("--force", action="store_true",
                    help="Sobrescribe la unit file existente.")
    sp.add_argument("--now", action="store_true",
                    help="Tras escribir la unit: daemon-reload + "
                         "enable --now (sin sudo, sin matar procesos).")
    sp.add_argument("--linger", action="store_true",
                    help="Activa linger para arrancar sin login gráfico "
                         "(implica --now). Best-effort sin sudo: si no "
                         "puede, imprime el comando con sudo.")
    sp.set_defaults(func=_cmd_install)

    sp = sub.add_parser("uninstall",
                        help="Elimina el systemd user service.")
    sp.add_argument("--now", action="store_true",
                    help="Para y deshabilita el servicio (disable --now + "
                         "daemon-reload) antes de borrar la unit.")
    sp.set_defaults(func=_cmd_uninstall)

    sp = sub.add_parser("spotify-auth",
                        help="Autoriza el bridge contra Spotify (OAuth2 PKCE). "
                             "Escribe config/secrets/spotify.yaml.")
    sp.add_argument("--client-id", dest="client_id",
                    help="Client ID de la app Spotify (si no, lee de "
                         "config/secrets/spotify.yaml).")
    sp.add_argument("--client-secret", dest="client_secret",
                    help="Client Secret (si no, lee de "
                         "config/secrets/spotify.yaml).")
    sp.add_argument("--port", type=int, default=30901,
                    help="Puerto local del callback (default 30901, igual que "
                         "el software oficial de Ulanzi en Windows).")
    sp.add_argument("--no-browser", action="store_true",
                    help="No intentes abrir el navegador automáticamente "
                         "(imprime la URL y espera).")
    sp.add_argument("--force", action="store_true",
                    help="Re-autoriza aunque ya haya refresh_token.")
    sp.set_defaults(func=_cmd_spotify_auth)

    sp = sub.add_parser("spotify-status",
                        help="Muestra estado de Spotify (token, devices, track).")
    sp.set_defaults(func=_cmd_spotify_status)

    sp = sub.add_parser("icon-gen",
                        help="Genera/cachea un icono con marco + texto. "
                             "Útil para iterar diseño o limpiar el cache.")
    sp.add_argument("--text", help="Texto a renderizar (obligatorio salvo --gc).")
    sp.add_argument("--color", default="#1a4f8a",
                    help="Color de fondo (default #1a4f8a).")
    sp.add_argument("--fg", default="#ffffff",
                    help="Color del texto (default #ffffff).")
    sp.add_argument("--out", help="Ruta de salida del PNG (default: cache "
                                  "config/icons/__generated__/<hash>.png).")
    sp.add_argument("--gc", action="store_true",
                    help="Borra PNGs huérfanos del cache __generated__/.")
    sp.set_defaults(func=_cmd_icon_gen)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    verbose = getattr(args, "verbose", False) or args.command == "bridge"
    _setup_logging(verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
