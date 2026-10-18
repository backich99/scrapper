"""Tests del esqueleto de PluginClient (Tarea 2.1).

Usa unittest (stdlib) para no añadir dependencias. Cubre el modelo Stream, la
normalización de base_url y las ramas de is_up() (health ok, fallback a
manifest.json, ambos fallan, y error de red sin excepción).
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import requests

from scraper import plugin_client
from scraper.plugin_client import (
    PluginClient,
    Stream,
    _derive_status,
    _gameweek_window,
    _in_gameweek,
    _is_nfl,
    _parse_proxy_headers,
    _parse_released,
    _parse_resolution_height,
    _parse_source,
)


class _FakeResponse:
    """Respuesta mínima con la interfaz que usan is_up() y list_nfl_gameweek()."""

    def __init__(self, status_code: int, json_data: object = None) -> None:
        self.status_code = status_code
        self._json_data = json_data

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> object:
        if self._json_data is _INVALID_JSON:
            raise ValueError("No JSON object could be decoded")
        return self._json_data


# Centinela: usar como json_data para simular un cuerpo que no es JSON válido.
_INVALID_JSON = object()


def _meta(meta_id: str, name: str, released: str | None) -> dict:
    """Construye un meta de catálogo mínimo como el que emite el plugin."""
    m: dict = {"id": meta_id, "name": name, "type": "tv"}
    if released is not None:
        m["released"] = released
    return m


class StreamDataclassTest(unittest.TestCase):
    def test_fields_assigned(self) -> None:
        s = Stream(
            url="https://host/stream.m3u8",
            source="DaddyLive",
            resolution_height=1080,
            referer="https://ref/",
            origin="https://orig",
            label="DaddyLive 1080p",
        )
        self.assertEqual(s.url, "https://host/stream.m3u8")
        self.assertEqual(s.source, "DaddyLive")
        self.assertEqual(s.resolution_height, 1080)
        self.assertEqual(s.referer, "https://ref/")
        self.assertEqual(s.origin, "https://orig")
        self.assertEqual(s.label, "DaddyLive 1080p")

    def test_optional_fields_accept_none(self) -> None:
        s = Stream(
            url="https://host/stream.m3u8",
            source="Unknown",
            resolution_height=None,
            referer=None,
            origin=None,
            label="stream",
        )
        self.assertIsNone(s.resolution_height)
        self.assertIsNone(s.referer)
        self.assertIsNone(s.origin)


class PluginClientInitTest(unittest.TestCase):
    def test_base_url_trailing_slash_stripped(self) -> None:
        client = PluginClient("http://localhost:7000/", timeout=5)
        self.assertEqual(client.base_url, "http://localhost:7000")
        self.assertEqual(client.timeout, 5)

    def test_base_url_without_slash_unchanged(self) -> None:
        client = PluginClient("http://localhost:7000", timeout=2.5)
        self.assertEqual(client.base_url, "http://localhost:7000")


class PluginClientIsUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = PluginClient("http://localhost:7000", timeout=1)

    def test_health_ok_returns_true(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200)) as get:
            self.assertTrue(self.client.is_up())
        # Con /health OK no hace falta probar /manifest.json.
        get.assert_called_once_with("http://localhost:7000/health", timeout=1)

    def test_falls_back_to_manifest_when_health_not_ok(self) -> None:
        responses = [_FakeResponse(404), _FakeResponse(200)]
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=responses) as get:
            self.assertTrue(self.client.is_up())
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            [c.args[0] for c in get.call_args_list],
            ["http://localhost:7000/health", "http://localhost:7000/manifest.json"],
        )

    def test_returns_false_when_both_endpoints_fail(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(500)):
            self.assertFalse(self.client.is_up())

    def test_network_error_returns_false_without_raising(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=requests.ConnectionError("refused")):
            self.assertFalse(self.client.is_up())

    def test_health_network_error_then_manifest_ok(self) -> None:
        side_effect = [requests.Timeout("slow"), _FakeResponse(200)]
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=side_effect):
            self.assertTrue(self.client.is_up())


class PluginClientInitTimezoneTest(unittest.TestCase):
    def test_valid_timezone_loaded(self) -> None:
        client = PluginClient("http://localhost:7000", timeout=5,
                              timezone="America/New_York")
        self.assertEqual(client.tz, ZoneInfo("America/New_York"))

    def test_default_timezone(self) -> None:
        client = PluginClient("http://localhost:7000", timeout=5)
        self.assertEqual(client.tz, ZoneInfo(plugin_client.DEFAULT_TIMEZONE))

    def test_invalid_timezone_falls_back_to_default(self) -> None:
        client = PluginClient("http://localhost:7000", timeout=5,
                              timezone="Not/AZone")
        self.assertEqual(client.tz, ZoneInfo(plugin_client.DEFAULT_TIMEZONE))


class GameweekWindowTest(unittest.TestCase):
    def test_window_from_a_wednesday(self) -> None:
        # 2026-10-21 es miércoles -> jornada martes 20 → lunes 26.
        start, end = _gameweek_window(date(2026, 10, 21))
        self.assertEqual(start, date(2026, 10, 20))
        self.assertEqual(end, date(2026, 10, 26))

    def test_window_from_the_tuesday_itself(self) -> None:
        # Martes 2026-10-20: la jornada arranca ese mismo día.
        start, end = _gameweek_window(date(2026, 10, 20))
        self.assertEqual(start, date(2026, 10, 20))
        self.assertEqual(end, date(2026, 10, 26))

    def test_window_from_the_monday(self) -> None:
        # Lunes 2026-10-26: cierre de la jornada que arrancó el martes 20.
        start, end = _gameweek_window(date(2026, 10, 26))
        self.assertEqual(start, date(2026, 10, 20))
        self.assertEqual(end, date(2026, 10, 26))

    def test_window_is_always_seven_days(self) -> None:
        for day in range(1, 29):
            start, end = _gameweek_window(date(2026, 2, day))
            self.assertEqual((end - start).days, 6)
            self.assertEqual(start.weekday(), 1)  # martes
            self.assertEqual(end.weekday(), 0)    # lunes


class ParseReleasedTest(unittest.TestCase):
    def test_z_suffix_normalized(self) -> None:
        self.assertEqual(
            _parse_released("2026-10-17T18:30:00.000Z"),
            "2026-10-17T18:30:00Z",
        )

    def test_offset_converted_to_utc(self) -> None:
        self.assertEqual(
            _parse_released("2026-10-17T13:30:00-05:00"),
            "2026-10-17T18:30:00Z",
        )

    def test_naive_assumed_utc(self) -> None:
        self.assertEqual(
            _parse_released("2026-10-17T18:30:00"),
            "2026-10-17T18:30:00Z",
        )

    def test_none_and_empty_and_invalid(self) -> None:
        self.assertIsNone(_parse_released(None))
        self.assertIsNone(_parse_released(""))
        self.assertIsNone(_parse_released("   "))
        self.assertIsNone(_parse_released("not-a-date"))
        self.assertIsNone(_parse_released(12345))


class DeriveStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)

    def test_upcoming_before_kickoff(self) -> None:
        self.assertEqual(
            _derive_status("2026-10-17T20:00:00Z", self.now), "upcoming"
        )

    def test_live_within_four_hours(self) -> None:
        self.assertEqual(
            _derive_status("2026-10-17T18:30:00Z", self.now), "live"
        )

    def test_unavailable_after_window(self) -> None:
        self.assertEqual(
            _derive_status("2026-10-17T10:00:00Z", self.now), "unavailable"
        )

    def test_unavailable_without_start_time(self) -> None:
        self.assertEqual(_derive_status(None, self.now), "unavailable")


class IsNflHeuristicTest(unittest.TestCase):
    def test_explicit_nfl_kept(self) -> None:
        self.assertTrue(_is_nfl("NFL: Chiefs vs Raiders"))

    def test_college_discarded(self) -> None:
        self.assertFalse(_is_nfl("NCAA: Alabama Crimson Tide vs LSU"))
        self.assertFalse(_is_nfl("Ohio State Buckeyes vs Michigan"))

    def test_nfl_takes_priority_over_college_hint(self) -> None:
        # Si menciona NFL explícitamente, gana sobre cualquier pista de college.
        self.assertTrue(_is_nfl("NFL preseason at Penn State University"))

    def test_team_names_detected(self) -> None:
        self.assertTrue(_is_nfl("Dallas Cowboys vs Philadelphia Eagles"))

    def test_ambiguous_kept(self) -> None:
        self.assertTrue(_is_nfl("American Football Game of the Week"))


class InGameweekTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = ZoneInfo("America/Mexico_City")
        self.start = date(2026, 10, 20)
        self.end = date(2026, 10, 26)

    def test_inside_window(self) -> None:
        self.assertTrue(
            _in_gameweek("2026-10-22T18:00:00Z", self.start, self.end, self.tz)
        )

    def test_before_window(self) -> None:
        self.assertFalse(
            _in_gameweek("2026-10-19T18:00:00Z", self.start, self.end, self.tz)
        )

    def test_after_window(self) -> None:
        self.assertFalse(
            _in_gameweek("2026-10-27T18:00:00Z", self.start, self.end, self.tz)
        )

    def test_none_start_time_kept(self) -> None:
        self.assertTrue(_in_gameweek(None, self.start, self.end, self.tz))

    def test_timezone_shift_affects_local_date(self) -> None:
        # 2026-10-20T03:00:00Z = 2026-10-19 21:00 en Ciudad de México (fuera, el
        # día anterior al martes de arranque).
        self.assertFalse(
            _in_gameweek("2026-10-20T03:00:00Z", self.start, self.end, self.tz)
        )


class ListNflGameweekTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = PluginClient(
            "http://localhost:7000", timeout=5, timezone="America/Mexico_City"
        )

    def _run_with_metas(self, metas: list, today: date) -> list[dict]:
        """Ejecuta list_nfl_gameweek() con un catálogo fijo y un 'hoy' fijo."""
        fake = _FakeResponse(200, {"metas": metas})

        real_now = datetime.now

        def fake_now(tz=None):
            # 'hoy' fijo a mediodía para evitar saltos por hora del día.
            base = datetime(today.year, today.month, today.day, 12, 0,
                            tzinfo=timezone.utc)
            return base.astimezone(tz) if tz is not None else base

        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=fake) as get, \
                mock.patch("scraper.plugin_client.datetime") as dt:
            dt.now.side_effect = fake_now
            dt.fromisoformat.side_effect = datetime.fromisoformat
            events = self.client.list_nfl_gameweek()
        get.assert_called_once_with(
            "http://localhost:7000/catalog/tv/nuvio_sports_american_football.json",
            timeout=5,
        )
        return events

    def test_maps_meta_to_project_schema(self) -> None:
        metas = [_meta("nuvio_sport_abc", "NFL: Chiefs vs Raiders",
                       "2026-10-22T00:30:00Z")]
        events = self._run_with_metas(metas, date(2026, 10, 21))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["id"], "nuvio_sport_abc")
        self.assertEqual(ev["title"], "NFL: Chiefs vs Raiders")
        self.assertEqual(ev["sport"], "american_football")
        self.assertEqual(ev["league"], "NFL")
        self.assertEqual(ev["start_time"], "2026-10-22T00:30:00Z")
        self.assertIsNone(ev["stream_url"])
        self.assertEqual(ev["source"], "live-sport-plugin")
        self.assertEqual(ev["referrer"], "")
        self.assertIn(ev["status"], ("live", "upcoming", "unavailable"))

    def test_college_filtered_out(self) -> None:
        metas = [
            _meta("nuvio_sport_1", "NFL: Bills vs Jets", "2026-10-22T18:00:00Z"),
            _meta("nuvio_sport_2", "NCAA: Alabama vs LSU", "2026-10-22T18:00:00Z"),
        ]
        events = self._run_with_metas(metas, date(2026, 10, 21))
        ids = [e["id"] for e in events]
        self.assertEqual(ids, ["nuvio_sport_1"])

    def test_out_of_gameweek_filtered_out(self) -> None:
        metas = [
            _meta("nuvio_sport_in", "NFL: Bills vs Jets", "2026-10-22T18:00:00Z"),
            _meta("nuvio_sport_old", "NFL: Rams vs 49ers", "2026-10-13T18:00:00Z"),
            _meta("nuvio_sport_future", "NFL: Lions vs Bears",
                  "2026-10-30T18:00:00Z"),
        ]
        events = self._run_with_metas(metas, date(2026, 10, 21))
        ids = [e["id"] for e in events]
        self.assertEqual(ids, ["nuvio_sport_in"])

    def test_event_without_start_time_kept(self) -> None:
        metas = [_meta("nuvio_sport_247", "NFL Network", None)]
        events = self._run_with_metas(metas, date(2026, 10, 21))
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["start_time"])
        self.assertEqual(events[0]["status"], "unavailable")

    def test_meta_without_id_or_title_skipped(self) -> None:
        metas = [
            {"name": "NFL: no id", "released": "2026-10-22T18:00:00Z"},
            {"id": "nuvio_sport_x", "released": "2026-10-22T18:00:00Z"},
            "not-a-dict",
            _meta("nuvio_sport_ok", "NFL: Chiefs vs Raiders",
                  "2026-10-22T18:00:00Z"),
        ]
        events = self._run_with_metas(metas, date(2026, 10, 21))
        self.assertEqual([e["id"] for e in events], ["nuvio_sport_ok"])

    def test_network_error_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=requests.ConnectionError("refused")):
            self.assertEqual(self.client.list_nfl_gameweek(), [])

    def test_http_error_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(500)):
            self.assertEqual(self.client.list_nfl_gameweek(), [])

    def test_invalid_json_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, _INVALID_JSON)):
            self.assertEqual(self.client.list_nfl_gameweek(), [])

    def test_payload_without_metas_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, {"foo": "bar"})):
            self.assertEqual(self.client.list_nfl_gameweek(), [])


def _stream_item(
    name: str,
    title: str,
    *,
    url: str | None = None,
    external_url: str | None = None,
    resolution: str | None = None,
    behavior_hints: dict | None = None,
) -> dict:
    """Construye un stream Stremio como el que emite /stream."""
    item: dict = {"name": name, "title": title}
    if url is not None:
        item["url"] = url
    if external_url is not None:
        item["externalUrl"] = external_url
    if resolution is not None:
        item["resolution"] = resolution
    if behavior_hints is not None:
        item["behaviorHints"] = behavior_hints
    return item


class ParseSourceTest(unittest.TestCase):
    def test_known_provider_matched(self) -> None:
        self.assertEqual(
            _parse_source("⚡ DaddyLive | 🎬 Main | 📺 ESPN"), "DaddyLive"
        )

    def test_known_provider_case_insensitive(self) -> None:
        self.assertEqual(_parse_source("direct via watchfooty feed"), "WatchFooty")

    def test_fallback_first_segment_without_emoji(self) -> None:
        self.assertEqual(_parse_source("⚡ CoolProvider | 📺 Fox"), "CoolProvider")

    def test_empty_title_is_unknown(self) -> None:
        self.assertEqual(_parse_source(""), "Unknown")


class ParseResolutionHeightTest(unittest.TestCase):
    def test_wxh(self) -> None:
        self.assertEqual(_parse_resolution_height("1920x1080", ""), 1080)

    def test_np(self) -> None:
        self.assertEqual(_parse_resolution_height("720p", ""), 720)
        self.assertEqual(_parse_resolution_height("1080p60", ""), 1080)

    def test_hd_and_sd_unknown(self) -> None:
        self.assertIsNone(_parse_resolution_height("HD", ""))
        self.assertIsNone(_parse_resolution_height("SD", ""))
        self.assertIsNone(_parse_resolution_height(None, ""))

    def test_fallback_to_title(self) -> None:
        self.assertEqual(
            _parse_resolution_height("HD", "📺 Quality: 1080p · 4 Mbps"), 1080
        )

    def test_no_signal_returns_none(self) -> None:
        self.assertIsNone(_parse_resolution_height("Auto", "no digits here"))


class ParseProxyHeadersTest(unittest.TestCase):
    def test_reads_referer_and_origin(self) -> None:
        hints = {"proxyHeaders": {"request": {
            "Referer": "https://ref/", "Origin": "https://orig",
        }}}
        self.assertEqual(_parse_proxy_headers(hints), ("https://ref/", "https://orig"))

    def test_missing_hints_returns_none_pair(self) -> None:
        self.assertEqual(_parse_proxy_headers(None), (None, None))
        self.assertEqual(_parse_proxy_headers({}), (None, None))
        self.assertEqual(_parse_proxy_headers({"proxyHeaders": {}}), (None, None))

    def test_empty_values_treated_as_none(self) -> None:
        hints = {"proxyHeaders": {"request": {"Referer": "", "Origin": ""}}}
        self.assertEqual(_parse_proxy_headers(hints), (None, None))


class ResolveStreamsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = PluginClient(
            "http://localhost:7000", timeout=5, retry_delay=4
        )

    def test_url_and_order_preserved(self) -> None:
        metas = {"streams": [
            _stream_item("🧿 Support Us", "Donate", external_url="https://donate"),
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive | 📺 ESPN",
                         url="http://localhost:7000/api/manifest?url=a",
                         resolution="1920x1080"),
            _stream_item("⚡ Direct Stream", "⚡ WatchFooty | 📺 Fox",
                         url="http://localhost:7000/api/manifest?url=b",
                         resolution="720p"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)) as get, \
                mock.patch("scraper.plugin_client._time.sleep") as sleep:
            streams = self.client.resolve_streams("nuvio_sport_abc")
        get.assert_called_once_with(
            "http://localhost:7000/stream/tv/nuvio_sport_abc.json", timeout=5
        )
        sleep.assert_not_called()
        self.assertEqual(len(streams), 2)
        self.assertEqual(streams[0].source, "DaddyLive")
        self.assertEqual(streams[0].resolution_height, 1080)
        self.assertEqual(streams[1].source, "WatchFooty")
        self.assertEqual(streams[1].resolution_height, 720)

    def test_support_us_and_web_stream_discarded(self) -> None:
        metas = {"streams": [
            _stream_item("🧿 Support Us", "Donate", external_url="https://d"),
            _stream_item("🌐 Web Stream", "⚡ EmbedSt | iframe",
                         url="http://localhost:7000/watch?u=x"),
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)), \
                mock.patch("scraper.plugin_client._time.sleep"):
            streams = self.client.resolve_streams("nuvio_sport_abc")
        self.assertEqual([s.source for s in streams], ["DaddyLive"])

    def test_stream_without_url_discarded(self) -> None:
        metas = {"streams": [
            _stream_item("▶️ YouTube", "▶️ YouTube", external_url="https://yt"),
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)), \
                mock.patch("scraper.plugin_client._time.sleep"):
            streams = self.client.resolve_streams("nuvio_sport_abc")
        self.assertEqual(len(streams), 1)

    def test_proxy_headers_mapped(self) -> None:
        metas = {"streams": [
            _stream_item(
                "⚡ Direct Stream", "⚡ DaddyLive | 📺 ESPN",
                url="https://upstream.example/live.m3u8",
                resolution="1080p",
                behavior_hints={"notWebReady": True, "proxyHeaders": {
                    "request": {"Referer": "https://ref/", "Origin": "https://o"},
                }},
            ),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)), \
                mock.patch("scraper.plugin_client._time.sleep"):
            streams = self.client.resolve_streams("nuvio_sport_abc")
        self.assertEqual(streams[0].referer, "https://ref/")
        self.assertEqual(streams[0].origin, "https://o")

    def test_retry_when_few_sources_and_keeps_longer(self) -> None:
        first = {"streams": [
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
        ]}
        second = {"streams": [
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
            _stream_item("⚡ Direct Stream", "⚡ WatchFooty",
                         url="http://localhost:7000/api/manifest?url=b"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=[_FakeResponse(200, first),
                                     _FakeResponse(200, second)]) as get, \
                mock.patch("scraper.plugin_client._time.sleep") as sleep:
            streams = self.client.resolve_streams("nuvio_sport_abc")
        sleep.assert_called_once_with(4)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(streams), 2)

    def test_retry_keeps_first_when_retry_has_fewer(self) -> None:
        first = {"streams": [
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
        ]}
        second = {"streams": []}
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=[_FakeResponse(200, first),
                                     _FakeResponse(200, second)]), \
                mock.patch("scraper.plugin_client._time.sleep"):
            streams = self.client.resolve_streams("nuvio_sport_abc")
        self.assertEqual(len(streams), 1)

    def test_no_retry_when_enough_sources(self) -> None:
        metas = {"streams": [
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
            _stream_item("⚡ Direct Stream", "⚡ WatchFooty",
                         url="http://localhost:7000/api/manifest?url=b"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)) as get, \
                mock.patch("scraper.plugin_client._time.sleep") as sleep:
            streams = self.client.resolve_streams("nuvio_sport_abc")
        sleep.assert_not_called()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(streams), 2)

    def test_no_retry_when_zero_sources(self) -> None:
        metas = {"streams": [
            _stream_item("🧿 Support Us", "Donate", external_url="https://d"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)) as get, \
                mock.patch("scraper.plugin_client._time.sleep") as sleep:
            streams = self.client.resolve_streams("nuvio_sport_abc")
        sleep.assert_not_called()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(streams, [])

    def test_zero_retry_delay_skips_retry(self) -> None:
        client = PluginClient("http://localhost:7000", timeout=5, retry_delay=0)
        metas = {"streams": [
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)) as get, \
                mock.patch("scraper.plugin_client._time.sleep") as sleep:
            streams = client.resolve_streams("nuvio_sport_abc")
        sleep.assert_not_called()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(streams), 1)

    def test_network_error_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        side_effect=requests.ConnectionError("refused")):
            self.assertEqual(self.client.resolve_streams("nuvio_sport_x"), [])

    def test_http_error_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(500)):
            self.assertEqual(self.client.resolve_streams("nuvio_sport_x"), [])

    def test_invalid_json_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, _INVALID_JSON)):
            self.assertEqual(self.client.resolve_streams("nuvio_sport_x"), [])

    def test_payload_without_streams_returns_empty(self) -> None:
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, {"foo": "bar"})):
            self.assertEqual(self.client.resolve_streams("nuvio_sport_x"), [])

    def test_non_dict_items_skipped(self) -> None:
        metas = {"streams": [
            "not-a-dict",
            _stream_item("⚡ Direct Stream", "⚡ DaddyLive",
                         url="http://localhost:7000/api/manifest?url=a"),
            _stream_item("⚡ Direct Stream", "⚡ WatchFooty",
                         url="http://localhost:7000/api/manifest?url=b"),
        ]}
        with mock.patch("scraper.plugin_client.requests.get",
                        return_value=_FakeResponse(200, metas)), \
                mock.patch("scraper.plugin_client._time.sleep"):
            streams = self.client.resolve_streams("nuvio_sport_abc")
        self.assertEqual(len(streams), 2)


if __name__ == "__main__":
    unittest.main()
