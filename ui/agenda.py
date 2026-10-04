"""Agenda de partidos del día desde la API pública de scoreboard de ESPN.

A diferencia de scores.py (que hace lookup de marcador por equipos), este módulo
devuelve la LISTA de partidos de un día concreto para poblar el tablero: cada
entrada trae los dos equipos, la hora de inicio, el estado y si está dentro de
la ventana en la que ya se puede intentar reproducir.

La API acepta el parámetro ?dates=YYYYMMDD para pedir un día concreto, así que
"hoy" se recalcula en cada consulta y a medianoche pasa al día siguiente solo.

Uso:
    ag = Agenda(sport="nfl")
    partidos = ag.today()   # -> list[AgendaGame]
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
_CACHE_TTL = 60  # segundos: la agenda cambia poco dentro de la misma hora.

# Minutos antes del inicio a partir de los cuales se permite intentar reproducir.
PLAYABLE_LEAD_MINUTES = 15


@dataclass
class AgendaGame:
    """Un partido del día, con lo necesario para dibujar la tarjeta y decidir el clic."""

    home_name: str          # nombre completo del equipo local (p. ej. "Dallas Cowboys")
    away_name: str          # nombre completo del visitante
    home_abbr: str          # abreviatura (p. ej. "DAL")
    away_abbr: str
    start: dt.datetime | None  # inicio en UTC (aware), o None si desconocido
    state: str              # "pre" | "in" | "post"
    detail: str             # p. ej. "8:00 PM ET", "Q3 - 4:21", "Final"
    score_home: int = 0
    score_away: int = 0

    @property
    def is_live(self) -> bool:
        return self.state == "in"

    @property
    def is_final(self) -> bool:
        return self.state == "post"

    @property
    def title(self) -> str:
        """Título estilo 'Away vs Home' (formato habitual de StreamEast)."""
        return f"{self.away_name} vs {self.home_name}"

    def is_playable(self, now: dt.datetime | None = None) -> bool:
        """True si ya se puede intentar reproducir.

        Ventana: desde PLAYABLE_LEAD_MINUTES antes del inicio hasta que el
        partido esté finalizado (estado 'post'). Si está en vivo, siempre True.
        """
        if self.is_final:
            return False
        if self.is_live:
            return True
        if self.start is None:
            # Sin hora fiable: permitir solo si ESPN ya lo marca 'in' (ya cubierto).
            return False
        now = now or dt.datetime.now(dt.timezone.utc)
        lead = dt.timedelta(minutes=PLAYABLE_LEAD_MINUTES)
        return now >= (self.start - lead)

    def status_text(self, now: dt.datetime | None = None) -> str:
        """Texto de estado para la tarjeta."""
        if self.is_live:
            return self.detail or "EN VIVO"
        if self.is_final:
            return self.detail or "Final"
        # Pre-partido: mostrar la hora local de inicio.
        if self.start is not None:
            local = self.start.astimezone()
            return local.strftime("%H:%M")
        return self.detail or "Programado"


class Agenda:
    """Consulta y cachea la agenda del día para un deporte."""

    def __init__(self, sport: str = "nfl", timeout: int = 12) -> None:
        self.sport = sport.lower()
        self.timeout = timeout
        self._cache: list[AgendaGame] | None = None
        self._cache_ts: float = 0.0
        self._cache_day: str = ""
        self._session = make_session()

    def _url(self, day: str) -> str | None:
        path = _SPORT_PATHS.get(self.sport)
        if not path:
            return None
        return f"{_BASE.format(path=path)}?dates={day}"

    def today(self) -> list[AgendaGame]:
        """Devuelve los partidos de HOY (fecha local del dispositivo)."""
        day = dt.datetime.now().strftime("%Y%m%d")
        return self._fetch(day)

    def _fetch(self, day: str) -> list[AgendaGame]:
        now = time.time()
        fresh = (
            self._cache is not None
            and self._cache_day == day
            and (now - self._cache_ts) < _CACHE_TTL
        )
        if fresh:
            return self._cache  # type: ignore[return-value]

        url = self._url(day)
        if not url:
            log.debug("Deporte sin endpoint de agenda: %s", self.sport)
            self._cache, self._cache_ts, self._cache_day = [], now, day
            return self._cache

        try:
            r = self._session.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("No se pudo obtener la agenda de ESPN (%s): %s", self.sport, exc)
            if self._cache is not None and self._cache_day == day:
                return self._cache
            self._cache, self._cache_ts, self._cache_day = [], now, day
            return self._cache

        games: list[AgendaGame] = []
        for ev in data.get("events", []):
            game = _parse_event(ev)
            if game is not None:
                games.append(game)

        games.sort(key=lambda g: (g.start or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
        self._cache, self._cache_ts, self._cache_day = games, now, day
        return games


def _parse_event(ev: dict) -> AgendaGame | None:
    """Convierte un evento del scoreboard de ESPN en AgendaGame (o None)."""
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None

    home = away = None
    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        elif c.get("homeAway") == "away":
            away = c
    # Fallback si ESPN no marca homeAway: primero=home por convención de la API.
    if home is None or away is None:
        home, away = competitors[0], competitors[1]

    def team_name(c: dict) -> str:
        t = c.get("team") or {}
        return t.get("displayName") or t.get("name") or t.get("abbreviation") or "?"

    def team_abbr(c: dict) -> str:
        t = c.get("team") or {}
        return (t.get("abbreviation") or "").upper()

    status = (ev.get("status") or {}).get("type", {})
    return AgendaGame(
        home_name=team_name(home),
        away_name=team_name(away),
        home_abbr=team_abbr(home),
        away_abbr=team_abbr(away),
        start=_parse_iso(ev.get("date", "")),
        state=status.get("state", ""),
        detail=status.get("shortDetail", ""),
        score_home=_to_int(home.get("score")),
        score_away=_to_int(away.get("score")),
    )


def _parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
