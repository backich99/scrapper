"""Cliente del addon live-sport-plugin (protocolo Stremio).

Única clase del proyecto que habla el protocolo Stremio del addon
``live-sport-plugin`` (servicio Node local). Traduce las respuestas del plugin
al esquema de evento del proyecto, de modo que la UI, el player y el fallback no
conozcan nada de ``/catalog``, ``/stream`` ni de los ids ``nuvio_sport_<id>``.

Este módulo contiene el esqueleto (Tarea 2.1): el constructor, el healthcheck
``is_up()`` y el modelo de datos :class:`Stream`. El listado de la jornada
(``list_nfl_gameweek``) y la resolución de streams (``resolve_streams``) se
implementan en tareas posteriores (2.2 y 2.3).

Diseñado para degradar en lugar de fallar: si el plugin no responde,
``is_up()`` devuelve ``False`` sin lanzar excepción, y la capa Python lo trata
como fuente no disponible (Requisito 3.4).
"""

from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

log = logging.getLogger(__name__)

# Zona por defecto del kiosk (coincide con el default de ScheduleConfig en
# scraper/kiosk_config.py). Se usa si no se pasa una zona o si la indicada no
# existe en la base de datos de zonas horarias.
DEFAULT_TIMEZONE = "America/Mexico_City"

# Deporte que produce este catálogo (el plugin agrupa NFL bajo "american_football").
_SPORT_AMERICAN_FOOTBALL = "american_football"

# El catálogo "american_football" mezcla NFL y college (NCAA). El plugin no
# expone una liga fiable, así que la única señal es el título (reporte §2.3).
# Nos quedamos con lo que parezca NFL y descartamos lo que claramente sea
# college. La comprobación es sobre el título en minúsculas.
_NFL_POSITIVE = re.compile(r"\bnfl\b")
# Términos que delatan fútbol americano universitario (no NFL).
_COLLEGE_HINTS = re.compile(
    r"\b(ncaa|college|cfb|university|univ\.?|state|wildcats|bulldogs|"
    r"crimson tide|longhorns|aggies|gators|buckeyes|sooners)\b"
)
# Día de la semana en que arranca la jornada: martes (date.weekday(): lun=0 … dom=6).
_TUESDAY = 1

# Umbral de "pocas fuentes": si la primera llamada a /stream devuelve menos
# streams reproducibles que esto, se reintenta una vez tras ``retry_delay`` (el
# plugin cachea las fuentes lentas entre llamadas; reporte §3.2). Dos fuentes ya
# permiten "siguiente streaming" (R7), así que por debajo de eso vale la pena
# esperar a que maduren más.
_FEW_SOURCES_THRESHOLD = 2

# Entradas del plugin que nunca son reproducibles por mpv y deben descartarse:
# la donación "🧿 Support Us" (sin ``url``) y los "🌐 Web Stream" (iframe web).
# La comparación se hace sobre ``name`` en minúsculas para tolerar el emoji.
_SUPPORT_US_MARK = "support us"
_WEB_STREAM_MARK = "web stream"

# Providers conocidos del plugin, para parsear ``source`` del ``title``. El
# título viene como "<icono> <Provider> | ..."; buscamos el provider como
# subcadena (case-insensitive). Lista no exhaustiva; si no hay match se intenta
# un parseo genérico del primer segmento del título.
_KNOWN_PROVIDERS = (
    "DaddyLive", "Streamed.pk", "Streamed", "WatchFooty", "EmbedSt",
    "EmbedIndia", "DamiTv", "TimStreams", "PpvSt", "ReplayZone", "LiveTV",
    "YouTube",
)

# Resolución tipo "1920x1080" → altura = segundo grupo.
_RESOLUTION_WXH = re.compile(r"(\d+)\s*x\s*(\d+)")
# Resolución/etiqueta tipo "1080p", "720p60" → altura = primer grupo.
_RESOLUTION_NP = re.compile(r"(\d{3,4})\s*p", re.IGNORECASE)

# Lista no exhaustiva de abreviaturas de equipos NFL; sirve como señal positiva
# cuando el título no incluye literalmente "NFL" pero sí nombres de franquicia.
_NFL_TEAM_TERMS = (
    "cardinals", "falcons", "ravens", "bills", "panthers", "bears", "bengals",
    "browns", "cowboys", "broncos", "lions", "packers", "texans", "colts",
    "jaguars", "chiefs", "raiders", "chargers", "rams", "dolphins", "vikings",
    "patriots", "saints", "giants", "jets", "eagles", "steelers", "49ers",
    "niners", "seahawks", "buccaneers", "titans", "commanders", "redskins",
)


@dataclass
class Stream:
    """Un stream resuelto que ``PluginClient`` entrega a la capa de reproducción.

    La resolución es best-effort: ``resolution_height`` puede ser ``None`` cuando
    el plugin no la conoce (p. ej. la reporta como ``'HD'``). Las cabeceras
    ``referer`` y ``origin`` provienen de ``behaviorHints.proxyHeaders.request``
    y solo están presentes en streams que las requieren.
    """

    url: str
    source: str                     # provider parseado del title (DaddyLive, ...)
    resolution_height: int | None   # 1080, 720, ... o None si desconocida
    referer: str | None             # de behaviorHints.proxyHeaders.request
    origin: str | None              # idem
    label: str                      # title legible para la UI


class PluginClient:
    """Cliente HTTP del addon live-sport-plugin.

    :param base_url: URL base del servicio Node (p. ej. ``http://localhost:7000``).
    :param timeout: Timeout en segundos para cada petición HTTP.
    :param timezone: Zona horaria del kiosk (de ``config/kiosk.yaml``). Se usa
        para calcular la ventana de la jornada (martes→lunes que contiene hoy).
        Si la zona no existe, se cae al default y se registra una advertencia.
    :param retry_delay: Segundos a esperar antes de reintentar ``/stream`` una
        vez cuando la primera llamada trae pocas fuentes (default 4, coincide con
        ``config/kiosk.yaml``). El plugin cachea las fuentes lentas entre
        llamadas, así que un reintento suele traer más (reporte §3.2).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float,
        timezone: str = DEFAULT_TIMEZONE,
        retry_delay: float = 4,
    ) -> None:
        # Normalizamos la base quitando la barra final para componer rutas sin
        # dobles barras ("http://host:7000/" + "/health" -> "...//health").
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.tz = _load_zone(timezone)
        self.retry_delay = retry_delay

    def is_up(self) -> bool:
        """Comprueba si el plugin está listo para atender peticiones.

        Intenta ``GET /health`` y, si no responde con éxito, ``GET /manifest.json``
        (el router del SDK Stremio siempre sirve el manifest). Devuelve ``True``
        solo si alguno responde con un código HTTP de éxito.

        Nunca lanza: ante cualquier error de red, timeout o estado no listo del
        plugin, devuelve ``False`` para que la capa Python lo trate como fuente no
        disponible y recurra al fallback (Requisito 3.4).
        """
        for path in ("/health", "/manifest.json"):
            url = f"{self.base_url}{path}"
            try:
                resp = requests.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                log.debug("plugin is_up: %s no respondió: %s", url, exc)
                continue
            if resp.ok:
                return True
            log.debug("plugin is_up: %s devolvió HTTP %s", url, resp.status_code)
        return False

    def list_nfl_gameweek(self) -> list[dict]:
        """Devuelve los eventos NFL de la jornada en curso.

        Pide ``GET /catalog/tv/nuvio_sports_american_football.json``, mapea cada
        ``meta`` al esquema de evento del proyecto, se queda con lo que parezca
        NFL (heurística de título, el plugin no separa NFL de college) y filtra
        por la ventana de la jornada: martes 00:00 → lunes 23:59 (en la zona del
        kiosk) que contiene el día de hoy (Requisitos 4.1 y 11.1).

        Los eventos sin ``start_time`` (canales 24/7, kickoff desconocido) se
        conservan: la jornada no puede excluirlos por fecha y la UI los muestra
        como parte de la lista. El ``stream_url`` se rellena luego al resolver.

        Nunca lanza: ante error de red o respuesta inesperada devuelve ``[]``
        para que la capa Python recurra al fallback (Requisito 4.5).
        """
        url = f"{self.base_url}/catalog/tv/nuvio_sports_american_football.json"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("list_nfl_gameweek: fallo al obtener el catálogo (%s)", exc)
            return []

        metas = payload.get("metas") if isinstance(payload, dict) else None
        if not isinstance(metas, list):
            log.warning("list_nfl_gameweek: respuesta sin 'metas' esperado")
            return []

        start, end = _gameweek_window(datetime.now(self.tz).date())
        events: list[dict] = []
        for meta in metas:
            if not isinstance(meta, dict):
                continue
            event = self._meta_to_event(meta)
            if event is None:
                continue
            if not _is_nfl(event["title"]):
                continue
            if not _in_gameweek(event["start_time"], start, end, self.tz):
                continue
            events.append(event)
        return events

    def _meta_to_event(self, meta: dict) -> dict | None:
        """Mapea un ``meta`` del catálogo al esquema de evento del proyecto.

        Devuelve ``None`` si el ``meta`` no tiene id o título usables.
        """
        event_id = meta.get("id")
        title = meta.get("name")
        if not isinstance(event_id, str) or not event_id:
            return None
        if not isinstance(title, str) or not title.strip():
            return None

        start_time = _parse_released(meta.get("released"))
        return {
            "id": event_id,
            "title": title.strip(),
            "league": "NFL",
            "sport": _SPORT_AMERICAN_FOOTBALL,
            "start_time": start_time,
            "stream_url": None,
            "referrer": "",
            "source": "live-sport-plugin",
            "status": _derive_status(start_time, datetime.now(self.tz)),
        }

    def resolve_streams(self, event_id: str) -> list[Stream]:
        """Resuelve las fuentes reproducibles de un partido, ya rankeadas.

        Pide ``GET /stream/tv/<event_id>.json`` y traduce la respuesta Stremio a
        una lista de :class:`Stream`, **preservando el orden** (el plugin ya las
        entrega mejor→peor; Requisitos 6.1 y 7.1). Se descartan las entradas no
        reproducibles por mpv (reporte §3.3/§5):

        - la donación "🧿 Support Us" (sin ``url``),
        - los "🌐 Web Stream" (iframe web, no HLS directo),
        - y cualquier stream sin ``url`` reproducible.

        De cada stream se parsea el ``source`` (provider) del ``title``, la
        altura de resolución (best-effort; ``None`` si el plugin no la conoce) y,
        si vienen, las cabeceras ``referer``/``origin`` de
        ``behaviorHints.proxyHeaders.request``.

        Si la primera llamada trae **pocas** fuentes (el plugin aún está minteando
        las lentas en background), se reintenta **una vez** tras ``retry_delay``
        segundos y se queda con la respuesta que traiga más fuentes.

        Nunca lanza: ante error de red o respuesta inesperada devuelve ``[]`` para
        que la capa Python recurra al fallback (Requisito 5.2).
        """
        streams = self._fetch_streams(event_id)
        if 0 < len(streams) < _FEW_SOURCES_THRESHOLD and self.retry_delay > 0:
            log.debug(
                "resolve_streams: %s trajo %d fuente(s); reintento tras %ss",
                event_id, len(streams), self.retry_delay,
            )
            _time.sleep(self.retry_delay)
            retry = self._fetch_streams(event_id)
            # El reintento solo mejora: nos quedamos con la lista más larga para
            # no perder fuentes si la segunda llamada trajera menos.
            if len(retry) > len(streams):
                streams = retry
        return streams

    def _fetch_streams(self, event_id: str) -> list[Stream]:
        """Una llamada a ``/stream`` → lista de :class:`Stream` reproducibles.

        Degrada en ``[]`` ante cualquier error de red o respuesta inesperada.
        """
        url = f"{self.base_url}/stream/tv/{event_id}.json"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("resolve_streams: fallo al obtener %s (%s)", url, exc)
            return []

        raw = payload.get("streams") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            log.warning("resolve_streams: respuesta sin 'streams' esperado")
            return []

        streams: list[Stream] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            stream = _stream_from_item(item)
            if stream is not None:
                streams.append(stream)
        return streams


def _stream_from_item(item: dict) -> Stream | None:
    """Traduce un stream Stremio al modelo :class:`Stream`, o ``None`` si no sirve.

    Descarta "Support Us", "Web Stream" y cualquier entrada sin ``url``
    reproducible (reporte §3.3/§5).
    """
    name = item.get("name")
    name_low = name.lower() if isinstance(name, str) else ""
    if _SUPPORT_US_MARK in name_low or _WEB_STREAM_MARK in name_low:
        return None

    url = item.get("url")
    if not isinstance(url, str) or not url.strip():
        return None

    title = item.get("title") if isinstance(item.get("title"), str) else ""
    referer, origin = _parse_proxy_headers(item.get("behaviorHints"))
    return Stream(
        url=url.strip(),
        source=_parse_source(title),
        resolution_height=_parse_resolution_height(item.get("resolution"), title),
        referer=referer,
        origin=origin,
        label=title.strip() or (name.strip() if isinstance(name, str) else url),
    )


def _parse_source(title: str) -> str:
    """Parsea el provider (``source``) del ``title`` del stream.

    El título viene como "<icono> <Provider> | 🎬 ... | 📺 ...". Primero se busca
    un provider conocido como subcadena (case-insensitive); si no, se usa el
    primer segmento legible (hasta el primer ``|`` o salto de línea), quitando el
    emoji inicial. Devuelve ``"Unknown"`` si no se puede determinar.
    """
    if not title:
        return "Unknown"
    low = title.lower()
    for provider in _KNOWN_PROVIDERS:
        if provider.lower() in low:
            return provider
    # Fallback: primer segmento, sin el icono/emoji inicial.
    first = re.split(r"[|\n]", title, maxsplit=1)[0].strip()
    # Quitar un posible emoji/símbolo inicial y espacios sobrantes.
    cleaned = first.lstrip("⚡🌐▶️🧿📺🎬👥⏱ ").strip()
    return cleaned or "Unknown"


def _parse_resolution_height(resolution: object, title: str) -> int | None:
    """Altura en píxeles del stream (best-effort), o ``None`` si desconocida.

    Orden de fiabilidad (reporte §4): ``resolution`` ``"WxH"`` → H; ``"Np"`` → N;
    ``"HD"``/``"SD"``/``None`` → desconocida (no se descarta, pero no se puede
    topar con certeza); fallback regex ``(\\d{3,4})p`` sobre el ``title``.
    """
    if isinstance(resolution, str) and resolution.strip():
        m = _RESOLUTION_WXH.search(resolution)
        if m:
            return int(m.group(2))
        m = _RESOLUTION_NP.search(resolution)
        if m:
            return int(m.group(1))
    # Fallback: buscar "...1080p..." en el título legible.
    if title:
        m = _RESOLUTION_NP.search(title)
        if m:
            return int(m.group(1))
    return None


def _parse_proxy_headers(behavior_hints: object) -> tuple[str | None, str | None]:
    """Extrae ``referer``/``origin`` de ``behaviorHints.proxyHeaders.request``.

    Devuelve ``(None, None)`` si no vienen (caso ``/api/manifest``, donde el
    propio plugin inyecta las cabeceras aguas arriba y mpv no necesita nada).
    """
    if not isinstance(behavior_hints, dict):
        return None, None
    proxy = behavior_hints.get("proxyHeaders")
    if not isinstance(proxy, dict):
        return None, None
    request = proxy.get("request")
    if not isinstance(request, dict):
        return None, None
    referer = request.get("Referer") or request.get("referer")
    origin = request.get("Origin") or request.get("origin")
    return (
        referer if isinstance(referer, str) and referer else None,
        origin if isinstance(origin, str) and origin else None,
    )


def _load_zone(name: str) -> ZoneInfo:
    """Carga una ``ZoneInfo``; cae al default si la zona no existe."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        log.warning(
            "Zona horaria %r inválida (%s); se usa el default %r.",
            name, exc, DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def _parse_released(released: object) -> str | None:
    """Normaliza el campo ``released`` del meta a un ISO 8601 UTC con ``Z``.

    El plugin entrega ``released`` como ISO 8601 (p. ej.
    ``"2026-10-17T18:30:00.000Z"``). Devolvemos una cadena ISO en UTC con sufijo
    ``Z`` (el esquema del proyecto usa ese formato), o ``None`` si falta o no se
    puede interpretar (canales 24/7, kickoff desconocido).
    """
    if not isinstance(released, str) or not released.strip():
        return None
    text = released.strip()
    # datetime.fromisoformat (3.11+) acepta el sufijo "Z".
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        log.debug("released no parseable: %r", text)
        return None
    # Sin zona explícita se asume UTC (el plugin emite UTC).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    # Formato canónico ...Z, sin microsegundos.
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_status(start_time: str | None, now: datetime) -> str:
    """Deriva ``status`` (live/upcoming/unavailable) a partir del kickoff.

    Americanos usan una ventana de 4 h desde el kickoff para considerarse en
    vivo (reporte §2.4). Sin kickoff conocido no podemos afirmar que esté en
    vivo ni próximo, así que se marca ``unavailable`` (la autoridad real del
    estado live la da ``/stream`` al resolver).
    """
    if start_time is None:
        return "unavailable"
    kickoff = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    now_utc = now.astimezone(timezone.utc)
    if now_utc < kickoff:
        return "upcoming"
    if now_utc <= kickoff + timedelta(hours=4):
        return "live"
    return "unavailable"


def _is_nfl(title: str) -> bool:
    """Heurística de título para quedarse con NFL y descartar college.

    El catálogo ``american_football`` mezcla NFL y NCAA; el plugin no expone una
    liga fiable (reporte §2.3). Reglas:

    - Si el título menciona explícitamente "NFL" → NFL.
    - Si menciona términos de college (NCAA, "University", apodos típicos) y no
      dice "NFL" → se descarta.
    - Si menciona un equipo NFL conocido → NFL.
    - En otro caso (ambiguo) se conserva: preferimos no perder un partido NFL
      por una señal débil; el usuario pidió solo NFL pero el catálogo ya es de
      americanos y el ruido de college real suele venir etiquetado.
    """
    low = title.lower()
    if _NFL_POSITIVE.search(low):
        return True
    if _COLLEGE_HINTS.search(low):
        return False
    if any(term in low for term in _NFL_TEAM_TERMS):
        return True
    return True


def _gameweek_window(today: date) -> tuple[date, date]:
    """Ventana de la jornada que contiene ``today``: martes → lunes.

    La jornada NFL va de martes a lunes (arranca con el TNF del jueves y cierra
    con el MNF del lunes). Cálculo (diseño §3b):
    ``martes = hoy - ((weekday - MARTES) mod 7)``; ``lunes = martes + 6 días``.
    """
    tuesday = today - timedelta(days=(today.weekday() - _TUESDAY) % 7)
    monday = tuesday + timedelta(days=6)
    return tuesday, monday


def _in_gameweek(
    start_time: str | None, start: date, end: date, tz: ZoneInfo
) -> bool:
    """Indica si ``start_time`` cae dentro de la ventana [start, end] en ``tz``.

    Los eventos sin ``start_time`` (canales 24/7, kickoff desconocido) se
    conservan: no podemos ubicarlos por fecha y descartarlos perdería partidos
    cuyo kickoff aún no publicó el plugin.
    """
    if start_time is None:
        return True
    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    local_date = dt.astimezone(tz).date()
    return start <= local_date <= end
