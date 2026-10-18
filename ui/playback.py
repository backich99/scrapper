"""Lógica pura de resolución bajo demanda para la UI (Tarea 7).

Este módulo concentra la parte **sin Kivy** del flujo "tocar un partido →
resolver streams → elegir el mejor" (diseño §5, Flujo B), para poder probarla
con ``unittest`` sin un display:

- :func:`wrap_stream_result`: envuelve el ``StreamResult`` del fallback
  (``scraper/ondemand.py``) como un :class:`~scraper.plugin_client.Stream`
  único, de modo que el resto de la capa de reproducción (``SourceSwitcher`` /
  ``ResolutionPolicy`` / mpv) no distinga la procedencia (Requisito 5.2).
- :func:`resolve_streams`: intenta primero el plugin
  (``PluginClient.resolve_streams``) y, si no devuelve nada, cae al scraper
  Python existente (Requisito 5.2), registrando el uso del fallback
  (Requisito 5.4). Devuelve la lista de :class:`Stream` rankeados.

La construcción del ``SourceSwitcher`` y la reproducción en mpv viven en la UI
(``ui/app.py``, ``KioskRoot._resolve_and_play``), porque dependen del hilo de
Kivy y del reproductor; aquí sólo queda la lógica pura y determinista.
"""

from __future__ import annotations

import logging

from scraper.plugin_client import Stream

log = logging.getLogger(__name__)

# ``source``/``label`` para el stream único que produce el fallback de StreamEast.
_FALLBACK_SOURCE = "streameast"
_FALLBACK_LABEL = "StreamEast"


def wrap_stream_result(result) -> Stream:
    """Envuelve un ``StreamResult`` del fallback como un :class:`Stream`.

    El scraper on-demand (``scraper/ondemand.py``) produce
    ``StreamResult(url, referrer)``. Para que el resto de la capa de reproducción
    (``SourceSwitcher`` / ``ResolutionPolicy`` / mpv) lo trate igual que un stream
    del plugin, lo envolvemos en un :class:`Stream` único: altura desconocida
    (el fallback no la reporta), sin ``origin`` y con el ``referrer`` como
    ``referer`` (Requisito 5.2; diseño §4).

    :param result: Un ``scraper.ondemand.StreamResult`` (``url`` y ``referrer``).
    :returns: Un :class:`Stream` equivalente, listo para ``SourceSwitcher``.
    """
    return Stream(
        url=result.url,
        source=_FALLBACK_SOURCE,
        resolution_height=None,
        referer=result.referrer or None,
        origin=None,
        label=_FALLBACK_LABEL,
    )


def resolve_streams(plugin, event_id, fallback_resolve, a_terms, b_terms,
                    timeout) -> list[Stream]:
    """Resuelve las fuentes de un partido: plugin primero, luego fallback.

    Flujo (diseño §5, Flujo B; Requisito 6.1 / 5.2):

    1. Si ``event_id`` existe, pide ``plugin.resolve_streams(event_id)`` (que ya
       lleva su propio reintento interno). Si devuelve streams, se usan.
    2. Si no hay ``event_id`` o el plugin no devolvió nada, se cae al scraper
       Python existente vía ``fallback_resolve(a_terms, b_terms, timeout=...)``.
       El uso del fallback se registra en el log (Requisito 5.4). Si el fallback
       devuelve un resultado, se envuelve como un :class:`Stream` único.

    No lanza: cualquier excepción del plugin o del fallback se captura y se
    registra, devolviendo lo que se tenga hasta ese momento (la UI nunca debe
    romperse por un fallo de resolución).

    :param plugin: ``PluginClient`` (o compatible con ``resolve_streams(id)``).
    :param event_id: id del evento del plugin, o ``None`` para ir directo al
        fallback (tarjetas de ESPN).
    :param fallback_resolve: callable ``(a_terms, b_terms, timeout=...) -> StreamResult | None``
        (normalmente ``scraper.ondemand.resolve_match``).
    :param a_terms: términos del equipo visitante para el fallback.
    :param b_terms: términos del equipo local para el fallback.
    :param timeout: segundos de timeout para el fallback.
    :returns: lista de :class:`Stream` rankeados (puede estar vacía).
    """
    if event_id:
        try:
            streams = plugin.resolve_streams(event_id)
        except Exception as exc:  # noqa: BLE001 - degradar, no romper
            log.warning("resolve_streams: fallo del plugin para %s (%s)", event_id, exc)
            streams = []
        if streams:
            return streams
        log.info(
            "resolve_streams: el plugin no devolvió fuentes para %s; "
            "se usa el fallback del scraper (R5.2).", event_id,
        )
    else:
        log.info(
            "resolve_streams: tarjeta sin event_id; se usa el fallback del "
            "scraper (R5.2)."
        )

    try:
        result = fallback_resolve(a_terms, b_terms, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - degradar, no romper
        log.warning("resolve_streams: fallo del fallback (%s)", exc)
        return []

    if result is None:
        return []
    log.info("resolve_streams: fallback del scraper resolvió un stream (R5.4).")
    return [wrap_stream_result(result)]
