#!/usr/bin/env python3
"""Migra playlists, canciones guardadas y álbumes de una cuenta de Spotify a otra."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import spotipy
from dotenv import load_dotenv
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

HERE = Path(__file__).parent
DUMP_PATH = HERE / "export.json"
CACHE_DIR = HERE / ".cache"

READ_SCOPES = (
    "playlist-read-private playlist-read-collaborative "
    "user-library-read user-follow-read"
)
# incluye lectura de playlists porque el import comprueba cuáles existen ya en destino
WRITE_SCOPES = (
    "playlist-modify-public playlist-modify-private "
    "user-library-modify user-follow-modify "
    "playlist-read-private playlist-read-collaborative"
)

FORBIDDEN_HINT = """
ERROR: Spotify responde 403 Forbidden. El token tiene los permisos correctos
(si faltara un scope el mensaje sería "Insufficient client scope"), así que la
cuenta de destino no puede escribir en su propia biblioteca. Comprueba:

  1. Que la cuenta esté dada de alta en Dashboard > tu app > Settings >
     User Management (modo desarrollo), con su email exacto.
  2. Que el dueño de la app tenga Spotify Premium.
  3. Que la cuenta pueda crear una playlist A MANO desde la app de Spotify.
     Si tampoco puede, la cuenta está restringida por Spotify (típico cuando
     el país de registro no coincide con el de uso) y no es problema del script.

export.json no se toca: arregla la cuenta y relanza el import, es idempotente.
"""


def client(account: str, scopes: str) -> spotipy.Spotify:
    """Autentica una cuenta. `account` solo sirve para separar los tokens en disco."""
    CACHE_DIR.mkdir(exist_ok=True)
    auth = SpotifyOAuth(
        scope=scopes,
        cache_handler=CacheFileHandler(cache_path=str(CACHE_DIR / f"token-{account}")),
        show_dialog=True,  # fuerza la pantalla de selección de cuenta
        open_browser=True,
    )
    sp = spotipy.Spotify(auth_manager=auth, retries=5, status_retries=5)
    me = sp.current_user()
    print(f"  conectado como: {me['display_name'] or me['id']} ({me['id']})")
    return sp


def paginate(sp: spotipy.Spotify, page: dict[str, Any]) -> Iterator[dict[str, Any]]:
    while page:
        yield from page["items"]
        page = sp.next(page) if page.get("next") else None


def chunked(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# spotipy 2.26 todavía llama a los endpoints de escritura antiguos, que Spotify ha
# retirado o deprecado y responden 403. Usamos los nuevos con sus métodos internos
# (`_post`/`_put`) para conservar la gestión de token y los reintentos.


def create_playlist(sp: spotipy.Spotify, pl: dict[str, Any]) -> str:
    new = sp._post(
        "me/playlists",
        payload={
            "name": pl["name"],
            "public": pl["public"] and not pl["collaborative"],
            "collaborative": pl["collaborative"],
            "description": pl["description"],
        },
    )
    return new["id"]


def add_to_playlist(sp: spotipy.Spotify, playlist_id: str, uris: list[str]) -> None:
    for batch in chunked(uris, 100):
        sp._post(f"playlists/{playlist_id}/items", payload={"uris": batch})
        time.sleep(0.1)


def save_to_library(sp: spotipy.Spotify, uris: list[str]) -> None:
    """Sustituye a /me/tracks y /me/albums. Acepta cualquier tipo de URI, máximo 40."""
    for batch in chunked(uris, 40):
        sp._put("me/library", uris=",".join(batch))
        time.sleep(0.1)



# --------------------------------------------------------------------------- export


def export() -> dict[str, Any]:
    print("Cuenta ORIGEN (la vieja):")
    sp = client("origen", READ_SCOPES)
    me = sp.current_user()["id"]

    owned: list[dict[str, Any]] = []
    followed: list[dict[str, Any]] = []

    print("\nLeyendo playlists...")
    for pl in paginate(sp, sp.current_user_playlists(limit=50)):
        if not pl:
            continue
        if pl["owner"]["id"] != me:
            followed.append({"id": pl["id"], "name": pl["name"]})
            print(f"  [sigo]  {pl['name']}")
            continue

        uris = []
        for entry in paginate(sp, sp.playlist_items(pl["id"], limit=100)):
            # la API devuelve la pista en "item"; "track" es el nombre antiguo del campo
            track = entry.get("item") or entry.get("track")
            if not track or not track.get("uri"):
                continue
            # los ficheros locales no existen en la nube, no se pueden copiar
            if entry.get("is_local") or track["uri"].startswith("spotify:local:"):
                continue
            uris.append(track["uri"])

        owned.append(
            {
                "name": pl["name"],
                "description": html.unescape(pl.get("description") or ""),
                "public": bool(pl.get("public")),
                "collaborative": bool(pl.get("collaborative")),
                "uris": uris,
            }
        )
        print(f"  [mía]   {pl['name']} ({len(uris)} temas)")

    print("\nLeyendo canciones guardadas...")
    saved_tracks = [
        it["track"]["uri"]
        for it in paginate(sp, sp.current_user_saved_tracks(limit=50))
        if it.get("track") and it["track"].get("uri")
    ]
    print(f"  {len(saved_tracks)} canciones")

    print("Leyendo álbumes guardados...")
    saved_albums = [
        it["album"]["id"]
        for it in paginate(sp, sp.current_user_saved_albums(limit=50))
        if it.get("album")
    ]
    print(f"  {len(saved_albums)} álbumes")

    data = {
        "source_user": me,
        "owned_playlists": owned,
        "followed_playlists": followed,
        "saved_tracks": saved_tracks,
        "saved_albums": saved_albums,
    }
    DUMP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nExportado a {DUMP_PATH}")
    return data


# --------------------------------------------------------------------------- import


def import_(data: dict[str, Any]) -> None:
    print("\nCuenta DESTINO (la nueva):")
    print("  Ojo: si el navegador sigue logueado con la cuenta vieja, cambia de cuenta")
    print("  en la pantalla de login o usa una ventana de incógnito.")
    input("  Pulsa Enter para continuar... ")
    sp = client("destino", WRITE_SCOPES)
    me = sp.current_user()["id"]

    if me == data.get("source_user"):
        sys.exit("ERROR: te has autenticado con la MISMA cuenta de origen. Aborto.")

    existing = {pl["name"] for pl in paginate(sp, sp.current_user_playlists(limit=50)) if pl}

    print("\nCreando playlists...")
    for pl in data["owned_playlists"]:
        if pl["name"] in existing:
            print(f"  [salto] {pl['name']} (ya existe)")
            continue
        add_to_playlist(sp, create_playlist(sp, pl), pl["uris"])
        print(f"  [ok]    {pl['name']} ({len(pl['uris'])} temas)")

    print("\nSiguiendo playlists de otros...")
    for pl in data["followed_playlists"]:
        try:
            sp.current_user_follow_playlist(pl["id"])
            print(f"  [ok]    {pl['name']}")
        except spotipy.SpotifyException as exc:
            print(f"  [error] {pl['name']}: {exc.msg}")

    # se añaden en orden inverso para conservar el orden cronológico original
    print("\nGuardando canciones...")
    save_to_library(sp, list(reversed(data["saved_tracks"])))
    print(f"  {len(data['saved_tracks'])} canciones")

    print("Guardando álbumes...")
    save_to_library(sp, [f"spotify:album:{a}" for a in reversed(data["saved_albums"])])
    print(f"  {len(data['saved_albums'])} álbumes")

    print("\nListo.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "step",
        nargs="?",
        default="all",
        choices=["export", "import", "all"],
        help="export: solo leer la cuenta vieja. import: solo escribir en la nueva.",
    )
    args = parser.parse_args()

    load_dotenv(HERE / ".env")
    for var in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.getenv(var):
            sys.exit(f"Falta la variable {var}. Revisa el README y crea el fichero .env")

    try:
        if args.step == "export":
            export()
        elif args.step == "import":
            if not DUMP_PATH.exists():
                sys.exit(f"No existe {DUMP_PATH}. Ejecuta primero: python migrate.py export")
            import_(json.loads(DUMP_PATH.read_text()))
        else:
            import_(export())
    except spotipy.SpotifyException as exc:
        if exc.http_status == 403:
            sys.exit(FORBIDDEN_HINT)
        raise


if __name__ == "__main__":
    main()
