"""Lista rankeada de fuentes y salto entre ellas sin re-resolver (R7, R9).

``SourceSwitcher`` mantiene la lista de :class:`~scraper.plugin_client.Stream`
ya resuelta de un partido y permite avanzar a la siguiente fuente cuando la
actual se pausa desde el origen, **sin volver a consultar el plugin**
(Requisitos 7.1, 7.2). La selección inicial reutiliza
:func:`~scraper.resolution_policy.pick_best`, de modo que la primera fuente es
el mejor stream dentro del tope configurado; a partir de ahí ``next()`` recorre
el resto respetando el mismo tope y devuelve ``None`` cuando se agotan
(Requisitos 7.4, 9.4).

Decisión de diseño — orden de recorrido
---------------------------------------
``pick_best`` puede elegir una fuente que **no** es la primera de la lista (p. ej.
si la primera excede el tope de resolución). Para que ``next()`` siga teniendo
sentido tras esa elección, el switcher construye una **lista ordenada de
candidatos** así:

1. El stream elegido por ``pick_best`` va primero (posición actual inicial).
2. El resto sigue en su **orden rankeado original** (mejor→peor tal como lo
   entrega el plugin), omitiendo el ya elegido.

El recorrido respeta el mismo tope que ``pick_best`` (Requisito 7.5):

- Los streams con altura conocida que **exceden** el tope se dejan para el
  final, ordenados por altura ascendente (igual criterio que ``pick_best`` al
  no haber candidatos ≤ tope: Requisito 6.5). Así "siguiente streaming" prioriza
  siempre lo reproducible dentro del límite y solo cae en lo que lo excede como
  último recurso, de menor a mayor altura.
- La altura desconocida (``None``) no bloquea: cuenta como candidato aceptable
  de baja prioridad, igual que en ``pick_best`` (diseño §2).

El resultado es un orden total y estable sobre el que ``current()``/``next()``
avanzan con un simple índice.
"""

from __future__ import annotations

import logging

from scraper.plugin_client import Stream
from scraper.resolution_policy import pick_best

log = logging.getLogger(__name__)


class SourceSwitcher:
    """Recorre las fuentes rankeadas de un partido respetando el tope.

    :param streams: Streams ya resueltos y rankeados mejor→peor (como los
        entrega ``PluginClient.resolve_streams``).
    :param max_height: Tope de altura en píxeles (de ``playback.max_resolution``,
        default 1080). Límite **blando**, igual que en ``pick_best``: la altura
        desconocida no bloquea y, si ninguna fuente lo cumple, se usan igualmente
        las que lo exceden (de menor a mayor altura).
    """

    def __init__(self, streams: list[Stream], max_height: int) -> None:
        self.max_height = max_height
        self._ordered = self._build_order(streams, max_height)
        # Índice de la posición actual dentro de ``_ordered``. Empieza en 0 (la
        # elección de pick_best) salvo lista vacía, donde no hay posición válida.
        self._pos = 0 if self._ordered else -1

    @staticmethod
    def _build_order(streams: list[Stream], max_height: int) -> list[Stream]:
        """Ordena las fuentes para el recorrido (ver docstring del módulo).

        Devuelve una lista con la elección de ``pick_best`` primero, luego el
        resto de candidatos ≤ tope (o altura desconocida) en su orden original,
        y por último los que exceden el tope ordenados por altura ascendente.
        """
        if not streams:
            return []

        best = pick_best(streams, max_height)

        within: list[Stream] = []   # altura None o <= tope (candidatos directos)
        over: list[Stream] = []     # altura conocida > tope (último recurso)
        for stream in streams:
            if stream is best:
                continue  # el elegido se antepone aparte
            height = stream.resolution_height
            if height is None or height <= max_height:
                within.append(stream)
            else:
                over.append(stream)

        # Los que exceden el tope van al final, de menor a mayor altura (mismo
        # criterio que pick_best cuando no hay candidatos: Requisito 6.5).
        over.sort(key=lambda s: s.resolution_height)  # type: ignore[arg-type,return-value]

        ordered: list[Stream] = []
        if best is not None:
            ordered.append(best)
        ordered.extend(within)
        ordered.extend(over)
        return ordered

    def current(self) -> Stream | None:
        """Devuelve la fuente en la posición actual, o ``None`` si no hay.

        ``None`` ocurre cuando la lista estaba vacía o cuando ``next()`` ya
        agotó todas las fuentes.
        """
        if 0 <= self._pos < len(self._ordered):
            return self._ordered[self._pos]
        return None

    def has_next(self) -> bool:
        """Indica si queda al menos una fuente después de la actual."""
        return self._pos + 1 < len(self._ordered)

    def next(self) -> Stream | None:
        """Avanza a la siguiente fuente y la devuelve.

        Devuelve ``None`` cuando se agotan las fuentes (Requisitos 7.4 y 9.4);
        en ese caso la posición queda al final y ``current()`` devolverá ``None``,
        de modo que llamadas sucesivas siguen devolviendo ``None`` (no se da la
        vuelta a la lista).
        """
        if not self.has_next():
            # Marcamos agotamiento moviendo la posición más allá del último.
            self._pos = len(self._ordered)
            log.debug("source_switcher: no quedan más fuentes")
            return None
        self._pos += 1
        return self._ordered[self._pos]

    def index(self) -> tuple[int, int]:
        """Posición actual (1-based) y total, para el indicador "fuente N de M".

        Devuelve ``(actual, total)`` con ``actual`` en base 1 (Requisito 9.5).
        Si la lista está vacía devuelve ``(0, 0)``. Si las fuentes se agotaron
        (``current()`` es ``None``), ``actual`` se fija en el total (todas vistas).
        """
        total = len(self._ordered)
        if total == 0:
            return (0, 0)
        if self._pos >= total:
            return (total, total)
        return (self._pos + 1, total)
