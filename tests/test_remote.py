"""Tests del control remoto web (ui/remote.py).

Usa unittest (stdlib). ``ui/remote.py`` no importa Kivy, así que corre sin
display: se levanta un ``RemoteServer`` real en 127.0.0.1 (puerto 0) con un
``FakeBackend`` y un ``run_on_ui`` que llama directo.

Ejecución:
    PYTHONPATH=. python tests/test_remote.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ui.remote import MAX_BODY_BYTES, RemoteServer, match_id, match_to_dict  # noqa: E402

PAGE = "<!doctype html><title>remote</title>"


def _game(event_id=None, away="DAL", home="PHI", start=None):
    return SimpleNamespace(
        event_id=event_id, away_abbr=away, home_abbr=home, away_name="Dallas",
        home_name="Philadelphia", title="Dallas vs Philadelphia", state="in",
        start=start, status_text=lambda: "Q3", is_playable=lambda: True,
    )


class FakeBackend:
    """Backend de prueba: una lista fija y respuestas configurables."""

    def __init__(self) -> None:
        self.ids = {"abc", "def"}
        self.played: list[str] = []
        self.stopped = 0
        self.play_result: tuple[int, dict] | None = None
        self.state = "idle"

    def list_matches(self) -> dict:
        return {"loaded": True, "source": "plugin",
                "matches": [match_to_dict(_game("abc"), False)]}

    def get_status(self) -> dict:
        return {"state": self.state, "match": None, "message": None, "source_label": None}

    def play(self, mid: str):
        if mid not in self.ids:
            return 404, {"ok": False, "error": "unknown_match", "message": "No existe."}
        if self.play_result:
            return self.play_result
        self.played.append(mid)
        return 202, {"ok": True, "state": "resolving", "match": None}

    def stop(self):
        self.stopped += 1
        return 200, {"ok": True, "state": "idle"}


class RemoteServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.server = RemoteServer(self.backend, lambda fn: fn(), "127.0.0.1", 0, PAGE)
        self.assertTrue(self.server.start())
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.stop()

    def _req(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(), exc.headers
            finally:
                exc.close()

    def _post(self, path, body=None, raw=None, ctype="application/json"):
        headers = {"Content-Type": ctype} if ctype else {}
        return self._req("POST", path, body=body, raw=raw, headers=headers)

    def test_get_matches_and_status(self) -> None:
        code, body, headers = self._req("GET", "/api/matches")
        data = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"] and data["loaded"])
        self.assertEqual(data["matches"][0]["id"], "abc")
        self.assertEqual(headers["Cache-Control"], "no-store")
        code, body, _ = self._req("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["state"], "idle")

    def test_play_ok(self) -> None:
        code, body, _ = self._post("/api/play", {"id": "abc"})
        self.assertEqual(code, 202)
        self.assertEqual(self.backend.played, ["abc"])

    def test_play_unknown_id(self) -> None:
        code, body, _ = self._post("/api/play", {"id": "nope"})
        self.assertEqual(code, 404)
        self.assertEqual(json.loads(body)["error"], "unknown_match")
        self.assertEqual(self.backend.played, [])

    def test_play_bad_requests(self) -> None:
        for payload in ({"id": 5}, {"id": ""}, {}, {"id": "x" * 500}, [1], {"id": None}):
            code, body, _ = self._post("/api/play", payload)
            self.assertEqual(code, 400, payload)
            self.assertEqual(json.loads(body)["error"], "bad_request")
        code, _, _ = self._post("/api/play", raw=b"{roto")
        self.assertEqual(code, 400)
        self.assertEqual(self.backend.played, [])

    def test_post_requires_json_content_type(self) -> None:
        code, body, _ = self._post("/api/play", {"id": "abc"}, ctype=None)
        self.assertEqual(code, 415)
        code, _, _ = self._post("/api/play", {"id": "abc"}, ctype="text/plain")
        self.assertEqual(code, 415)
        code, _, _ = self._post("/api/stop", {}, ctype="text/plain")
        self.assertEqual(code, 415)
        self.assertEqual(self.backend.played, [])
        self.assertEqual(self.backend.stopped, 0)

    def test_body_too_large(self) -> None:
        code, body, _ = self._post("/api/play", raw=b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual(code, 413)
        self.assertEqual(json.loads(body)["error"], "too_large")

    def test_conflicts_pass_through(self) -> None:
        for err in ("busy", "not_playable"):
            self.backend.play_result = (409, {"ok": False, "error": err, "message": "m"})
            code, body, _ = self._post("/api/play", {"id": "abc"})
            self.assertEqual(code, 409)
            self.assertEqual(json.loads(body)["error"], err)

    def test_stop_with_empty_and_json_body(self) -> None:
        code, body, _ = self._post("/api/stop")
        self.assertEqual(code, 200)
        code, _, _ = self._post("/api/stop", {})
        self.assertEqual(code, 200)
        self.assertEqual(self.backend.stopped, 2)

    def test_unknown_route_and_method(self) -> None:
        self.assertEqual(self._req("GET", "/nada")[0], 404)
        self.assertEqual(self._post("/nada", {})[0], 404)
        self.assertEqual(self._req("GET", "/api/play")[0], 405)
        self.assertEqual(self._post("/api/status", {})[0], 405)
        self.assertEqual(self._req("PUT", "/api/play", body={})[0], 405)
        self.assertEqual(self._req("DELETE", "/api/stop")[0], 405)

    def test_index_html_and_no_cors(self) -> None:
        code, body, headers = self._req("GET", "/")
        self.assertEqual(code, 200)
        self.assertEqual(body.decode(), PAGE)
        self.assertIn("text/html", headers["Content-Type"])
        _, _, h2 = self._req("GET", "/api/status")
        self.assertIsNone(h2.get("Access-Control-Allow-Origin"))
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_ui_timeout_is_503(self) -> None:
        def boom(_fn):
            raise TimeoutError()
        self.server.stop()
        server = RemoteServer(self.backend, boom, "127.0.0.1", 0, PAGE)
        self.assertTrue(server.start())
        self.server = server
        host, port = server.server_address
        self.base = f"http://{host}:{port}"
        self.assertEqual(self._req("GET", "/api/status")[0], 503)
        self.assertEqual(self._post("/api/play", {"id": "abc"})[0], 503)


class HelpersTests(unittest.TestCase):
    def test_match_id_with_event_id(self) -> None:
        self.assertEqual(match_id(_game("EV123")), "EV123")

    def test_match_id_espn(self) -> None:
        start = dt.datetime(2025, 9, 7, 17, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(match_id(_game(start=start)), "espn-dal-phi-20250907")
        self.assertEqual(match_id(_game()), "espn-dal-phi")

    def test_bind_to_busy_port_returns_false(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            port = s.getsockname()[1]
            server = RemoteServer(FakeBackend(), lambda fn: fn(), "127.0.0.1", port, PAGE)
            self.assertFalse(server.start())
            server.stop()


if __name__ == "__main__":
    unittest.main()
