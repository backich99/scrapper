"""Control remoto web del kiosk (sin Kivy).

Sirve una página móvil y una API JSON mínima para elegir un partido, detenerlo
y ver el estado desde el celular, en la misma red local. Este módulo no importa
Kivy: toda la interacción con la app pasa por un ``RemoteBackend`` que
``ui/app.py`` implementa sobre ``KioskRoot``.

Hilos: los handlers HTTP corren en hilos del servidor y no pueden tocar widgets.
Por eso cada llamada al backend se ejecuta con ``run_on_ui`` (inyectado), que en
la app programa la función en el hilo de Kivy y espera el resultado. Si la UI no
responde a tiempo, ``run_on_ui`` lanza ``TimeoutError`` y la API responde 503.

Seguridad (sin login, solo LAN): lista cerrada de rutas y métodos; el id del
partido es solo una clave de búsqueda sobre la lista actual (el backend responde
404 si no existe); cuerpo máximo de 4 KB; los POST exigen
``Content-Type: application/json`` (fuerza preflight CORS desde páginas ajenas) y
el servidor no emite cabeceras CORS.

Contrato de la API: ver README.md ("Control remoto desde el móvil").
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

# Límites de validación en el borde de la API.
MAX_BODY_BYTES = 4096
MAX_ID_LENGTH = 120


def match_id(game: Any) -> str:
    """Id estable de un partido de la lista actual.

    Usa ``event_id`` del plugin si existe; para partidos de ESPN (sin
    ``event_id``) se arma ``espn-{away}-{home}-{YYYYMMDD UTC}`` en minúsculas.
    """
    event_id = getattr(game, "event_id", None)
    if event_id:
        return str(event_id)
    parts = ["espn", str(game.away_abbr), str(game.home_abbr)]
    start = getattr(game, "start", None)
    if start is not None:
        parts.append(start.strftime("%Y%m%d"))
    return "-".join(parts).lower()


def match_to_dict(game: Any, unavailable: bool) -> dict:
    """Serializa un partido al formato ``Match`` de la API."""
    start = getattr(game, "start", None)
    return {
        "id": match_id(game),
        "title": game.title,
        "away": game.away_name,
        "home": game.home_name,
        "league": "NFL",
        "state": game.state,
        "status_text": game.status_text(),
        "start": start.isoformat() if start is not None else None,
        "playable": bool(game.is_playable()),
        "unavailable": bool(unavailable),
    }


class RemoteBackend(Protocol):
    """Operaciones que el servidor necesita de la app (se ejecutan en el hilo de la UI)."""

    def list_matches(self) -> dict:
        """``{"loaded": bool, "source": "plugin"|"espn"|None, "matches": [Match, ...]}``."""

    def get_status(self) -> dict:
        """``{"state": "idle"|"resolving"|"playing", "match", "message", "source_label"}``."""

    def play(self, match_id: str) -> tuple[int, dict]:
        """Elige un partido de la lista actual. Devuelve ``(código HTTP, cuerpo)``."""

    def stop(self) -> tuple[int, dict]:
        """Detiene la reproducción. Devuelve ``(código HTTP, cuerpo)``."""


def _error(code: int, error: str, message: str) -> tuple[int, dict]:
    return code, {"ok": False, "error": error, "message": message}


class RemoteServer:
    """Servidor HTTP del control remoto, en un hilo daemon."""

    def __init__(
        self,
        backend: RemoteBackend,
        run_on_ui: Callable[[Callable[[], Any]], Any],
        bind: str,
        port: int,
        page_html: str,
    ) -> None:
        self._backend = backend
        self._run_on_ui = run_on_ui
        self._bind = bind
        self._port = port
        self._page = page_html.encode("utf-8")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def server_address(self) -> tuple[str, int] | None:
        """Dirección real de escucha (útil con puerto 0), o ``None`` si no arrancó."""
        return self._httpd.server_address[:2] if self._httpd else None

    def start(self) -> bool:
        """Abre el puerto y atiende en un hilo daemon. ``False`` si no pudo abrirlo."""
        try:
            httpd = ThreadingHTTPServer((self._bind, self._port), self._make_handler())
        except OSError as exc:
            log.warning(
                "Control remoto: no se pudo abrir %s:%s (%s); la UI táctil sigue activa.",
                self._bind, self._port, exc,
            )
            return False
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(
            target=lambda: httpd.serve_forever(poll_interval=0.1), name="remote-http", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Detiene el servidor y libera el puerto."""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None

    # -- Handler --------------------------------------------------------- #

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
                log.debug("remote: " + fmt, *args)

            # -- Respuestas -- #
            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _send_json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")

            def _call(self, fn: Callable[[], Any]) -> Any:
                """Ejecuta ``fn`` en la UI; traduce fallos a (código, cuerpo) de error."""
                try:
                    return True, server._run_on_ui(fn)
                except TimeoutError:
                    return False, _error(503, "ui_timeout", "La pantalla de la Raspberry no responde.")
                except Exception:  # noqa: BLE001 - la API no debe tumbar el hilo
                    log.exception("Control remoto: error interno")
                    return False, _error(500, "internal", "Error interno del kiosk.")

            def _reply(self, ok: bool, result: Any, *, ok_code: int = 200) -> None:
                if not ok:
                    code, body = result
                    self._send_json(code, body)
                else:
                    self._send_json(ok_code, result)

            # -- Rutas -- #
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(200, server._page, "text/html; charset=utf-8")
                elif path == "/api/matches":
                    ok, res = self._call(lambda: server._backend.list_matches())
                    self._reply(ok, res if not ok else {"ok": True, **res})
                elif path == "/api/status":
                    ok, res = self._call(lambda: server._backend.get_status())
                    self._reply(ok, res if not ok else {"ok": True, **res})
                elif path in ("/api/play", "/api/stop"):
                    self._send_json(*_error(405, "method_not_allowed", "Método no permitido."))
                else:
                    self._send_json(*_error(404, "not_found", "Ruta no encontrada."))

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path not in ("/api/play", "/api/stop"):
                    if path in ("/", "/api/matches", "/api/status"):
                        self._send_json(*_error(405, "method_not_allowed", "Método no permitido."))
                    else:
                        self._send_json(*_error(404, "not_found", "Ruta no encontrada."))
                    return

                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    self.close_connection = True
                    self._send_json(*_error(
                        415, "unsupported_media_type",
                        "Content-Type debe ser application/json.",
                    ))
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if length < 0:
                    self.close_connection = True
                    self._send_json(*_error(400, "bad_request", "Content-Length inválido."))
                    return
                if length > MAX_BODY_BYTES:
                    self.close_connection = True
                    self._send_json(*_error(413, "too_large", "Cuerpo demasiado grande."))
                    return
                raw = self.rfile.read(length) if length else b""

                if path == "/api/stop":
                    ok, res = self._call(lambda: server._backend.stop())
                    self._reply_pair(ok, res)
                    return

                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    self._send_json(*_error(400, "bad_request", "JSON inválido."))
                    return
                mid = data.get("id") if isinstance(data, dict) else None
                if not isinstance(mid, str) or not mid or len(mid) > MAX_ID_LENGTH:
                    self._send_json(*_error(
                        400, "bad_request", "Falta 'id' (texto de hasta 120 caracteres)."
                    ))
                    return
                ok, res = self._call(lambda: server._backend.play(mid))
                self._reply_pair(ok, res)

            def _reply_pair(self, ok: bool, res: Any) -> None:
                """Responde con el ``(código, cuerpo)`` del backend o del error de ``_call``."""
                code, body = res
                self._send_json(code, body)

            def do_PUT(self) -> None:  # noqa: N802
                self._send_json(*_error(405, "method_not_allowed", "Método no permitido."))

            do_DELETE = do_PATCH = do_PUT

        return Handler

