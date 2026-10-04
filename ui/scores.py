"""Cliente de la API pública de scoreboard de ESPN para marcadores en vivo.

No es una API oficial documentada, pero es de acceso público y estable.
Se cachea la respuesta unos segundos para no golpearla en cada tarjeta.

Uso:
    sb = ScoreBoard(sport="nfl")
    info = sb.lookup("DAL", "NYG")   # -> ScoreInfo o None
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

import requests

from scraper.dns_fallback import make_session

log = logging.getLogger(__name__)

# Mapa deporte -> ruta ESPN (football/nfl, basketball/nba, etc.).
_SPORT_PATHS: dict[str, str] = {
    "nfl": "football/nfl",
    "nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "nhl": "hockey/nhl",
}

_BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
_CACHE_TTL = 30  # segundos


@dataclass
class ScoreInfo:
    """Marcador y estado de un partido, orientado a los equipos consultados."""

    score_a: int
    score_b: int
    state: str          # "pre" | "in" | "post"
    detail: str         # p. ej. "9:13 - 3rd", "Final"
    date: str = ""      # ISO 8601 UTC del inicio del partido (de ESPN), o ""

    @property
    def is_live(self) -> bool:
        return self.state == "in"

    @property
    def is_today(self) -> bool:
        """True si el partido es hoy (en horario local del dispositivo)."""
        game_date = _parse_iso(self.date)
        if game_date is None:
            return False
        return game_date.astimezone().date() == dt.datetime.now().date()


class ScoreBoard:
    """Consulta y cachea el scoreboard de un deporte; permite lookup por equipos."""

    def __init__(self, sport: str = "nfl", timeout: int = 12) -> None:
        self.sport = sport.lower()
        self.timeout = timeout
        self._cache: list[dict] | None = None
        self._cache_ts: float = 0.0
        self._session = make_session()

    def _url(self) -> str | None:
        path = _SPORT_PATHS.get(self.sport)
        return _BASE.format(path=path) if path else None

    def _fetch(self) -> list[dict]:
        """Descarga (o devuelve de caché) la lista de partidos normalizada."""
        now = time.time()
        if self._cache is not None and (now - self._cache_ts) < _CACHE_TTL:
            return self._cache

        url = self._url()
        if not url:
            log.debug("Deporte sin endpoint de marcador: %s", self.sport)
            self._cache, self._cache_ts = [], now
            return self._cache

        try:
            r = self._session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("No se pudo obtener el marcador de ESPN (%s): %s", self.sport, exc)
            # Conservar la caché anterior si existe; si no, lista vacía.
            if self._cache is not None:
                return self._cache
            self._cache, self._cache_ts = [], now
            return self._cache

        games: list[dict] = []
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            status = (ev.get("status") or {}).get("type", {})
            competitors = comp.get("competitors", [])
            teams = {}
            for c in competitors:
                abbr = (c.get("team") or {}).get("abbreviation")
                if abbr:
                    teams[abbr.upper()] = _to_int(c.get("score"))
            games.append({
                "teams": teams,
                "state": status.get("state", ""),
                "detail": status.get("shortDetail", ""),
                "date": ev.get("date", ""),
            })

        self._cache, self._cache_ts = games, now
        return games

    def lookup(self, abbr_a: str, abbr_b: str) -> ScoreInfo | None:
        """Busca el partido que contenga ambas abreviaturas; devuelve ScoreInfo."""
        a, b = abbr_a.upper(), abbr_b.upper()
        for game in self._fetch():
            teams = game["teams"]
            if a in teams and b in teams:
                return ScoreInfo(
                    score_a=teams[a],
                    score_b=teams[b],
                    state=game["state"],
                    detail=game["detail"],
                    date=game.get("date", ""),
                )
        return None


def _parse_iso(value: str) -> dt.datetime | None:
    """Parsea una fecha ISO 8601 de ESPN (p. ej. '2026-09-14T17:00Z')."""
    if not value:
        return None
    try:
        # ESPN usa sufijo 'Z'; datetime.fromisoformat lo admite desde 3.11,
        # pero normalizamos por compatibilidad.
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
