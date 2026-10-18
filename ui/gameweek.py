"""Adaptación y ordenación de la jornada NFL para la UI (lógica pura).

Este módulo concentra la parte **sin Kivy** de la integración del plugin en la
pantalla de la jornada (Tarea 6), para poder probarla con ``unittest`` sin un
display:

- :class:`PluginGame`: adaptador que convierte un evento del plugin (dict con el
  esquema del proyecto) en un objeto que "habla" como ``AgendaGame`` —los mismos
  atributos y métodos que ``MatchCard`` consume— sin tocar el render de la
  tarjeta.
- :func:`adapt_plugin_event`: construye un :class:`PluginGame` a partir de un
  evento del plugin, derivando nombres/abreviaturas desde el título
  ("Away vs Home") con ``ui/teams.py``.
- :func:`order_gameweek`: orden híbrido de la jornada (en vivo y de hoy arriba,
  próximos debajo) (Requisito 4.2).
- :class:`UnavailableTracker`: recuerda, entre refrescos, qué ``event_id`` se
  marcaron "no disponible" para volver a mostrarlos como tal (Requisito 4.4).

La UI (``ui/app.py``) usa estas piezas desde ``KioskRoot.refresh()`` y conserva
la ruta de ``Agenda`` (ESPN) como fallback (Requisito 5.1).
"""

from __future__ import annotations

import datetime as dt
import logging

from .teams import parse_matchup, team_badge

log = logging.getLogger(__name__)

# Mapa estado del plugin -> estado al estilo ESPN que consume ``MatchCard``.
# El plugin usa "live"/"upcoming"/"unavailable"; ``AgendaGame.state`` usa
# "in"/"pre"/"post". No hay un "post" nativo en el plugin (la autoridad del fin
# de partido no la da el catálogo), así que "unavailable" se mapea a "pre": la
# tarjeta no quedará jugable salvo que el estado real sea "in".
_PLUGIN_STATE_TO_ESPN: dict[str, str] = {
    "live": "in",
    "upcoming": "pre",
    "unavailable": "pre",
}

# Texto que muestra la tarjeta cuando el partido se marcó "no disponible".
UNAVAILABLE_TEXT = "No disponible"

# Texto del overlay de reproducción cuando se agotan las fuentes (Requisito 9.4).
NO_MORE_SOURCES_TEXT = "No hay más fuentes"


def source_label(index: tuple[int, int]) -> str:
    """Formatea el indicador "fuente N de M" del overlay (Requisito 9.5).

    :param index: tupla ``(actual, total)`` tal como la devuelve
        ``SourceSwitcher.index()`` (``actual`` en base 1; ``(0, 0)`` si no hay
        fuentes).
    :returns: cadena legible para la pantalla de 7". Si no hay fuentes
        (``total == 0``) devuelve cadena vacía, de modo que el overlay pueda
        ocultar el indicador.
    """
    current, total = index
    if total <= 0:
        return ""
    return f"Fuente {current} de {total}"


def next_source_enabled(has_next: bool) -> bool:
    """Decide si el botón "Siguiente streaming" debe estar habilitado (R9.4).

    Función trivial pero explícita para poder probar la regla sin Kivy: el botón
    se habilita sólo si ``SourceSwitcher.has_next()`` indica que queda al menos
    una fuente por delante.
    """
    return bool(has_next)


class PluginGame:
    """Adaptador de un evento del plugin con la interfaz de ``AgendaGame``.

    Expone exactamente los atributos y métodos que ``MatchCard`` consume
    (``home_name``/``away_name``/``home_abbr``/``away_abbr``/``start``/``state``,
    las propiedades ``is_live``/``is_final``/``title`` y los métodos
    ``is_playable``/``status_text``), de modo que la tarjeta no distingue si el
    partido viene del plugin o de ESPN. El ``event_id`` del plugin se conserva
    para la resolución bajo demanda (Tarea 7).

    No se incluyen marcadores: el catálogo del plugin no los expone de forma
    fiable (el estado live lo decide el plugin, Requisito 11.4), así que
    ``score_home``/``score_away`` quedan en 0 y la tarjeta muestra "VS".
    """

    # Minutos antes del kickoff a partir de los cuales se permite reproducir.
    # Coincide con ``ui.agenda.PLAYABLE_LEAD_MINUTES`` para un comportamiento
    # homogéneo entre la fuente del plugin y la de ESPN.
    PLAYABLE_LEAD_MINUTES = 15

    def __init__(
        self,
        event_id: str,
        home_name: str,
        away_name: str,
        home_abbr: str,
        away_abbr: str,
        start: dt.datetime | None,
        state: str,
        detail: str = "",
    ) -> None:
        self.event_id = event_id
        self.home_name = home_name
        self.away_name = away_name
        self.home_abbr = home_abbr
        self.away_abbr = away_abbr
        self.start = start
        self.state = state
        self.detail = detail
        # El plugin no entrega marcador; la tarjeta mostrará "VS".
        self.score_home = 0
        self.score_away = 0

    @property
    def is_live(self) -> bool:
        return self.state == "in"

    @property
    def is_final(self) -> bool:
        return self.state == "post"

    @property
    def title(self) -> str:
        """Título estilo 'Away vs Home' (formato habitual del plugin/StreamEast)."""
        return f"{self.away_name} vs {self.home_name}"

    def is_playable(self, now: dt.datetime | None = None) -> bool:
        """True si ya se puede intentar reproducir.

        Misma ventana que ``ui.agenda.AgendaGame``: desde
        ``PLAYABLE_LEAD_MINUTES`` antes del kickoff hasta que el partido termine.
        En vivo siempre True; finalizado nunca; sin hora conocida no es jugable.
        """
        if self.is_final:
            return False
        if self.is_live:
            return True
        if self.start is None:
            return False
        now = now or dt.datetime.now(dt.timezone.utc)
        lead = dt.timedelta(minutes=self.PLAYABLE_LEAD_MINUTES)
        return now >= (self.start - lead)

    def status_text(self, now: dt.datetime | None = None) -> str:
        """Texto de estado para la tarjeta (hora local si es pre-partido)."""
        if self.is_live:
            return self.detail or "EN VIVO"
        if self.is_final:
            return self.detail or "Final"
        if self.start is not None:
            return self.start.astimezone().strftime("%H:%M")
        return self.detail or "Programado"


def _parse_start(start_time: str | None) -> dt.datetime | None:
    """Parsea un ``start_time`` ISO 8601 (con 'Z' o offset) a ``datetime`` aware.

    Devuelve ``None`` si falta o no se puede interpretar (canales 24/7, kickoff
    desconocido). El plugin emite UTC con sufijo 'Z'; ``enrich`` puede sustituirlo
    por un ISO con offset de la zona del kiosk — ambos son válidos aquí.
    """
    if not start_time or not isinstance(start_time, str):
        return None
    try:
        return dt.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except ValueError:
        log.debug("start_time no parseable: %r", start_time)
        return None


def adapt_plugin_event(event: dict) -> PluginGame | None:
    """Convierte un evento del plugin (dict) en un :class:`PluginGame`.

    Deriva los nombres de equipo del ``title`` ("Away vs Home") con
    ``parse_matchup`` y las abreviaturas con ``team_badge`` de ``ui/teams.py``.
    Mapea el ``status`` del plugin ("live"/"upcoming"/"unavailable") al ``state``
    estilo ESPN que consume ``MatchCard``.

    Devuelve ``None`` si el evento no tiene ``id`` o un ``title`` separable en dos
    equipos (sin eso no se puede pintar una tarjeta coherente).
    """
    if not isinstance(event, dict):
        return None
    event_id = event.get("id")
    title = event.get("title")
    if not isinstance(event_id, str) or not event_id:
        return None
    if not isinstance(title, str) or not title.strip():
        return None

    pair = parse_matchup(title)
    if pair is None:
        # Título no separable ("A vs B"): no podemos derivar dos equipos.
        return None
    away_name, home_name = pair[0], pair[1]
    away_abbr, _away_col = team_badge(away_name)
    home_abbr, _home_col = team_badge(home_name)

    status = event.get("status")
    state = _PLUGIN_STATE_TO_ESPN.get(status, "pre") if isinstance(status, str) else "pre"

    return PluginGame(
        event_id=event_id,
        home_name=home_name,
        away_name=away_name,
        home_abbr=home_abbr,
        away_abbr=away_abbr,
        start=_parse_start(event.get("start_time")),
        state=state,
        detail="",
    )


def _is_today(game, today: dt.date) -> bool:
    """¿El kickoff del partido cae hoy (en la zona local del dispositivo)?"""
    if game.start is None:
        return False
    return game.start.astimezone().date() == today


def order_gameweek(games: list, today: dt.date | None = None) -> list:
    """Orden híbrido de la jornada (Requisito 4.2).

    Prioriza arriba los partidos **en vivo** y los **de hoy**; deja el resto de la
    jornada (próximos y, de haberlos, finalizados) debajo. Dentro de cada grupo
    se ordena por hora de kickoff ascendente (los de hora desconocida al final).

    No muta la lista de entrada; devuelve una nueva lista ordenada.
    """
    today = today or dt.datetime.now().date()
    far_future = dt.datetime.max.replace(tzinfo=dt.timezone.utc)

    def sort_key(game) -> tuple:
        # Grupo 0 = destacado (en vivo o de hoy); grupo 1 = resto de la jornada.
        top = game.is_live or _is_today(game, today)
        group = 0 if top else 1
        start = game.start or far_future
        return (group, start)

    return sorted(games, key=sort_key)


class UnavailableTracker:
    """Recuerda qué ``event_id`` se marcaron "no disponible" entre refrescos.

    Al reconstruir las tarjetas, la UI consulta ``is_unavailable(event_id)`` para
    volver a pintar como "no disponible" un partido que ya se marcó así en una
    resolución previa (Requisito 4.4). Los eventos de fallback (sin ``event_id``)
    no se registran (``None`` nunca se marca).
    """

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def mark(self, event_id: str | None) -> None:
        """Marca un ``event_id`` como no disponible (ignora ``None``)."""
        if event_id:
            self._ids.add(event_id)

    def clear(self, event_id: str | None) -> None:
        """Olvida la marca de un ``event_id`` (p. ej. si vuelve a reproducir)."""
        if event_id:
            self._ids.discard(event_id)

    def is_unavailable(self, event_id: str | None) -> bool:
        """¿Está este ``event_id`` marcado como no disponible?"""
        return bool(event_id) and event_id in self._ids
