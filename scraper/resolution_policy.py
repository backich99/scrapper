"""Política de selección del mejor stream dentro de un tope de resolución.

Esta política decide qué :class:`~scraper.plugin_client.Stream` reproducir de
entre los que entrega ``PluginClient.resolve_streams`` (ya rankeados mejor→peor).
El tope de altura (``max_height``, de ``config/kiosk.yaml`` → ``playback.max_resolution``,
default 1080) es un límite **blando**: la resolución es best-effort y a menudo el
plugin la desconoce (``Stream.resolution_height`` ``None``), en cuyo caso el
stream no se descarta sino que se trata como candidato de baja prioridad
(Requisitos 6.2, 6.3 y 6.5; diseño §2).

La política opera sobre ``Stream.resolution_height`` directamente: la altura ya
se parsea al resolver (``_parse_resolution_height`` en ``plugin_client``), así
que aquí no se re-parsea nada.
"""

from __future__ import annotations

import logging

from scraper.plugin_client import Stream

log = logging.getLogger(__name__)


def pick_best(streams: list[Stream], max_height: int) -> Stream | None:
    """Elige el mejor stream cuya altura no exceda ``max_height``.

    Como ``streams`` ya viene ordenado por calidad+velocidad (mejor primero), la
    selección se reduce a devolver el primer candidato aceptable:

    1. **Candidatos**: streams con ``resolution_height <= max_height`` o con
       altura desconocida (``None``). La altura desconocida no bloquea: se trata
       como aceptable de baja prioridad (Requisito 6.2; diseño §2).
    2. Como ya vienen ordenados, se devuelve el **primer** candidato.
    3. Si **ningún** stream cumple el tope (todas las alturas conocidas lo
       exceden), se devuelve el de **menor** altura, para seguir reproduciendo
       algo antes de declarar el partido no reproducible (Requisito 6.5).
    4. Si la lista está vacía, se devuelve ``None``.

    :param streams: Streams rankeados mejor→peor (como los entrega el plugin).
    :param max_height: Tope de altura en píxeles (p. ej. 1080). Blando.
    :returns: El :class:`Stream` elegido, o ``None`` si la lista está vacía.
    """
    if not streams:
        return None

    for stream in streams:
        height = stream.resolution_height
        if height is None or height <= max_height:
            if height is None:
                log.debug(
                    "pick_best: elegido %r con altura desconocida (tope %dp)",
                    stream.source, max_height,
                )
            return stream

    # Ningún stream cumple el tope: todas las alturas son conocidas y mayores
    # que max_height. Devolvemos el de menor altura (Requisito 6.5).
    best = min(streams, key=lambda s: s.resolution_height)  # type: ignore[arg-type,return-value]
    log.info(
        "pick_best: ningún stream <= %dp; se usa el de menor altura (%sp)",
        max_height, best.resolution_height,
    )
    return best
