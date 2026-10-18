"""Cliente de fixtures/horarios de NFL desde nflverse (CSV).

Encapsula el acceso al CSV público de nflverse (``nfldata/data/games.csv``),
igual que ``PluginClient`` encapsula el protocolo Stremio. El plugin sigue
siendo la fuente de la lista de partidos y la autoridad del estado live; esta
clase solo **enriquece** con nombres de equipo y horarios fiables (Requisito 11).

Este módulo contiene (Tarea 5.1) el constructor, la descarga del CSV con
``requests``, el cacheado local semanal en ``data/`` y el acceso a las filas
crudas (``csv.DictReader``); y (Tarea 5.2) el modelo :class:`ScheduleGame` y la
jornada en curso (``gameweek_games``): ventana martes→lunes que contiene hoy en
la zona del kiosk y ``start_time`` derivado de ``gameday`` + ``gametime`` (ET →
kiosk); y (Tarea 5.3) el enriquecimiento de eventos del plugin (``enrich``):
empareja un evento con un :class:`ScheduleGame` por código/nombre de equipo
(reutilizando los términos de ``ui/teams.py``) y rellena ``start_time`` y los
nombres de equipo cuando le faltan al plugin, sin tocar los campos ya presentes
y sin usar scores para estado live (Requisitos 11.2 y 11.4).

Diseñado para degradar en lugar de fallar: si la descarga del CSV falla (red,
404) y no hay caché utilizable, ``fetch_rows()`` devuelve ``[]`` y registra el
fallo en el log (Requisito 11.6).
"""

from __future__ import annotations

import csv
import io
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time as _dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# Reutilizamos los helpers ya existentes en plugin_client en lugar de
# duplicarlos: el modelo Stream del plugin ya vive ahí, así que importar la
# lógica de ventana de jornada (martes→lunes) y de carga de zona horaria desde
# el mismo módulo mantiene una única fuente de verdad (DRY).
from scraper.plugin_client import _gameweek_window, _load_zone

# Reutilizamos el parser de enfrentamientos y los helpers de equipo de la capa
# UI para el emparejamiento (Tarea 5.3). ``ui/teams.py`` sólo importa ``re`` (no
# Kivy ni dependencias pesadas), así que es seguro importarlo desde la capa de
# scraping sin arrastrar la UI.
from ui.teams import parse_matchup, slug_terms, team_badge

log = logging.getLogger(__name__)

# Zona horaria de los horarios de nflverse: el CSV trae ``gametime`` sin zona
# explícita, pero nflverse publica las horas en ET (Eastern Time). Convertimos
# ET → zona del kiosk antes de exponer ``start_time`` (Requisito 11.3).
_ET_ZONE = ZoneInfo("America/New_York")

# Formato de ``gameday`` (fecha ISO, "YYYY-MM-DD") y ``gametime`` (hora 24h,
# "HH:MM") en el CSV de nflverse.
_GAMEDAY_FMT = "%Y-%m-%d"
_GAMETIME_FMT = "%H:%M"

# Directorio donde se guarda la caché del CSV, relativo a la raíz del repositorio
# (scraper/nfl_schedule.py -> ../data).
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Nombre fijo del archivo de caché dentro de ``data/``. Reutilizamos siempre el
# mismo archivo y decidimos su validez por la marca de tiempo de modificación
# (mtime) frente al TTL, en lugar de acumular archivos con fecha en el nombre.
CACHE_FILENAME = "nfl_schedule.csv"

# Alias de códigos de equipo NFL. El CSV de nflverse usa un conjunto de códigos
# (p. ej. "WAS", "LAR"), pero ``ui/teams.py`` y algunos títulos del plugin usan
# variantes ("WSH", "LA"). Normalizamos ambos lados a un código canónico antes
# de comparar para ser tolerantes con esas diferencias (Requisito 11.2).
_TEAM_CODE_ALIASES: dict[str, str] = {
    "WSH": "WAS",   # Washington: nflverse usa WAS; ESPN/otros usan WSH.
    "LA": "LAR",    # "LA" ambiguo → Rams (los Chargers son LAC explícito).
    "JAC": "JAX",   # Jacksonville: variante JAC.
    "SD": "LAC",    # San Diego → Los Angeles Chargers (código antiguo).
    "OAK": "LV",    # Oakland → Las Vegas Raiders (código antiguo).
    "STL": "LAR",   # St. Louis → Los Angeles Rams (código antiguo).
}


def _canon_code(code: str) -> str:
    """Normaliza un código de equipo a su forma canónica (mayúsculas + alias)."""
    up = code.strip().upper()
    return _TEAM_CODE_ALIASES.get(up, up)


@dataclass
class ScheduleGame:
    """Un partido de la jornada derivado del CSV de nflverse (diseño §3b).

    :param game_id: Identificador del partido en nflverse (p. ej.
        ``"2025_01_KC_LAC"``).
    :param gameday: Fecha del partido (``gameday`` del CSV).
    :param start_time: ``gameday`` + ``gametime`` interpretado en ET y convertido
        a la zona del kiosk. Es ``None`` si el CSV no trae ``gametime``.
    :param home_team: Código del equipo local (p. ej. ``"KC"``).
    :param away_team: Código del equipo visitante.
    """

    game_id: str
    gameday: date
    start_time: datetime | None
    home_team: str
    away_team: str


class NflScheduleClient:
    """Cliente del CSV de fixtures de nflverse.

    :param csv_url: URL del CSV de nflverse (de ``config/kiosk.yaml``).
    :param cache_ttl: Validez de la caché en segundos. Mientras el archivo
        cacheado sea más reciente que ``cache_ttl``, se reutiliza sin descargar
        (default equivale a 1 semana en ``config/kiosk.yaml``; Requisito 11.5).
    :param tz: Zona horaria del kiosk (de ``config/kiosk.yaml``). Se almacena
        para las tareas posteriores (ventana de jornada y conversión de ET);
        esta tarea solo descarga y cachea.
    :param data_dir: Directorio donde guardar la caché. Por defecto ``data/`` en
        la raíz del repositorio; parametrizable para pruebas.
    """

    def __init__(
        self,
        csv_url: str,
        cache_ttl: int,
        tz: str,
        data_dir: str | Path | None = None,
    ) -> None:
        self.csv_url = csv_url
        self.cache_ttl = cache_ttl
        self.tz = tz
        # Zona del kiosk ya resuelta a ZoneInfo (cae al default si es inválida).
        # ``self.tz`` se conserva como la cadena original; ``self._zone`` es la
        # usada para la ventana de jornada y la conversión ET → kiosk.
        self._zone = _load_zone(tz)
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        self.cache_path = self.data_dir / CACHE_FILENAME
        # Caché perezosa en memoria de la jornada para ``enrich``. La UI llama a
        # ``enrich`` una vez por evento dentro de un mismo refresco; sin esta
        # caché, N eventos recalcularían ``gameweek_games()`` (parseo del CSV +
        # construcción de ``start_time``) N veces. Memoizamos la lista en la
        # instancia y la invalidamos explícitamente con ``refresh_schedule()``
        # al inicio de cada refresco. ``fetch_rows`` ya cachea en disco contra
        # el TTL; esto evita el reparseo repetido, no la frescura de los datos.
        self._gameweek_cache: list[ScheduleGame] | None = None

    # -- API pública ----------------------------------------------------------

    def fetch_rows(self, force: bool = False) -> list[dict[str, str]]:
        """Devuelve las filas del CSV de nflverse como lista de dicts.

        Usa la caché local en ``data/`` mientras sea más reciente que
        ``cache_ttl``. Si la caché falta, está vencida o ``force`` es ``True``,
        descarga el CSV con ``requests`` y lo guarda en ``data/``.

        Degradación suave (Requisito 11.6): si la descarga falla (red, código no
        200) se intenta reutilizar la caché existente aunque esté vencida; si no
        hay ninguna caché utilizable, devuelve ``[]`` y registra el fallo en el
        log.

        :param force: Si es ``True``, ignora la validez de la caché y fuerza la
            re-descarga (lo usa ``update-now`` para refrescar el CSV; R11.5).
        """
        if not force and self._cache_is_fresh():
            rows = self._read_cache()
            if rows is not None:
                return rows
            # La caché existe pero no se pudo leer: intentamos descargar.

        text = self._download()
        if text is not None:
            self._write_cache(text)
            return _parse_csv(text)

        # La descarga falló: degradación suave. Reutilizamos cualquier caché
        # existente (aunque esté vencida) antes de rendirnos (R11.6).
        rows = self._read_cache()
        if rows is not None:
            log.warning(
                "No se pudo descargar el CSV de nflverse; se usa la caché "
                "existente en %s (posiblemente vencida).",
                self.cache_path,
            )
            return rows

        log.warning(
            "CSV de nflverse no disponible y sin caché utilizable; se opera sin "
            "fixtures (lista sin enriquecer)."
        )
        return []

    def gameweek_games(self) -> list[ScheduleGame]:
        """Devuelve los partidos de la jornada en curso (martes→lunes).

        Lee el CSV (vía :meth:`fetch_rows`, con caché), calcula la ventana de la
        jornada que contiene hoy en la zona del kiosk (martes 00:00 → lunes
        23:59; diseño §3b), se queda con las filas cuyo ``gameday`` cae dentro de
        la ventana y construye ``start_time`` combinando ``gameday`` + ``gametime``
        interpretado en ET y convertido a la zona del kiosk (Requisitos 11.1,
        11.3 y 4.1).

        Degradación suave (Requisito 11.6): si la fuente no está disponible
        (``fetch_rows`` devuelve ``[]``), devuelve ``[]`` sin lanzar.
        """
        rows = self.fetch_rows()
        if not rows:
            return []

        today = _today(self._zone)
        start, end = _gameweek_window(today)

        games: list[ScheduleGame] = []
        for row in rows:
            gameday = _parse_gameday(row.get("gameday"))
            if gameday is None or not (start <= gameday <= end):
                continue
            games.append(
                ScheduleGame(
                    game_id=row.get("game_id", "") or "",
                    gameday=gameday,
                    start_time=_build_start_time(
                        gameday, row.get("gametime"), self._zone
                    ),
                    home_team=row.get("home_team", "") or "",
                    away_team=row.get("away_team", "") or "",
                )
            )
        return games

    # -- Enriquecimiento (Tarea 5.3) -----------------------------------------

    def refresh_schedule(self) -> None:
        """Invalida la caché en memoria de la jornada usada por ``enrich``.

        La UI debe llamarla al inicio de cada refresco de la lista para que el
        siguiente ``enrich`` recalcule la jornada (y, si procede, re-descargue el
        CSV según el TTL). Entre refrescos, los ``enrich`` por evento comparten la
        misma lista memoizada.
        """
        self._gameweek_cache = None

    def _cached_gameweek_games(self) -> list[ScheduleGame]:
        """Devuelve la jornada memoizada, calculándola la primera vez.

        La memoización evita que ``enrich`` recalcule ``gameweek_games()`` por
        cada evento de un mismo refresco (ver ``__init__``). Se invalida con
        :meth:`refresh_schedule`.
        """
        if self._gameweek_cache is None:
            self._gameweek_cache = self.gameweek_games()
        return self._gameweek_cache

    def enrich(self, event: dict) -> dict:
        """Enriquece un evento del plugin con datos fiables de nflverse.

        Empareja el ``title`` del evento ("Equipo A vs Equipo B") con un
        :class:`ScheduleGame` de la jornada por código/nombre de equipo
        (reutilizando ``parse_matchup``/``team_badge``/``slug_terms`` de
        ``ui/teams.py``) y, si hay coincidencia, **rellena sólo los campos que al
        plugin le falten** (Requisito 11.2):

        - ``start_time``: si el evento no lo trae, se toma de ``ScheduleGame``
          (como cadena ISO 8601).
        - ``title``: si el título del evento es débil (no se pudo separar en dos
          equipos, o algún lado es un código escueto tipo "KC"/"WSH"), se
          reconstruye con los nombres completos de nflverse.

        Nunca sobreescribe un valor no vacío ya presente en el evento. No usa
        scores para estado live (Requisito 11.4): ``ScheduleGame`` no los expone y
        el estado live lo decide el plugin (Requisito 6).

        Degradación suave (Requisito 11.6): si la jornada no está disponible o no
        hay coincidencia, devuelve el evento sin cambios.

        El evento se muta in situ (y también se devuelve) para encajar con el uso
        de la UI.
        """
        if not isinstance(event, dict):
            return event

        games = self._cached_gameweek_games()
        if not games:
            return event

        match = self._match_game(event, games)
        if match is None:
            return event

        # Rellenar start_time sólo si falta (no sobreescribir el del plugin, que
        # es la autoridad del kickoff; R11.4). Se expone como ISO 8601.
        if not event.get("start_time") and match.start_time is not None:
            event["start_time"] = match.start_time.isoformat()

        # Mejorar el título sólo si es débil; nunca pisar uno ya legible.
        if _title_is_weak(event.get("title")):
            home = _team_display_name(match.home_team)
            away = _team_display_name(match.away_team)
            event["title"] = f"{away} vs {home}"

        return event

    def _match_game(
        self, event: dict, games: list[ScheduleGame]
    ) -> ScheduleGame | None:
        """Encuentra el ``ScheduleGame`` que corresponde al evento, o ``None``.

        Deriva los códigos canónicos de los dos equipos del evento a partir de su
        ``title`` y los compara (sin importar el orden local/visitante) con los
        de cada partido de la jornada.
        """
        event_codes = _event_team_codes(event)
        if event_codes is None:
            return None
        for game in games:
            game_codes = {_canon_code(game.home_team), _canon_code(game.away_team)}
            if event_codes == game_codes:
                return game
        return None

    # -- Caché ----------------------------------------------------------------

    def _cache_is_fresh(self) -> bool:
        """¿Existe la caché y su antigüedad no supera ``cache_ttl``?"""
        try:
            mtime = self.cache_path.stat().st_mtime
        except OSError:
            return False
        age = _time.time() - mtime
        return age <= self.cache_ttl

    def _read_cache(self) -> list[dict[str, str]] | None:
        """Lee y parsea la caché. Devuelve ``None`` si no existe o no es legible."""
        try:
            text = self.cache_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return _parse_csv(text)

    def _write_cache(self, text: str) -> None:
        """Guarda el CSV descargado en ``data/`` (crea el directorio si falta).

        La marca de tiempo que gobierna el TTL es el mtime del archivo, que el
        sistema de archivos actualiza al escribir. Si la escritura falla, se
        registra pero no se interrumpe: ya tenemos las filas en memoria.
        """
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            log.warning(
                "No se pudo escribir la caché del CSV de nflverse en %s (%s); "
                "se continúa sin cachear.",
                self.cache_path, exc,
            )

    # -- Descarga -------------------------------------------------------------

    def _download(self) -> str | None:
        """Descarga el CSV con ``requests``. Devuelve el texto o ``None`` si falla.

        Nunca lanza: ante error de red, timeout o código HTTP no exitoso,
        registra la advertencia y devuelve ``None`` para activar la degradación
        suave (R11.6).
        """
        try:
            resp = requests.get(self.csv_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning(
                "Fallo al descargar el CSV de nflverse desde %s (%s).",
                self.csv_url, exc,
            )
            return None
        return resp.text


def _parse_csv(text: str) -> list[dict[str, str]]:
    """Parsea el texto CSV a una lista de dicts (una por partido).

    Usa ``csv.DictReader`` con la primera fila como cabecera. Tolera un CSV vacío
    o sin cabecera devolviendo ``[]``.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return []
    return [dict(row) for row in reader]


def _today(zone: ZoneInfo) -> date:
    """Fecha de hoy en la zona del kiosk.

    Aislado en una función para poder fijarlo en las pruebas (se parchea
    ``nfl_schedule._today``) sin tocar ``datetime.now`` global.
    """
    return datetime.now(zone).date()


def _parse_gameday(value: str | None) -> date | None:
    """Parsea ``gameday`` ("YYYY-MM-DD") a ``date``; ``None`` si falta/inválido."""
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), _GAMEDAY_FMT).date()
    except ValueError:
        log.debug("gameday no parseable: %r", value)
        return None


def _build_start_time(
    gameday: date, gametime: str | None, zone: ZoneInfo
) -> datetime | None:
    """Combina ``gameday`` + ``gametime`` (ET) y lo convierte a la zona del kiosk.

    ``gametime`` es una hora 24h sin zona ("HH:MM") que nflverse publica en ET.
    Devuelve un ``datetime`` consciente de zona en la zona del kiosk, o ``None``
    si ``gametime`` falta o no se puede parsear (Requisito 11.3).
    """
    if not gametime or not gametime.strip():
        return None
    try:
        parsed = datetime.strptime(gametime.strip(), _GAMETIME_FMT).time()
    except ValueError:
        log.debug("gametime no parseable: %r", gametime)
        return None
    et_dt = datetime.combine(
        gameday, _dtime(parsed.hour, parsed.minute), tzinfo=_ET_ZONE
    )
    return et_dt.astimezone(zone)


# Códigos canónicos conocidos (los de ``ui/teams.py`` tras aplicar alias). Se usa
# para reconocer un término de slug que ya es un código de equipo (p. ej. el
# título trae "KC vs PHI" en lugar de nombres completos).
_KNOWN_CODES: set[str] = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB",
    "TEN", "WAS",
}

# Código canónico -> nombre completo del equipo (invertido desde ui/teams.py vía
# team_badge). Se construye perezosamente para no acoplar al orden de import.
_CODE_TO_NAME: dict[str, str] = {}


def _team_code_for_name(name: str) -> str | None:
    """Deriva el código canónico de equipo a partir de un nombre del título.

    Reutiliza la lógica de ``ui/teams.py``: ``team_badge`` da la abreviatura
    oficial para un NFL conocido, y ``slug_terms`` aporta términos candidatos por
    si el nombre ya es un código (p. ej. "KC"). Devuelve ``None`` si no se puede
    reconocer un equipo NFL (p. ej. nombre genérico sin match).
    """
    if not name or not name.strip():
        return None

    # 1) El lado del título puede ser ya un código ("KC", "PHI", o un alias como
    #    "WSH"/"LA"). Lo comprobamos primero porque ``team_badge`` hace match por
    #    subcadena y podría confundir un código corto con otro equipo.
    direct = _canon_code(name)
    if direct in _KNOWN_CODES:
        return direct

    # 2) Nombre de equipo: ``team_badge`` da la abreviatura oficial del NFL
    #    conocido (o iniciales genéricas si no lo reconoce).
    abbr, _color = team_badge(name)
    code = _canon_code(abbr)
    if code in _KNOWN_CODES:
        return code

    # 3) Último recurso: alguno de los términos del slug (ciudad/apodo/abreviatura)
    #    puede coincidir con un código conocido (p. ej. un nombre con ruido
    #    alrededor de un código). Reutiliza ``slug_terms`` de ui/teams.py.
    for term in slug_terms(name, abbr):
        candidate = _canon_code(term)
        if candidate in _KNOWN_CODES:
            return candidate
    return None


def _event_team_codes(event: dict) -> frozenset[str] | None:
    """Códigos canónicos de los dos equipos del evento, o ``None``.

    Separa el ``title`` ("A vs B") con ``parse_matchup`` y resuelve cada lado a su
    código. Devuelve ``None`` si el título no se puede separar o si algún equipo
    no se reconoce (sin pareja no hay emparejamiento fiable).
    """
    pair = parse_matchup(str(event.get("title", "")))
    if pair is None:
        return None
    code_a = _team_code_for_name(pair[0])
    code_b = _team_code_for_name(pair[1])
    if code_a is None or code_b is None or code_a == code_b:
        return None
    return frozenset({code_a, code_b})


def _title_is_weak(title: str | None) -> bool:
    """¿El título del evento es débil (merece reconstruirse con nflverse)?

    Sólo mejoramos el título cuando aporta poco frente a los nombres completos de
    nflverse, sin pisar un título ya descriptivo (R11.2). Es débil si:

    - está vacío o no se puede separar en dos equipos ("A vs B"); o
    - algún lado es un **código escueto** ("KC", "PHI", "WSH"): el token, tras
      normalizar, coincide con un código de equipo conocido. En ese caso el
      nombre completo es más legible en la pantalla del kiosk.

    Un título con nombres de equipo legibles (aunque sean apodos reconocibles) no
    se considera débil y se deja intacto.
    """
    if not title or not title.strip():
        return True
    pair = parse_matchup(title)
    if pair is None:
        return True
    return _side_is_bare_code(pair[0]) or _side_is_bare_code(pair[1])


def _side_is_bare_code(name: str) -> bool:
    """¿Este lado del título es un código escueto (p. ej. "KC", "WSH")?"""
    return _canon_code(name) in _KNOWN_CODES


def _team_display_name(code: str) -> str:
    """Nombre completo "Title Case" del equipo para un código canónico.

    Deriva el nombre desde el mapa de ``ui/teams.py`` (invertido en caché). Si el
    código no está en el mapa, cae al propio código en mayúsculas.
    """
    if not _CODE_TO_NAME:
        # Construcción perezosa: invertimos _NFL_TEAMS vía team_badge sobre cada
        # nombre conocido para no importar la estructura privada de ui.teams.
        from ui.teams import _NFL_TEAMS  # import local: estructura interna.

        for full_name, (abbr, _color) in _NFL_TEAMS.items():
            _CODE_TO_NAME[_canon_code(abbr)] = full_name.title()

    return _CODE_TO_NAME.get(_canon_code(code), code.upper())
