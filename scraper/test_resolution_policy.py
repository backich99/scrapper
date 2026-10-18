"""Tests de la política de selección del mejor stream (Tarea 3).

Usa unittest (stdlib) para no añadir dependencias. Cubre:
- lista vacía → None,
- selección del primero cuando cumple el tope (ya rankeado),
- salto del que excede el tope hasta el primer aceptable,
- altura desconocida (None) tratada como candidato aceptable,
- todas las alturas por encima del tope → la de menor altura (R6.5),
- empate: respeta el orden/el primero.
"""

from __future__ import annotations

import unittest

from scraper.resolution_policy import pick_best
from scraper.plugin_client import Stream


def _stream(source: str, height: int | None) -> Stream:
    """Stream mínimo para probar la política (solo importa altura y source)."""
    return Stream(
        url=f"http://example.test/{source}.m3u8",
        source=source,
        resolution_height=height,
        referer=None,
        origin=None,
        label=f"{source} {height}",
    )


class PickBestTests(unittest.TestCase):
    def test_lista_vacia_devuelve_none(self) -> None:
        self.assertIsNone(pick_best([], 1080))

    def test_primero_cumple_tope_se_devuelve(self) -> None:
        # Ya rankeado mejor→peor; el primero cumple el tope.
        streams = [_stream("A", 1080), _stream("B", 720), _stream("C", 480)]
        self.assertIs(pick_best(streams, 1080), streams[0])

    def test_salta_los_que_exceden_hasta_el_primer_aceptable(self) -> None:
        # Los dos primeros exceden 1080; el tercero (720) es el primer candidato.
        streams = [_stream("A", 2160), _stream("B", 1440), _stream("C", 720)]
        self.assertIs(pick_best(streams, 1080), streams[2])

    def test_altura_desconocida_es_candidato_aceptable(self) -> None:
        # El primero excede el tope; el segundo tiene altura desconocida (None)
        # y debe aceptarse como candidato de baja prioridad (no bloquear).
        streams = [_stream("A", 1440), _stream("B", None), _stream("C", 720)]
        self.assertIs(pick_best(streams, 1080), streams[1])

    def test_desconocida_primera_se_prefiere_por_orden(self) -> None:
        # Altura desconocida en primera posición: como ya viene rankeada, se
        # devuelve ella (no bloquea por falta de dato).
        streams = [_stream("A", None), _stream("B", 720)]
        self.assertIs(pick_best(streams, 1080), streams[0])

    def test_todos_exceden_tope_devuelve_menor_altura(self) -> None:
        # Ningún stream cumple 1080; se elige el de menor altura (1440), no el
        # primero rankeado (2160). R6.5.
        streams = [_stream("A", 2160), _stream("B", 1440), _stream("C", 1600)]
        self.assertIs(pick_best(streams, 1080), streams[1])

    def test_todos_exceden_un_solo_stream(self) -> None:
        streams = [_stream("A", 2160)]
        self.assertIs(pick_best(streams, 1080), streams[0])

    def test_altura_igual_al_tope_es_aceptable(self) -> None:
        # Límite inclusivo: altura == tope cumple.
        streams = [_stream("A", 1080)]
        self.assertIs(pick_best(streams, 1080), streams[0])

    def test_tope_distinto_720(self) -> None:
        # Con tope 720, un 1080 inicial se salta y se toma el 720.
        streams = [_stream("A", 1080), _stream("B", 720), _stream("C", 480)]
        self.assertIs(pick_best(streams, 720), streams[1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
