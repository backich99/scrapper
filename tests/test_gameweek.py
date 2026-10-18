"""Tests de la lógica pura de la jornada en la UI (ui/gameweek.py, Tarea 6).

Usa unittest (stdlib) para no añadir dependencias ni requerir un display de
Kivy. ``ui/gameweek.py`` sólo importa ``ui/teams.py`` (que a su vez sólo usa
``re``), así que estas pruebas corren sin arrancar la app.

Ejecución:
    PYTHONPATH=. python tests/test_gameweek.py
(También funciona bajo pytest si estuviera instalado.)

Cubre:
- adaptación de un evento del plugin a un objeto tipo ``AgendaGame`` (R4.1, R4.3),
- mapeo de estado live/upcoming/unavailable → in/pre (R4.2),
- orden híbrido: en vivo y de hoy arriba, próximos debajo (R4.2),
- seguimiento de "no disponible" entre refrescos (R4.4).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ui.gameweek import (  # noqa: E402
    NO_MORE_SOURCES_TEXT,
    UNAVAILABLE_TEXT,
    PluginGame,
    UnavailableTracker,
    adapt_plugin_event,
    next_source_enabled,
    order_gameweek,
    source_label,
)


def _event(
    event_id: str = "nuvio_sport_1",
    title: str = "Kansas City Chiefs vs Philadelphia Eagles",
    start_time: str | None = "2026-10-17T18:30:00Z",
    status: str = "upcoming",
) -> dict:
    """Evento del plugin mínimo en el esquema del proyecto."""
    return {
        "id": event_id,
        "title": title,
        "league": "NFL",
        "sport": "american_football",
        "start_time": start_time,
        "stream_url": None,
        "referrer": "",
        "source": "live-sport-plugin",
        "status": status,
    }


class AdaptPluginEventTests(unittest.TestCase):
    def test_adapta_nombres_y_abreviaturas_desde_el_titulo(self) -> None:
        game = adapt_plugin_event(_event())
        self.assertIsNotNone(game)
        assert game is not None  # para el type checker
        # parse_matchup separa "Away vs Home".
        self.assertEqual(game.away_name, "Kansas City Chiefs")
        self.assertEqual(game.home_name, "Philadelphia Eagles")
        self.assertEqual(game.away_abbr, "KC")
        self.assertEqual(game.home_abbr, "PHI")
        self.assertEqual(game.event_id, "nuvio_sport_1")

    def test_titulo_estilo_away_vs_home(self) -> None:
        game = adapt_plugin_event(_event())
        assert game is not None
        self.assertEqual(game.title, "Kansas City Chiefs vs Philadelphia Eagles")

    def test_estado_live_mapea_a_in_y_es_jugable(self) -> None:
        game = adapt_plugin_event(_event(status="live"))
        assert game is not None
        self.assertEqual(game.state, "in")
        self.assertTrue(game.is_live)
        self.assertTrue(game.is_playable())

    def test_estado_upcoming_mapea_a_pre(self) -> None:
        game = adapt_plugin_event(_event(status="upcoming"))
        assert game is not None
        self.assertEqual(game.state, "pre")
        self.assertFalse(game.is_live)
        self.assertFalse(game.is_final)

    def test_estado_unavailable_mapea_a_pre(self) -> None:
        game = adapt_plugin_event(_event(status="unavailable"))
        assert game is not None
        self.assertEqual(game.state, "pre")

    def test_parsea_start_time_iso_con_z(self) -> None:
        game = adapt_plugin_event(_event(start_time="2026-10-17T18:30:00Z"))
        assert game is not None
        self.assertIsNotNone(game.start)
        assert game.start is not None
        self.assertEqual(game.start.tzinfo, dt.timezone.utc)
        self.assertEqual(game.start.hour, 18)

    def test_start_time_nulo_queda_none_y_no_jugable(self) -> None:
        game = adapt_plugin_event(_event(start_time=None, status="upcoming"))
        assert game is not None
        self.assertIsNone(game.start)
        self.assertFalse(game.is_playable())

    def test_sin_id_devuelve_none(self) -> None:
        ev = _event()
        ev["id"] = ""
        self.assertIsNone(adapt_plugin_event(ev))

    def test_titulo_no_separable_devuelve_none(self) -> None:
        self.assertIsNone(adapt_plugin_event(_event(title="Partido suelto")))

    def test_no_dict_devuelve_none(self) -> None:
        self.assertIsNone(adapt_plugin_event("no soy un dict"))  # type: ignore[arg-type]

    def test_jugable_dentro_de_la_ventana_previa(self) -> None:
        # 10 min antes del kickoff → jugable (ventana de 15 min).
        now = dt.datetime(2026, 10, 17, 18, 20, tzinfo=dt.timezone.utc)
        game = adapt_plugin_event(_event(start_time="2026-10-17T18:30:00Z"))
        assert game is not None
        self.assertTrue(game.is_playable(now=now))

    def test_no_jugable_demasiado_pronto(self) -> None:
        now = dt.datetime(2026, 10, 17, 17, 0, tzinfo=dt.timezone.utc)
        game = adapt_plugin_event(_event(start_time="2026-10-17T18:30:00Z"))
        assert game is not None
        self.assertFalse(game.is_playable(now=now))


class OrderGameweekTests(unittest.TestCase):
    def _game(self, name, state, start):
        return PluginGame(
            event_id=name,
            home_name=f"{name} Home",
            away_name=f"{name} Away",
            home_abbr="HOM",
            away_abbr="AWY",
            start=start,
            state=state,
        )

    def test_en_vivo_y_hoy_arriba_proximos_debajo(self) -> None:
        today = dt.date(2026, 10, 17)
        tz = dt.timezone.utc
        live = self._game("live", "in", dt.datetime(2026, 10, 17, 18, 0, tzinfo=tz))
        hoy = self._game("hoy", "pre", dt.datetime(2026, 10, 17, 20, 0, tzinfo=tz))
        manana = self._game("mañana", "pre", dt.datetime(2026, 10, 19, 20, 0, tzinfo=tz))
        # Orden de entrada deliberadamente "al revés".
        ordered = order_gameweek([manana, hoy, live], today=today)
        self.assertEqual([g.event_id for g in ordered], ["live", "hoy", "mañana"])

    def test_dentro_del_grupo_ordena_por_hora(self) -> None:
        today = dt.date(2026, 10, 17)
        tz = dt.timezone.utc
        tarde = self._game("tarde", "pre", dt.datetime(2026, 10, 17, 23, 0, tzinfo=tz))
        temprano = self._game("temprano", "pre", dt.datetime(2026, 10, 17, 17, 0, tzinfo=tz))
        ordered = order_gameweek([tarde, temprano], today=today)
        self.assertEqual([g.event_id for g in ordered], ["temprano", "tarde"])

    def test_sin_hora_va_al_final_de_su_grupo(self) -> None:
        today = dt.date(2026, 10, 17)
        tz = dt.timezone.utc
        futuro = self._game("futuro", "pre", dt.datetime(2026, 10, 20, 20, 0, tzinfo=tz))
        sin_hora = self._game("sin_hora", "pre", None)
        ordered = order_gameweek([sin_hora, futuro], today=today)
        self.assertEqual([g.event_id for g in ordered], ["futuro", "sin_hora"])

    def test_no_muta_la_lista_de_entrada(self) -> None:
        today = dt.date(2026, 10, 17)
        tz = dt.timezone.utc
        a = self._game("a", "pre", dt.datetime(2026, 10, 20, 20, 0, tzinfo=tz))
        b = self._game("b", "in", dt.datetime(2026, 10, 17, 20, 0, tzinfo=tz))
        original = [a, b]
        order_gameweek(original, today=today)
        self.assertIs(original[0], a)
        self.assertIs(original[1], b)


class UnavailableTrackerTests(unittest.TestCase):
    def test_marca_y_consulta(self) -> None:
        tracker = UnavailableTracker()
        self.assertFalse(tracker.is_unavailable("x"))
        tracker.mark("x")
        self.assertTrue(tracker.is_unavailable("x"))

    def test_ignora_none(self) -> None:
        tracker = UnavailableTracker()
        tracker.mark(None)
        self.assertFalse(tracker.is_unavailable(None))

    def test_clear_olvida_la_marca(self) -> None:
        tracker = UnavailableTracker()
        tracker.mark("x")
        tracker.clear("x")
        self.assertFalse(tracker.is_unavailable("x"))

    def test_persiste_entre_consultas(self) -> None:
        # Simula dos refrescos: la marca sobrevive porque el tracker es el mismo.
        tracker = UnavailableTracker()
        tracker.mark("nuvio_sport_7")
        self.assertTrue(tracker.is_unavailable("nuvio_sport_7"))
        self.assertTrue(tracker.is_unavailable("nuvio_sport_7"))


class ConstantsTests(unittest.TestCase):
    def test_texto_no_disponible_definido(self) -> None:
        self.assertTrue(UNAVAILABLE_TEXT)


class SourceLabelTests(unittest.TestCase):
    """Indicador "fuente N de M" del overlay de reproducción (R9.5)."""

    def test_formatea_actual_y_total(self) -> None:
        self.assertEqual(source_label((1, 5)), "Fuente 1 de 5")
        self.assertEqual(source_label((2, 5)), "Fuente 2 de 5")

    def test_fuente_unica(self) -> None:
        self.assertEqual(source_label((1, 1)), "Fuente 1 de 1")

    def test_sin_fuentes_devuelve_vacio(self) -> None:
        # (0, 0) de un SourceSwitcher con lista vacía → sin indicador.
        self.assertEqual(source_label((0, 0)), "")

    def test_agotadas_actual_igual_al_total(self) -> None:
        # Tras agotar las fuentes, index() devuelve (total, total).
        self.assertEqual(source_label((5, 5)), "Fuente 5 de 5")


class NextSourceEnabledTests(unittest.TestCase):
    """Habilitación del botón "Siguiente streaming" según has_next (R9.4)."""

    def test_habilitado_cuando_hay_siguiente(self) -> None:
        self.assertTrue(next_source_enabled(True))

    def test_deshabilitado_cuando_no_hay_siguiente(self) -> None:
        self.assertFalse(next_source_enabled(False))

    def test_texto_sin_fuentes_definido(self) -> None:
        self.assertTrue(NO_MORE_SOURCES_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
