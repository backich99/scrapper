"""Tests de la construcción de argumentos de mpv (player/mpv_player.py, Tarea 8).

Usa unittest (stdlib) para no añadir dependencias ni arrancar mpv: se verifica
el comando que ``MpvPlayer._build_cmd`` produce, sin lanzar ningún proceso.

Ejecución:
    PYTHONPATH=. python tests/test_mpv_player.py
(También funciona bajo pytest si estuviera instalado.)

Cubre (R6.4):
- la cabecera Origin aparece como ``--http-header-fields=Origin: <origin>``
  cuando el stream trae ``origin``,
- no aparece ninguna opción Origin cuando no se indica,
- el argumento ``origin`` de ``play`` tiene prioridad sobre el valor por
  defecto de la instancia,
- el soporte de Origin es ortogonal al ``referrer`` ya existente.
"""

from __future__ import annotations

import os
import sys
import unittest

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from player.mpv_player import MpvPlayer  # noqa: E402


def _origin_args(cmd: list[str]) -> list[str]:
    """Argumentos de cmd relacionados con la cabecera Origin."""
    return [a for a in cmd if a.startswith("--http-header-fields=")]


class BuildCmdOriginTest(unittest.TestCase):
    def test_origin_presente_cuando_se_indica(self):
        player = MpvPlayer(origin="")  # sin defecto de instancia
        cmd = player._build_cmd("http://cdn/stream.m3u8", origin="https://example.com")
        self.assertIn("--http-header-fields=Origin: https://example.com", cmd)

    def test_origin_ausente_cuando_no_se_indica(self):
        player = MpvPlayer(origin="")
        cmd = player._build_cmd("http://cdn/stream.m3u8")
        self.assertEqual(_origin_args(cmd), [])

    def test_arg_de_play_tiene_prioridad_sobre_el_defecto(self):
        player = MpvPlayer(origin="https://default.example")
        cmd = player._build_cmd("http://cdn/stream.m3u8", origin="https://override.example")
        self.assertIn("--http-header-fields=Origin: https://override.example", cmd)
        self.assertNotIn("--http-header-fields=Origin: https://default.example", cmd)

    def test_defecto_de_instancia_cuando_no_hay_arg(self):
        player = MpvPlayer(origin="https://default.example")
        cmd = player._build_cmd("http://cdn/stream.m3u8")
        self.assertIn("--http-header-fields=Origin: https://default.example", cmd)

    def test_origin_y_referrer_son_independientes(self):
        player = MpvPlayer(origin="", referrer="")
        cmd = player._build_cmd(
            "http://cdn/stream.m3u8",
            referrer="https://ref.example",
            origin="https://org.example",
        )
        self.assertIn("--referrer=https://ref.example", cmd)
        self.assertIn("--http-header-fields=Origin: https://org.example", cmd)


if __name__ == "__main__":
    unittest.main()
