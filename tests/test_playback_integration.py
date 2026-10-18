"""Integración headless de la capa de reproducción bajo demanda (Tarea 14).

Verifica, sin Kivy ni red, que las piezas puras del flujo "tocar un partido →
resolver → elegir el mejor ≤ tope → Siguiente" encajan entre sí tal y como las
cablea ``KioskRoot._resolve_and_play`` (diseño §5, Flujo B/C):

    ui.playback.resolve_streams  →  SourceSwitcher(..., max_height)
                                     ├─ current()  == pick_best (mejor ≤ tope)
                                     └─ next()      recorre el resto (R7/R9)

Son las mismas costuras testeables que usa la UI (``resolve_streams`` acepta un
plugin/fallback inyectado; ``SourceSwitcher`` recibe la lista y el tope), pero
ejercitadas juntas en vez de por separado. No sustituye la verificación en el
dispositivo (mpv/overlay/táctil), sino que confirma la lógica de selección y
salto de fuente de extremo a extremo en Python.

Ejecución:
    PYTHONPATH=. python tests/test_playback_integration.py

Cubre:
- plugin con varias fuentes rankeadas → se reproduce la mejor ≤ 1080 (R6.2/R6.3),
- "Siguiente streaming" avanza por el resto y acaba agotándose (R7.2/R7.4/R9.4),
- todas las fuentes > tope → se elige la de menor altura (R6.5),
- plugin sin fuentes → fallback del scraper, envuelto como Stream único (R5.2).
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scraper.plugin_client import Stream  # noqa: E402
from scraper.source_switcher import SourceSwitcher  # noqa: E402
from ui import playback  # noqa: E402

MAX_HEIGHT = 1080


@dataclass
class _FakeStreamResult:
    """Doble del ``StreamResult`` de scraper/ondemand (sólo url + referrer)."""

    url: str
    referrer: str


class _FakePlugin:
    """Doble de PluginClient: devuelve una lista fija de streams rankeados."""

    def __init__(self, streams=None) -> None:
        self._streams = streams or []

    def resolve_streams(self, event_id: str):
        return list(self._streams)


def _stream(source: str, height: int | None) -> Stream:
    return Stream(
        url=f"https://example/{source}.m3u8",
        source=source,
        resolution_height=height,
        referer="https://ref/",
        origin=None,
        label=f"{source} {height}",
    )


def _no_fallback(a_terms, b_terms, timeout):
    """Fallback que nunca resuelve (el plugin manda en estos casos)."""
    return None


def _resolve_switcher(plugin, event_id, fallback):
    """Reproduce el cableado de ``KioskRoot._resolve_and_play`` (parte pura).

    Resuelve los streams y, si hay, construye el ``SourceSwitcher`` con el mismo
    tope que usa la UI. Devuelve el switcher (o ``None`` si no hay fuentes).
    """
    streams = playback.resolve_streams(
        plugin, event_id, fallback, ["away"], ["home"], 30
    )
    if not streams:
        return None
    return SourceSwitcher(streams, max_height=MAX_HEIGHT)


class ResolveAndSwitchTests(unittest.TestCase):
    def test_elige_el_mejor_bajo_tope_y_siguiente_recorre_el_resto(self) -> None:
        # Rankeados mejor→peor como los entrega el plugin; la 1ª excede el tope.
        plugin = _FakePlugin(streams=[
            _stream("UHD", 2160),   # > 1080: no debe elegirse primero (R6.3)
            _stream("FHD", 1080),   # mejor candidato ≤ tope
            _stream("HD", 720),
        ])
        switcher = _resolve_switcher(plugin, "nuvio_sport_1", _no_fallback)
        self.assertIsNotNone(switcher)

        # current() == pick_best: la mejor fuente ≤ 1080 (R6.2/R6.3).
        best = switcher.current()
        self.assertEqual(best.source, "FHD")
        self.assertEqual(best.resolution_height, 1080)

        # "Siguiente streaming" avanza por el resto respetando el tope: primero
        # la otra ≤ tope (720), luego la que lo excede queda al final (R7.2/R7.5).
        self.assertTrue(switcher.has_next())
        self.assertEqual(switcher.next().source, "HD")
        self.assertEqual(switcher.next().source, "UHD")  # > tope, último recurso

        # Agotadas: next() devuelve None y no da la vuelta (R7.4/R9.4).
        self.assertFalse(switcher.has_next())
        self.assertIsNone(switcher.next())
        self.assertIsNone(switcher.next())

    def test_indicador_fuente_n_de_m_avanza(self) -> None:
        plugin = _FakePlugin(streams=[_stream("A", 1080), _stream("B", 720)])
        switcher = _resolve_switcher(plugin, "nuvio_sport_1", _no_fallback)
        self.assertEqual(switcher.index(), (1, 2))  # "fuente 1 de 2" (R9.5)
        switcher.next()
        self.assertEqual(switcher.index(), (2, 2))

    def test_todas_exceden_el_tope_elige_la_de_menor_altura(self) -> None:
        plugin = _FakePlugin(streams=[
            _stream("UHD", 2160),
            _stream("QHD", 1440),
        ])
        switcher = _resolve_switcher(plugin, "nuvio_sport_1", _no_fallback)
        # Ninguna ≤ 1080: se usa la de menor altura (R6.5).
        self.assertEqual(switcher.current().resolution_height, 1440)

    def test_plugin_sin_fuentes_cae_al_fallback_como_stream_unico(self) -> None:
        plugin = _FakePlugin(streams=[])

        def _fallback(a_terms, b_terms, timeout):
            return _FakeStreamResult("https://fb/s.m3u8", "https://fb/")

        switcher = _resolve_switcher(plugin, "nuvio_sport_1", _fallback)
        self.assertIsNotNone(switcher)
        best = switcher.current()
        # El fallback se envuelve como Stream único (altura desconocida) (R5.2).
        self.assertEqual(best.url, "https://fb/s.m3u8")
        self.assertEqual(best.source, "streameast")
        self.assertIsNone(best.resolution_height)
        self.assertFalse(switcher.has_next())

    def test_sin_plugin_ni_fallback_no_hay_switcher(self) -> None:
        switcher = _resolve_switcher(_FakePlugin(streams=[]), None, _no_fallback)
        self.assertIsNone(switcher)


if __name__ == "__main__":
    unittest.main(verbosity=2)
