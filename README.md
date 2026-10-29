# spotify-migrate

Copia playlists propias, playlists seguidas, canciones guardadas y álbumes de una cuenta
de Spotify a otra.

## 1. Crear la app en Spotify

1. Entra en https://developer.spotify.com/dashboard con **cualquiera** de las dos cuentas.
2. *Create app*. Nombre y descripción, lo que quieras.
3. En **Redirect URIs** añade exactamente: `http://127.0.0.1:8080/callback`
4. En *Which API/SDKs* marca **Web API**. Guarda.
5. En *Settings* copia el **Client ID** y el **Client secret**.
6. En *Settings > User Management* añade el email de **las dos cuentas** (la app nace en
   modo desarrollo y solo deja entrar a usuarios dados de alta).

## 2. Configurar

```bash
cd /Users/jmrosal/dev/spotify-migrate
cp .env.example .env   # y rellena client id y secret
```

## 3. Ejecutar

```bash
source .venv/bin/activate
python migrate.py
```

El script hace dos logins seguidos:

1. **Origen** (cuenta vieja) → lee todo y lo vuelca en `export.json`.
2. **Destino** (cuenta nueva) → crea las playlists y guarda canciones/álbumes.

> Entre los dos logins el navegador seguirá con la sesión de la cuenta vieja. En la
> pantalla de Spotify usa *"No eres tú?" / cambiar de cuenta*, o abre el enlace en una
> ventana de incógnito. El script aborta si detecta que ambas cuentas son la misma.

También puedes hacerlo por pasos:

```bash
python migrate.py export   # solo lee la cuenta vieja
python migrate.py import   # solo escribe en la nueva (usa export.json)
```

## Notas

- Es idempotente a nivel de playlist: si ya existe una con el mismo nombre en el destino,
  la salta. Puedes relanzar `import` si algo falla a medias.
- Los ficheros **locales** de las playlists no se copian (no existen en la nube).
- El orden de "Me gusta" se conserva añadiendo las canciones en orden inverso.
- Los tokens se guardan en `.cache/`. Borra esa carpeta para forzar un login limpio.
