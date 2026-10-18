"""Tests de la lógica pura de resolución bajo demanda (ui/playback.py, Tarea 7).

Usa unittest (stdlib) para no añadir dependencias ni requerir un display de
Kivy. ``ui/playback.py`` sólo importa ``scraper/plugin_client.py`` (requests +
stdlib), así que estas pruebas corren sin arrancar la app ni la red: el plugin
y el fallback se sustituyen por dobles de prueba simples.

Ejecución:
    PYTHONPATH=. python tests/test_playback.py
(También funciona bajo pytest si estuviera instalado.)

Cubre:
- envoltura de un ``StreamResult`` del fallback en un ``Stream`` único (R5.2),
- preferencia del plugin cuando devuelve fuentes (R6.1),
- caída al fallback cuando el plugin no devuelve nada (R5.2),
- ir directo al fallback cuando no hay ``event_id``,
- degradación: excepciones del plugin o del fallback no rompen (devuelven []).
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scraper.plugin_client import Stream  # noqa: E402
from ui import playback  # noqa: E402


@dataclass
class _FakeStreamResult:
    """Doble del ``StreamResult`` de scraper/ondemand (sólo url + referrer)."""

    url: str
    referrer: str


class _FakePlugin:
    """Doble de PluginClient: devuelve una lista fija o lanza si se le pide."""

    def __init__(self, streams=None, raises: bool = False) -> None:
        self._streams = streams or []
        self._raises = raises
        self.calls: list[str] = []

    def resolve_streams(self, event_id: str):
        self.calls.append(event_id)
        if self._raises:
            raise RuntimeError("boom")
        return list(self._streams)


def _stream(source: str = "DaddyLive", height: int | None = 1080) -> Stream:
    return Stream(
        url=f"https://example/{source}.m3u8",
        source=source,
        resolution_height=height,
        referer="https://ref/",
        origin=None,
        label=source,
    )


class WrapStreamResultTests(unittest.TestCase):
    def test_envuelve_url_y_referrer(self) -> None:
        stream = playback.wrap_stream_result(_FakeStreamResult("http://x/s.m3u8", "http://x/"))
        self.assertEqual(stream.url, "http://x/s.m3u8")
        self.assertEqual(stream.referer, "http://x/")
        self.assertIsNone(stream.origin)
        self.assertIsNone(stream.resolution_height)
        self.assertEqual(stream.source, "streameast")
        self.assertEqual(stream.label, "StreamEast")

    def test_referrer_vacio_se_normaliza_a_none(self) -> None:
        stream = playback.wrap_stream_result(_FakeStreamResult("http://x/s.m3u8", ""))
        self.assertIsNone(stream.referer)


class ResolveStreamsTests(unittest.TestCase):
    def _fallback(self, result):
        """Fabrica un callable fallback que registra sus llamadas."""
        calls: list[dict] = []

        def _fn(a_terms, b_terms, timeout):
            calls.append({"a": a_terms, "b": b_terms, "timeout": timeout})
            return result

        _fn.calls = calls  # type: ignore[attr-defined]
        return _fn

    def test_usa_el_plugin_cuando_devuelve_fuentes(self) -> None:
        plugin = _FakePlugin(streams=[_stream("A"), _stream("B")])
        fb = self._fallback(_FakeStreamResult("http://fb/", "http://fb/"))
        out = playback.resolve_streams(plugin, "nuvio_sport_1", fb, ["a"], ["b"], 30)
        self.assertEqual([s.source for s in out], ["A", "B"])
        self.assertEqual(plugin.calls, ["nuvio_sport_1"])
        # El fallback no debe invocarse si el plugin ya trajo fuentes.
        self.assertEqual(fb.calls, [])  # type: ignore[attr-defined]

    def test_cae_al_fallback_cuando_el_plugin_no_devuelve_nada(self) -> None:
        plugin = _FakePlugin(streams=[])
        fb = self._fallback(_FakeStreamResult("http://fb/s.m3u8", "http://fb/"))
        out = playback.resolve_streams(plugin, "nuvio_sport_1", fb, ["a"], ["b"], 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].url, "http://fb/s.m3u8")
        self.assertEqual(out[0].source, "streameast")
        # El timeout se propaga al fallback.
        self.assertEqual(fb.calls[0]["timeout"], 25)  # type: ignore[attr-defined]

    def test_sin_event_id_va_directo_al_fallback(self) -> None:
        plugin = _FakePlugin(streams=[_stream("A")])
        fb = self._fallback(_FakeStreamResult("http://fb/s.m3u8", "http://fb/"))
        out = playback.resolve_streams(plugin, None, fb, ["a"], ["b"], 30)
        # No se consulta el plugin sin event_id.
        self.assertEqual(plugin.calls, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].url, "http://fb/s.m3u8")

    def test_fallback_vacio_devuelve_lista_vacia(self) -> None:
        plugin = _FakePlugin(streams=[])
        fb = self._fallback(None)
        out = playback.resolve_streams(plugin, "nuvio_sport_1", fb, ["a"], ["b"], 30)
        self.assertEqual(out, [])

    def test_excepcion_del_plugin_cae_al_fallback(self) -> None:
        plugin = _FakePlugin(raises=True)
        fb = self._fallback(_FakeStreamResult("http://fb/s.m3u8", "http://fb/"))
        out = playback.resolve_streams(plugin, "nuvio_sport_1", fb, ["a"], ["b"], 30)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].url, "http://fb/s.m3u8")

    def test_excepcion_del_fallback_devuelve_lista_vacia(self) -> None:
        plugin = _FakePlugin(streams=[])

        def _boom(a_terms, b_terms, timeout):
            raise RuntimeError("boom")

        out = playback.resolve_streams(plugin, "nuvio_sport_1", _boom, ["a"], ["b"], 30)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
