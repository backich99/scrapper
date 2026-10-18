"""Tests de descarga y caché del CSV de nflverse (Tarea 5.1).

Usa unittest (stdlib) para no añadir dependencias. ``requests.get`` se mockea y
la caché se escribe en un directorio temporal. Cubre:
- descarga + parseo de filas (csv.DictReader) y escritura de la caché,
- reutilización de la caché fresca (no se vuelve a descargar),
- re-descarga cuando la caché está vencida (TTL superado),
- force=True ignora una caché fresca y vuelve a descargar,
- degradación suave: descarga falla y hay caché → se reutiliza aunque vencida,
- degradación suave: descarga falla y no hay caché → [],
- descarga HTTP no exitosa (404) tratada como fallo,
- CSV vacío o sin cabecera → [].
"""

from __future__ import annotations

import tempfile
import time as _time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import requests

from scraper import nfl_schedule
from scraper.nfl_schedule import (
    NflScheduleClient,
    ScheduleGame,
    _build_start_time,
    _parse_csv,
    _parse_gameday,
)


_CSV_HEADER = (
    "game_id,season,week,gameday,gametime,weekday,"
    "home_team,away_team,home_score,away_score"
)
_CSV_ROW_1 = "2025_01_KC_LAC,2025,1,2025-09-05,20:15,Friday,LAC,KC,,"
_CSV_ROW_2 = "2025_01_DAL_PHI,2025,1,2025-09-04,20:20,Thursday,PHI,DAL,,"
_SAMPLE_CSV = f"{_CSV_HEADER}\n{_CSV_ROW_1}\n{_CSV_ROW_2}\n"
_OTHER_CSV = f"{_CSV_HEADER}\n{_CSV_ROW_1}\n"


class _FakeResponse:
    """Respuesta mínima con la interfaz que usa _download()."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 400):
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _client(data_dir: Path, cache_ttl: int = 604800) -> NflScheduleClient:
    return NflScheduleClient(
        csv_url="https://example.test/games.csv",
        cache_ttl=cache_ttl,
        tz="America/Mexico_City",
        data_dir=data_dir,
    )


class ParseCsvTests(unittest.TestCase):
    def test_parses_rows_into_dicts(self) -> None:
        rows = _parse_csv(_SAMPLE_CSV)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["game_id"], "2025_01_KC_LAC")
        self.assertEqual(rows[0]["gameday"], "2025-09-05")
        self.assertEqual(rows[0]["gametime"], "20:15")
        self.assertEqual(rows[0]["home_team"], "LAC")
        self.assertEqual(rows[0]["away_team"], "KC")

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual(_parse_csv(""), [])

    def test_header_only_returns_empty(self) -> None:
        self.assertEqual(_parse_csv(_CSV_HEADER + "\n"), [])


class FetchRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_downloads_parses_and_writes_cache(self) -> None:
        client = _client(self.data_dir)
        with mock.patch.object(
            nfl_schedule.requests, "get",
            return_value=_FakeResponse(200, _SAMPLE_CSV),
        ) as mock_get:
            rows = client.fetch_rows()
        mock_get.assert_called_once()
        self.assertEqual(len(rows), 2)
        # La caché debe quedar escrita en data/ con el contenido descargado.
        self.assertTrue(client.cache_path.exists())
        self.assertEqual(
            client.cache_path.read_text(encoding="utf-8"), _SAMPLE_CSV
        )

    def test_reuses_fresh_cache_without_download(self) -> None:
        client = _client(self.data_dir)
        client.cache_path.write_text(_SAMPLE_CSV, encoding="utf-8")
        with mock.patch.object(nfl_schedule.requests, "get") as mock_get:
            rows = client.fetch_rows()
        mock_get.assert_not_called()
        self.assertEqual(len(rows), 2)

    def test_redownloads_when_cache_stale(self) -> None:
        # TTL de 1 segundo y mtime en el pasado → caché vencida.
        client = _client(self.data_dir, cache_ttl=1)
        client.cache_path.write_text(_OTHER_CSV, encoding="utf-8")
        old = _time.time() - 3600
        import os
        os.utime(client.cache_path, (old, old))
        with mock.patch.object(
            nfl_schedule.requests, "get",
            return_value=_FakeResponse(200, _SAMPLE_CSV),
        ) as mock_get:
            rows = client.fetch_rows()
        mock_get.assert_called_once()
        self.assertEqual(len(rows), 2)  # el CSV nuevo, no el viejo (1 fila)

    def test_force_refresh_ignores_fresh_cache(self) -> None:
        client = _client(self.data_dir)
        client.cache_path.write_text(_OTHER_CSV, encoding="utf-8")
        with mock.patch.object(
            nfl_schedule.requests, "get",
            return_value=_FakeResponse(200, _SAMPLE_CSV),
        ) as mock_get:
            rows = client.fetch_rows(force=True)
        mock_get.assert_called_once()
        self.assertEqual(len(rows), 2)

    def test_download_failure_falls_back_to_stale_cache(self) -> None:
        client = _client(self.data_dir, cache_ttl=1)
        client.cache_path.write_text(_SAMPLE_CSV, encoding="utf-8")
        old = _time.time() - 3600
        import os
        os.utime(client.cache_path, (old, old))
        with mock.patch.object(
            nfl_schedule.requests, "get",
            side_effect=requests.ConnectionError("boom"),
        ):
            rows = client.fetch_rows()
        # Degradación suave: se reutiliza la caché vencida (R11.6).
        self.assertEqual(len(rows), 2)

    def test_download_failure_without_cache_returns_empty(self) -> None:
        client = _client(self.data_dir)
        with mock.patch.object(
            nfl_schedule.requests, "get",
            side_effect=requests.Timeout("slow"),
        ):
            rows = client.fetch_rows()
        self.assertEqual(rows, [])

    def test_http_error_treated_as_failure(self) -> None:
        client = _client(self.data_dir)
        with mock.patch.object(
            nfl_schedule.requests, "get",
            return_value=_FakeResponse(404),
        ):
            rows = client.fetch_rows()
        self.assertEqual(rows, [])
        # No debe quedar caché escrita tras un 404.
        self.assertFalse(client.cache_path.exists())


# -- Tarea 5.2: jornada en curso y ScheduleGame -----------------------------

# CSV con partidos repartidos en varias jornadas martes→lunes para probar el
# filtro de ventana. La jornada que contiene el 2025-09-11 (jueves) va del
# martes 2025-09-09 al lunes 2025-09-15.
_SCHED_HEADER = (
    "game_id,season,week,gameday,gametime,weekday,"
    "home_team,away_team,home_score,away_score"
)
_SCHED_ROWS = "\n".join(
    [
        # Semana anterior (lunes 2025-09-08) → fuera de la ventana.
        "2025_01_MIN_CHI,2025,1,2025-09-08,20:15,Monday,CHI,MIN,,",
        # Dentro de la ventana (jue 11, dom 14, lun 15).
        "2025_02_WSH_GB,2025,2,2025-09-11,20:15,Thursday,GB,WSH,,",
        "2025_02_KC_PHI,2025,2,2025-09-14,16:25,Sunday,PHI,KC,,",
        "2025_02_LV_LAC,2025,2,2025-09-15,22:00,Monday,LAC,LV,,",
        # Semana siguiente (martes 2025-09-16) → fuera de la ventana.
        "2025_03_SF_SEA,2025,3,2025-09-16,20:15,Tuesday,SEA,SF,,",
        # Dentro de la ventana pero sin gametime → start_time None.
        "2025_02_NYJ_BUF,2025,2,2025-09-14,,Sunday,BUF,NYJ,,",
    ]
)
_SCHED_CSV = f"{_SCHED_HEADER}\n{_SCHED_ROWS}\n"

_MX = ZoneInfo("America/Mexico_City")
_ET = ZoneInfo("America/New_York")


class ParseGamedayTests(unittest.TestCase):
    def test_parses_iso_date(self) -> None:
        self.assertEqual(_parse_gameday("2025-09-14"), date(2025, 9, 14))

    def test_empty_or_none_returns_none(self) -> None:
        self.assertIsNone(_parse_gameday(""))
        self.assertIsNone(_parse_gameday(None))
        self.assertIsNone(_parse_gameday("   "))

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(_parse_gameday("14/09/2025"))
        self.assertIsNone(_parse_gameday("not-a-date"))


class BuildStartTimeTests(unittest.TestCase):
    def test_combines_and_converts_et_to_kiosk(self) -> None:
        # 20:15 ET el 2025-09-11; en septiembre ET es EDT (UTC-4) y CDMX es
        # CST (UTC-6, sin DST), 2 h de diferencia → 18:15.
        start = _build_start_time(date(2025, 9, 11), "20:15", _MX)
        assert start is not None
        self.assertEqual(start.hour, 18)
        self.assertEqual(start.minute, 15)
        self.assertEqual(start.date(), date(2025, 9, 11))
        # Debe quedar en la zona del kiosk.
        self.assertEqual(start.utcoffset(), datetime(
            2025, 9, 11, tzinfo=_MX).utcoffset())
        # El instante absoluto coincide con el mismo reloj en ET.
        self.assertEqual(
            start, datetime(2025, 9, 11, 20, 15, tzinfo=_ET).astimezone(_MX)
        )

    def test_missing_gametime_returns_none(self) -> None:
        self.assertIsNone(_build_start_time(date(2025, 9, 14), "", _MX))
        self.assertIsNone(_build_start_time(date(2025, 9, 14), None, _MX))

    def test_invalid_gametime_returns_none(self) -> None:
        self.assertIsNone(_build_start_time(date(2025, 9, 14), "8pm", _MX))


class GameweekGamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _client(self) -> NflScheduleClient:
        return NflScheduleClient(
            csv_url="https://example.test/games.csv",
            cache_ttl=604800,
            tz="America/Mexico_City",
            data_dir=self.data_dir,
        )

    def test_filters_to_gameweek_window(self) -> None:
        client = self._client()
        # "Hoy" = jueves 2025-09-11 (dentro de la jornada 09-09 → 09-15).
        with mock.patch.object(
            client, "fetch_rows", return_value=_parse_csv(_SCHED_CSV)
        ), mock.patch.object(
            nfl_schedule, "_today", return_value=date(2025, 9, 11)
        ):
            games = client.gameweek_games()

        ids = {g.game_id for g in games}
        self.assertEqual(
            ids,
            {
                "2025_02_WSH_GB",
                "2025_02_KC_PHI",
                "2025_02_LV_LAC",
                "2025_02_NYJ_BUF",
            },
        )
        # Las filas de la semana anterior/siguiente quedan fuera.
        self.assertNotIn("2025_01_MIN_CHI", ids)
        self.assertNotIn("2025_03_SF_SEA", ids)

    def test_builds_schedule_game_fields(self) -> None:
        client = self._client()
        with mock.patch.object(
            client, "fetch_rows", return_value=_parse_csv(_SCHED_CSV)
        ), mock.patch.object(
            nfl_schedule, "_today", return_value=date(2025, 9, 11)
        ):
            games = {g.game_id: g for g in client.gameweek_games()}

        tnf = games["2025_02_WSH_GB"]
        self.assertIsInstance(tnf, ScheduleGame)
        self.assertEqual(tnf.gameday, date(2025, 9, 11))
        self.assertEqual(tnf.home_team, "GB")
        self.assertEqual(tnf.away_team, "WSH")
        assert tnf.start_time is not None
        # 20:15 ET (EDT) → 18:15 CDMX (CST) en septiembre.
        self.assertEqual((tnf.start_time.hour, tnf.start_time.minute), (18, 15))

    def test_missing_gametime_yields_none_start_time(self) -> None:
        client = self._client()
        with mock.patch.object(
            client, "fetch_rows", return_value=_parse_csv(_SCHED_CSV)
        ), mock.patch.object(
            nfl_schedule, "_today", return_value=date(2025, 9, 11)
        ):
            games = {g.game_id: g for g in client.gameweek_games()}
        self.assertIsNone(games["2025_02_NYJ_BUF"].start_time)

    def test_window_from_monday_includes_same_gameweek(self) -> None:
        # Lunes 2025-09-15 cierra la misma jornada que arrancó el martes 09-09.
        client = self._client()
        with mock.patch.object(
            client, "fetch_rows", return_value=_parse_csv(_SCHED_CSV)
        ), mock.patch.object(
            nfl_schedule, "_today", return_value=date(2025, 9, 15)
        ):
            ids = {g.game_id for g in client.gameweek_games()}
        self.assertIn("2025_02_LV_LAC", ids)
        self.assertIn("2025_02_WSH_GB", ids)
        self.assertNotIn("2025_03_SF_SEA", ids)

    def test_empty_source_returns_empty(self) -> None:
        client = self._client()
        with mock.patch.object(client, "fetch_rows", return_value=[]):
            self.assertEqual(client.gameweek_games(), [])


# -- Tarea 5.3: enriquecimiento de eventos ----------------------------------

from scraper.nfl_schedule import (  # noqa: E402  (import tras los helpers CSV)
    _event_team_codes,
    _team_code_for_name,
    _team_display_name,
    _title_is_weak,
)


def _event(**overrides) -> dict:
    """Construye un evento con el esquema del proyecto (design.md §1)."""
    base = {
        "id": "nuvio_sport_x",
        "title": "Kansas City Chiefs vs Philadelphia Eagles",
        "league": "NFL",
        "sport": "american_football",
        "start_time": None,
        "stream_url": None,
        "referrer": "",
        "source": "live-sport-plugin",
        "status": "upcoming",
    }
    base.update(overrides)
    return base


class TeamCodeHelperTests(unittest.TestCase):
    def test_full_name_to_code(self) -> None:
        self.assertEqual(
            _team_code_for_name("Kansas City Chiefs"), "KC")
        self.assertEqual(
            _team_code_for_name("Philadelphia Eagles"), "PHI")

    def test_code_in_title_recognized(self) -> None:
        # Títulos que ya traen códigos ("KC vs PHI").
        self.assertEqual(_team_code_for_name("KC"), "KC")
        self.assertEqual(_team_code_for_name("PHI"), "PHI")

    def test_alias_codes_canonicalized(self) -> None:
        # WSH→WAS, LA→LAR y códigos antiguos.
        self.assertEqual(_team_code_for_name("WSH"), "WAS")
        self.assertEqual(_team_code_for_name("LA"), "LAR")
        self.assertEqual(_team_code_for_name("OAK"), "LV")

    def test_nickname_only(self) -> None:
        # ui/teams.team_badge hace match parcial por apodo.
        self.assertEqual(_team_code_for_name("Cowboys"), "DAL")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(_team_code_for_name("Some Random FC"))
        self.assertIsNone(_team_code_for_name(""))

    def test_event_team_codes(self) -> None:
        codes = _event_team_codes(_event())
        self.assertEqual(codes, frozenset({"KC", "PHI"}))

    def test_event_team_codes_no_vs(self) -> None:
        self.assertIsNone(_event_team_codes(_event(title="Just one team")))

    def test_display_name(self) -> None:
        self.assertEqual(_team_display_name("KC"), "Kansas City Chiefs")
        self.assertEqual(_team_display_name("WAS"), "Washington Commanders")
        self.assertEqual(_team_display_name("WSH"), "Washington Commanders")
        # Código desconocido cae a mayúsculas.
        self.assertEqual(_team_display_name("zzz"), "ZZZ")

    def test_title_is_weak(self) -> None:
        # Vacío o sin "A vs B" → débil.
        self.assertTrue(_title_is_weak(""))
        self.assertTrue(_title_is_weak(None))
        self.assertTrue(_title_is_weak("Something unparseable"))
        # Códigos escuetos → débil (merecen nombre completo).
        self.assertTrue(_title_is_weak("KC vs PHI"))
        self.assertTrue(_title_is_weak("WSH vs GB"))
        # Nombres legibles → no débil (no se tocan).
        self.assertFalse(
            _title_is_weak("Kansas City Chiefs vs Philadelphia Eagles"))
        self.assertFalse(_title_is_weak("Chiefs vs Eagles"))


class EnrichTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _client_with_gameweek(self) -> NflScheduleClient:
        client = NflScheduleClient(
            csv_url="https://example.test/games.csv",
            cache_ttl=604800,
            tz="America/Mexico_City",
            data_dir=self.data_dir,
        )
        # Inyectamos la jornada memoizada directamente para aislar enrich del
        # pipeline del CSV (ya cubierto por GameweekGamesTests).
        client._gameweek_cache = [
            ScheduleGame(
                game_id="2025_02_KC_PHI",
                gameday=date(2025, 9, 14),
                start_time=datetime(2025, 9, 14, 14, 25, tzinfo=_MX),
                home_team="PHI",
                away_team="KC",
            ),
            ScheduleGame(
                game_id="2025_02_WSH_GB",
                gameday=date(2025, 9, 11),
                start_time=datetime(2025, 9, 11, 18, 15, tzinfo=_MX),
                home_team="GB",
                away_team="WAS",
            ),
            ScheduleGame(
                game_id="2025_02_NYJ_BUF",
                gameday=date(2025, 9, 14),
                start_time=None,  # sin gametime
                home_team="BUF",
                away_team="NYJ",
            ),
        ]
        return client

    def test_fills_missing_start_time(self) -> None:
        client = self._client_with_gameweek()
        event = _event(start_time=None)
        out = client.enrich(event)
        self.assertEqual(
            out["start_time"],
            datetime(2025, 9, 14, 14, 25, tzinfo=_MX).isoformat(),
        )

    def test_does_not_overwrite_existing_start_time(self) -> None:
        client = self._client_with_gameweek()
        existing = "2025-09-14T10:00:00-06:00"
        event = _event(start_time=existing)
        out = client.enrich(event)
        self.assertEqual(out["start_time"], existing)

    def test_matches_regardless_of_home_away_order(self) -> None:
        # El título lista PHI primero; el schedule tiene PHI como local, KC
        # visitante. El emparejamiento no debe depender del orden.
        client = self._client_with_gameweek()
        event = _event(
            title="Philadelphia Eagles vs Kansas City Chiefs", start_time=None)
        out = client.enrich(event)
        self.assertEqual(
            out["start_time"],
            datetime(2025, 9, 14, 14, 25, tzinfo=_MX).isoformat(),
        )

    def test_tolerates_team_code_aliases(self) -> None:
        # Título trae WSH; el CSV usa WAS. Deben emparejar.
        client = self._client_with_gameweek()
        event = _event(title="WSH vs GB", start_time=None)
        out = client.enrich(event)
        self.assertEqual(
            out["start_time"],
            datetime(2025, 9, 11, 18, 15, tzinfo=_MX).isoformat(),
        )

    def test_improves_weak_title(self) -> None:
        client = self._client_with_gameweek()
        event = _event(title="KC vs PHI", start_time=None)
        out = client.enrich(event)
        # away (KC) vs home (PHI) con nombres completos de nflverse.
        self.assertEqual(
            out["title"], "Kansas City Chiefs vs Philadelphia Eagles")

    def test_does_not_overwrite_good_title(self) -> None:
        client = self._client_with_gameweek()
        good = "Kansas City Chiefs vs Philadelphia Eagles"
        event = _event(title=good, start_time=None)
        out = client.enrich(event)
        self.assertEqual(out["title"], good)

    def test_no_match_returns_event_unchanged(self) -> None:
        client = self._client_with_gameweek()
        event = _event(title="Dallas Cowboys vs New York Giants", start_time=None)
        before = dict(event)
        out = client.enrich(event)
        self.assertEqual(out, before)

    def test_unparseable_title_returns_unchanged(self) -> None:
        client = self._client_with_gameweek()
        event = _event(title="No matchup here", start_time=None)
        before = dict(event)
        out = client.enrich(event)
        self.assertEqual(out, before)

    def test_empty_schedule_returns_unchanged(self) -> None:
        client = NflScheduleClient(
            csv_url="https://example.test/games.csv",
            cache_ttl=604800,
            tz="America/Mexico_City",
            data_dir=self.data_dir,
        )
        with mock.patch.object(client, "gameweek_games", return_value=[]):
            event = _event(start_time=None)
            before = dict(event)
            out = client.enrich(event)
        self.assertEqual(out, before)

    def test_schedule_without_gametime_leaves_start_time_none(self) -> None:
        client = self._client_with_gameweek()
        event = _event(title="New York Jets vs Buffalo Bills", start_time=None)
        out = client.enrich(event)
        self.assertIsNone(out["start_time"])

    def test_gameweek_memoized_across_enrich_calls(self) -> None:
        # Varios enrich en un mismo refresco no deben recalcular la jornada.
        client = NflScheduleClient(
            csv_url="https://example.test/games.csv",
            cache_ttl=604800,
            tz="America/Mexico_City",
            data_dir=self.data_dir,
        )
        with mock.patch.object(
            client, "gameweek_games", return_value=[],
        ) as mock_gw:
            client.enrich(_event())
            client.enrich(_event())
            client.enrich(_event())
        mock_gw.assert_called_once()

    def test_refresh_schedule_invalidates_cache(self) -> None:
        client = NflScheduleClient(
            csv_url="https://example.test/games.csv",
            cache_ttl=604800,
            tz="America/Mexico_City",
            data_dir=self.data_dir,
        )
        with mock.patch.object(
            client, "gameweek_games", return_value=[],
        ) as mock_gw:
            client.enrich(_event())
            client.refresh_schedule()
            client.enrich(_event())
        self.assertEqual(mock_gw.call_count, 2)


if __name__ == "__main__":
    unittest.main()
