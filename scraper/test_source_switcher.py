"""Tests del recorrido de fuentes rankeadas (Tarea 4).

Usa unittest (stdlib) para no añadir dependencias. Cubre:
- lista vacía → current/next None, has_next False, index (0, 0),
- selección inicial vía pick_best (mejor stream ≤ tope),
- next() avanza en orden rankeado y None al agotarse (R7.4/R9.4),
- next() no da la vuelta tras agotarse,
- pick_best elige un stream que no es el primero → va primero y next() sigue,
- streams que exceden el tope quedan al final por altura ascendente (R7.5),
- altura desconocida tratada como candidato aceptable,
- index() 1-based y "fuente N de M" (R9.5).
"""

from __future__ import annotations

import unittest

from scraper.source_switcher import SourceSwitcher
from scraper.plugin_client import Stream


def _stream(source: str, height: int | None) -> Stream:
    """Stream mínimo para probar el switcher (solo importan altura y source)."""
    return Stream(
        url=f"http://example.test/{source}.m3u8",
        source=source,
        resolution_height=height,
        referer=None,
        origin=None,
        label=f"{source} {height}",
    )


class SourceSwitcherTests(unittest.TestCase):
    def test_lista_vacia(self) -> None:
        sw = SourceSwitcher([], 1080)
        self.assertIsNone(sw.current())
        self.assertFalse(sw.has_next())
        self.assertIsNone(sw.next())
        self.assertEqual(sw.index(), (0, 0))

    def test_seleccion_inicial_es_mejor_bajo_tope(self) -> None:
        # Todos cumplen el tope: el primero rankeado es el mejor (igual que
        # pick_best). current() lo devuelve.
        streams = [_stream("A", 1080), _stream("B", 720), _stream("C", 480)]
        sw = SourceSwitcher(streams, 1080)
        self.assertIs(sw.current(), streams[0])
        self.assertEqual(sw.index(), (1, 3))

    def test_next_avanza_en_orden_y_se_agota(self) -> None:
        streams = [_stream("A", 1080), _stream("B", 720), _stream("C", 480)]
        sw = SourceSwitcher(streams, 1080)
        self.assertTrue(sw.has_next())
        self.assertIs(sw.next(), streams[1])
        self.assertEqual(sw.index(), (2, 3))
        self.assertIs(sw.next(), streams[2])
        self.assertEqual(sw.index(), (3, 3))
        self.assertFalse(sw.has_next())
        # Agotadas: next() devuelve None y current() queda en None.
        self.assertIsNone(sw.next())
        self.assertIsNone(sw.current())

    def test_next_no_da_la_vuelta_tras_agotarse(self) -> None:
        streams = [_stream("A", 720)]
        sw = SourceSwitcher(streams, 1080)
        self.assertIs(sw.current(), streams[0])
        self.assertIsNone(sw.next())
        # Llamadas sucesivas siguen devolviendo None (no reinicia).
        self.assertIsNone(sw.next())
        self.assertIsNone(sw.current())
        self.assertEqual(sw.index(), (1, 1))

    def test_pick_best_no_primero_va_primero_y_next_sigue(self) -> None:
        # El primero excede el tope (1440 > 1080); pick_best elige el 720 (B).
        # B debe ser la posición actual inicial, y next() recorre el resto en
        # orden rankeado: C (480), luego el que excede el tope (A, 1440) al final.
        a = _stream("A", 1440)
        b = _stream("B", 720)
        c = _stream("C", 480)
        sw = SourceSwitcher([a, b, c], 1080)
        self.assertIs(sw.current(), b)      # mejor bajo tope
        self.assertEqual(sw.index(), (1, 3))
        self.assertIs(sw.next(), c)         # siguiente candidato ≤ tope
        self.assertIs(sw.next(), a)         # el que excede el tope, al final
        self.assertIsNone(sw.next())        # agotadas

    def test_sobre_tope_al_final_por_altura_ascendente(self) -> None:
        # Todos exceden 1080: pick_best devuelve el de menor altura (1200).
        # El recorrido coloca el resto por altura ascendente tras él.
        a = _stream("A", 2160)
        b = _stream("B", 1200)
        c = _stream("C", 1600)
        sw = SourceSwitcher([a, b, c], 1080)
        self.assertIs(sw.current(), b)      # menor altura (R6.5)
        self.assertIs(sw.next(), c)         # 1600
        self.assertIs(sw.next(), a)         # 2160
        self.assertIsNone(sw.next())

    def test_altura_desconocida_es_candidato(self) -> None:
        # El primero excede el tope; el segundo tiene altura None y se elige
        # como candidato aceptable. next() sigue con el resto.
        a = _stream("A", 1440)
        b = _stream("B", None)
        c = _stream("C", 720)
        sw = SourceSwitcher([a, b, c], 1080)
        self.assertIs(sw.current(), b)
        self.assertIs(sw.next(), c)         # ≤ tope, orden original
        self.assertIs(sw.next(), a)         # excede el tope, al final
        self.assertIsNone(sw.next())

    def test_index_1_based(self) -> None:
        streams = [_stream("A", 720), _stream("B", 480)]
        sw = SourceSwitcher(streams, 1080)
        self.assertEqual(sw.index(), (1, 2))
        sw.next()
        self.assertEqual(sw.index(), (2, 2))

    def test_un_solo_stream_no_tiene_siguiente(self) -> None:
        streams = [_stream("A", 1080)]
        sw = SourceSwitcher(streams, 1080)
        self.assertFalse(sw.has_next())
        self.assertEqual(sw.index(), (1, 1))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
