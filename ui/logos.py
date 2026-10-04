"""Descarga y caché en disco de logos oficiales de equipos (desde ESPN).

Los logos se identifican por abreviatura de equipo (p. ej. 'DAL') y deporte.
Se cachean en data/logos/<sport>/<abbr>.png para no volver a descargarlos.

Nota: los logos son marcas registradas de sus equipos/ligas; uso doméstico.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "logos"

# Patrón de logo de ESPN por deporte (abreviatura en minúsculas).
_ESPN_LOGO = "https://a.espncdn.com/i/teamlogos/{league}/500/scoreboard/{abbr}.png"
_LEAGUE_BY_SPORT = {"nfl": "nfl", "nba": "nba", "mlb": "mlb", "nhl": "nhl"}

# Evita descargas duplicadas concurrentes del mismo logo.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
# Abreviaturas que ya fallaron, para no reintentar en bucle durante la sesión.
_failed: set[str] = set()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


def logo_path(abbr: str, sport: str = "nfl", timeout: int = 12) -> str | None:
    """Devuelve la ruta local del logo del equipo, descargándolo si hace falta.

    Devuelve None si el deporte no tiene logos ESPN o si la descarga falla.
    """
    league = _LEAGUE_BY_SPORT.get(sport.lower())
    if not league or not abbr:
        return None

    key = f"{league}/{abbr.lower()}"
    if key in _failed:
        return None

    dest = CACHE_DIR / league / f"{abbr.lower()}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)

    lock = _lock_for(key)
    with lock:
        # Re-comprobar dentro del lock (otro hilo pudo descargarlo).
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        url = _ESPN_LOGO.format(league=league, abbr=abbr.lower())
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "")
            if "image" not in ctype or not r.content:
                raise ValueError(f"respuesta no es imagen: {ctype}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Escritura atómica.
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            return str(dest)
        except (requests.RequestException, ValueError, OSError) as exc:
            log.warning("No se pudo descargar el logo %s: %s", key, exc)
            _failed.add(key)
            return None
