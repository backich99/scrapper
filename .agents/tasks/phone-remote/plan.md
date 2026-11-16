# Plan: control remoto web desde el móvil (fase 1)

Repo: `/home/karlos/Kiro/scrapper` (Python 3 / Kivy / mpv, rama `main`). No tocar `live-sport-plugin`. Código, comentarios, docstrings y textos de UI en español, con el estilo del repo (`from __future__ import annotations`, docstrings largos, `unittest` stdlib). No hacer push.

## Hallazgos del código

- **No existe servidor HTTP en el kiosk.** El único HTTP es el servicio Node del plugin en `localhost:7000` (`setup/plugin.service`). Hay que añadir uno.
- Todo el estado de reproducción vive en un único proceso: la app Kivy (`ui/app.py`, `KioskRoot`). Allí están el `MpvPlayer`, el lock (`scraper/playback_lock.py`), el overlay Detener/Siguiente, el `SourceSwitcher` de la tarjeta y el `UnavailableTracker`.
- Flujo local al tocar un partido:
  1. `MatchCard.on_release` llama a `KioskRoot._on_select(card)`.
  2. `_on_select` sale sin hacer nada si `card._busy`. Si `not game.is_playable()` pone el estado "Partido finalizado" o "Disponible ~HH:MM" y sale. Si no, pone `card._busy = True` y lanza un hilo con `_resolve_and_play(card, a_terms, b_terms)`.
  3. `_resolve_and_play` (hilo) llama a `playback.resolve_streams` (plugin y luego fallback `scraper.ondemand.resolve_match`) y construye el `SourceSwitcher`. En `finish` (hilo Kivy, vía `Clock.schedule_once`) llama a `_play_stream`, `_acquire_playback_lock`, `_show_overlay(card)` y `_start_playback_watch(card)`.
  4. Detener es `KioskRoot._on_overlay_stop(self._overlay)`: para el watch, `player.stop()`, libera el lock, dispara el update pendiente y oculta el overlay.
  5. Si mpv termina solo, `_check_playback_ended` hace lo mismo que Detener.
- La lista son `KioskRoot._cards` (`list[MatchCard]`), reconstruida en `_rebuild_cards(games, from_plugin)` (hilo Kivy). `card.game` es un `PluginGame` (`ui/gameweek.py`) o un `AgendaGame` (`ui/agenda.py`). Ambos tienen `title`, `away_name`, `home_name`, `away_abbr`, `home_abbr`, `start` (UTC aware o `None`), `state` (`pre|in|post`), `is_live`, `is_final`, `is_playable()` y `status_text()`. Solo `PluginGame` tiene `event_id`; `MatchCard.event_id = getattr(game, "event_id", None)` es `None` para ESPN.
- Hay un overlay a pantalla completa mientras se reproduce (`_overlay_visible`, `_overlay.card`). Localmente no se puede elegir otro partido sin pulsar Detener antes. El remoto copia esa regla.
- `MatchCard.set_status(text, color)` solo escribe en el label (`_status_label`). No guarda el texto en un atributo legible.
- `ScrapperApp.build` crea `MpvPlayer()` y devuelve `KioskRoot` (modo `agenda`, el de producción) o `ChannelsRoot` (modo `canales`, `SCRAPPER_UI_MODE`). `ScrapperApp.on_stop` hace `player.stop()`.
- `MpvPlayer` no expone volumen ni pausa (no hay IPC; `--no-terminal`). README: "el volumen se controla desde la TV". **Volumen y pausa quedan fuera** (la condición "si ya lo expone barato" no se cumple).
- Config: `scraper/kiosk_config.py` (dataclasses + `_as_str` / `_as_positive_int` que rechazan bool y avisan) y `config/kiosk.yaml`. Los tests están en `tests/test_kiosk_config.py`.
- `ui/app.py` importa Kivy y **Kivy no está instalado en esta máquina de desarrollo**. La lógica nueva va en un módulo sin Kivy (`ui/remote.py`) y `ui/app.py` solo la cablea.
- Comandos de test (comprobados en esta máquina):
  - `PYTHONPATH=. python3 -m unittest discover -s tests` (46 tests OK; `-t .` falla porque `tests/` no es un paquete).
  - `PYTHONPATH=. python3 -m unittest discover -s scraper -p "test_*.py" -t .` (160 tests OK).
- Hay un plan paralelo sin implementar, `.agents/tasks/failover-sources/plan.md`, que reescribe `_start_playback_watch`, `_check_playback_ended` y los textos del overlay, y añade el helper `_as_bool` en `kiosk_config.py`. Este plan solo usa `_cards`, `_on_select`, `_on_overlay_stop`, `_overlay_visible`, `_overlay.card` y `_switcher`, que ese plan no cambia. Si `_as_bool` ya existe al implementar, se reutiliza; si no, se crea con la misma firma `_as_bool(section, key, default)`.

## Decisiones

- **D1. Servidor embebido en el proceso Kivy, no un proceso aparte.** El estado y las rutas de reproducción (lock, overlay, switcher) están en `KioskRoot`. Un proceso separado tendría que duplicar el scraping y la resolución, o añadir IPC. Se usa `http.server.ThreadingHTTPServer` de la stdlib en un hilo daemon. Sin dependencias nuevas.
- **D2. Las operaciones del remoto se ejecutan en el hilo de Kivy.** Los handlers HTTP corren en hilos del servidor y no pueden tocar widgets. `KioskRoot._run_on_ui(fn, timeout=5)` programa `fn` con `Clock.schedule_once`, espera con un `threading.Event` y devuelve el resultado o relanza la excepción. Si expira el timeout, el handler responde 503. `ui/remote.py` recibe `run_on_ui` inyectado, así que los tests lo reemplazan por una llamada directa.
- **D3. Reutilizar `_on_select` y `_on_overlay_stop` tal cual.** Play = `_on_select(card)` y Stop = `_on_overlay_stop(self._overlay)`. Así el comportamiento es idéntico al táctil (lock, overlay, watch, "no disponible"). El remoto solo hace pre-validaciones para poder responder un código HTTP útil, porque `_on_select` falla en silencio.
- **D4. Id de partido.** `match_id(game)` = `game.event_id` si existe; si no, `espn-{away_abbr}-{home_abbr}-{YYYYMMDD de start en UTC}` en minúsculas (sin fecha: `espn-{away}-{home}`). El id es **solo una clave de búsqueda sobre la lista actual**: `play` busca el id en `_cards` y nunca pasa texto del cliente a otra cosa.
- **D5. `play` es asíncrono (202).** La resolución tarda hasta `plugin.resolve_timeout` (30 s). El endpoint responde 202 de inmediato y el móvil consulta `/api/status` cada 2 s. El resultado ("Reproduciendo en TV", "No disponible", "Error al reproducir") ya queda en el texto de estado de la tarjeta, que se expone en `status.message`.
- **D6. Elegir con algo en curso → 409.** Igual que en local, hay que detener antes. `stop` está siempre a un toque en la página.
- **D7. Seguridad sin login (solo LAN).** Valida en el borde: método y ruta con lista cerrada; id con tipo `str`, longitud ≤ 120 y existente en la lista actual; cuerpo ≤ 4 KB; los POST exigen `Content-Type: application/json`. Eso fuerza preflight CORS en cualquier página web maliciosa y el servidor **no** emite cabeceras CORS, así que una web externa abierta en el móvil no puede disparar `play`/`stop`. El bind por defecto es `0.0.0.0` (necesario para entrar desde el móvil) y es configurable. Aviso en README: no exponer el puerto a internet.
- **D8. Solo modo `agenda`.** `ChannelsRoot` (modo `canales`) no se cablea y se registra un log informativo. Queda fuera de alcance.
- **D9. Fallo al abrir el puerto no tumba el kiosk.** Si `OSError` al hacer bind (puerto ocupado), `log.warning` y la UI táctil sigue funcionando.
- **D10. Frontend: una página HTML estática con JS plano**, sin framework ni CDN (la Pi puede no tener internet fiable y es LAN). Se sirve desde `ui/remote_page.html`, leída una vez al arrancar el servidor.

## Contrato de la API (JSON, UTF-8, `Cache-Control: no-store`)

Errores: `{"ok": false, "error": "<codigo>", "message": "<texto en español>"}`.

| Método y ruta | Respuesta OK | Errores |
|---|---|---|
| `GET /` | HTML de `ui/remote_page.html` | |
| `GET /api/matches` | 200 `{"ok":true,"loaded":bool,"source":"plugin"\|"espn"\|null,"matches":[Match,...]}` | |
| `GET /api/status` | 200 `{"ok":true,"state":"idle"\|"resolving"\|"playing","match":Match\|null,"message":str\|null,"source_label":str\|null}` | |
| `POST /api/play` cuerpo `{"id":"<match id>"}` | 202 `{"ok":true,"state":"resolving","match":Match}` | 400 `bad_request` (JSON inválido, sin `id`, `id` no string o muy largo), 404 `unknown_match`, 409 `busy` (algo resolviéndose o reproduciéndose), 409 `not_playable` (finalizado o aún no en horario; el mensaje incluye la hora, igual que la tarjeta), 413 `too_large`, 415 `unsupported_media_type` |
| `POST /api/stop` (cuerpo `{}` o vacío) | 200 `{"ok":true,"state":"idle"}`. Idempotente: en `idle` devuelve 200 sin hacer nada | 409 `busy` si está `resolving` (no hay forma de cancelar la resolución hoy) |
| cualquier otra ruta o método | | 404 `not_found` / 405 `method_not_allowed` |
| fallo interno / UI no responde en 5 s | | 500 `internal` / 503 `ui_timeout` |

`Match`: `{"id","title","away","home","league":"NFL","state":"pre"|"in"|"post","status_text","start":"<ISO UTC>"|null,"playable":bool,"unavailable":bool}`.
- `away`/`home` = `away_name`/`home_name`; `status_text` = `game.status_text()`; `playable` = `game.is_playable()`; `unavailable` = `UnavailableTracker.is_unavailable(card.event_id)`.
- `loaded` es `false` mientras `KioskRoot._signature is None` (aún no terminó el primer refresco) y la página muestra "Cargando partidos…".

Estado (`state`):
- `resolving` si alguna `card._busy`.
- `playing` si `_overlay_visible` y `_overlay.card` no es `None`; `match` es esa tarjeta y `source_label` sale de `source_label(card._switcher.index())` si hay switcher.
- `idle` en cualquier otro caso.
- `message` es el último texto de estado de la tarjeta (ver paso 3), o `null`.

## Claves de configuración (`config/kiosk.yaml`, sección nueva `remote:`)

```yaml
remote:
  enabled: true         # false desactiva el control remoto web
  bind: "0.0.0.0"       # dirección de escucha ("127.0.0.1" = solo local)
  port: 8080            # puerto TCP (1-65535)
```

Defaults en `RemoteConfig`: `enabled=True`, `bind="0.0.0.0"`, `port=8080`. Un valor inválido da `log.warning` y usa el default, como el resto del archivo (R8.3). El puerto 8080 no choca con el plugin (7000).

## Pasos (en orden de dependencia)

- [ ] 1. Configuración `remote.*`.
      Añadir `RemoteConfig` (`enabled: bool`, `bind: str`, `port: int`) y el campo `remote` en `KioskConfig`. Añadir el helper `_as_bool(section, key, default)` (rechaza no-bool con warning) si aún no existe, y `_as_port(section, key, default)` (entero 1-65535, rechaza bool). Leer la sección `remote` en `load_kiosk_config` con `_as_section` y `_as_str` para `bind`. Añadir la sección documentada y comentada en español a `config/kiosk.yaml`. Extender `tests/test_kiosk_config.py` siguiendo sus patrones: defaults sin sección, valores válidos, `port: 0`, `port: 70000`, `port: true`, `enabled: "si"`, `bind: ""`.
      Files: `scraper/kiosk_config.py`, `config/kiosk.yaml`, `tests/test_kiosk_config.py`
      Verify: `PYTHONPATH=. python3 -m unittest discover -s tests` — todos pasan, incluidos los nuevos.

- [ ] 2. Módulo `ui/remote.py` (sin Kivy) con la lógica y el servidor HTTP.
      Contenido:
      - `match_id(game) -> str` (D4).
      - `match_to_dict(game, unavailable: bool) -> dict`.
      - Protocolo documentado `RemoteBackend` con cuatro métodos que ejecuta el hilo de Kivy: `list_matches() -> dict` (`loaded`, `source`, `matches` ya como dicts), `get_status() -> dict`, `play(match_id) -> tuple[int, dict]` (código y cuerpo, aplica 404/409 de la tabla) y `stop() -> tuple[int, dict]`.
      - `RemoteServer(backend, run_on_ui, bind, port, page_html)`: `ThreadingHTTPServer` con `daemon_threads = True`; handler `BaseHTTPRequestHandler` con las rutas y errores de la tabla (límite de cuerpo 4 KB, Content-Type JSON en POST, `Cache-Control: no-store`, sin cabeceras CORS, `log_message` redirigido a `log.debug`). Métodos `start()` (hilo daemon `serve_forever`; devuelve `False` y hace `log.warning` si el bind falla con `OSError`) y `stop()` (`shutdown()` + `server_close()`).
      - Todas las llamadas al backend pasan por `run_on_ui(lambda: backend.xxx(...))`; una excepción de timeout se traduce a 503.
      Crear `tests/test_remote.py` (unittest, patrón de `tests/test_playback.py`: `sys.path.insert` al inicio). Usar un `FakeBackend` y un `run_on_ui` directo, y levantar `RemoteServer` real en `127.0.0.1` con puerto 0 (leer `server.server_address`) con `urllib.request`. Casos: `GET /api/matches` y `/api/status`; `play` OK devuelve 202; id desconocido 404; id no string, sin `id` o JSON roto 400; id de 500 caracteres 400; POST sin `Content-Type: application/json` 415; cuerpo > 4 KB 413; `busy` y `not_playable` pasan con 409; `stop` idempotente; ruta desconocida 404; método no permitido 405; `GET /` devuelve HTML; ausencia de cabecera `Access-Control-Allow-Origin`; `match_id` con y sin `event_id`; bind a un puerto ocupado devuelve `False` sin lanzar.
      Files: `ui/remote.py`, `tests/test_remote.py`
      Verify: `PYTHONPATH=. python3 -m unittest discover -s tests` — todos pasan (el módulo no importa Kivy, corre en esta máquina).

- [ ] 3. Página móvil.
      Crear `ui/remote_page.html` (HTML + CSS + JS en un solo archivo, sin librerías ni CDN, `lang="es"`, `<meta name="viewport" content="width=device-width, initial-scale=1">`, tema oscuro con la paleta de `ui/app.py`). Comportamiento:
      - Lista de tarjetas grandes (≥ 64 px de alto, tocables con el pulgar) con título, hora/estado (`status_text`), etiqueta EN VIVO si `state == "in"`; las no jugables o `unavailable` aparecen atenuadas con su texto.
      - Banner fijo arriba con el estado: "Nada en reproducción", "Buscando stream…" o "Reproduciendo: <título>" (+ `source_label`), y botón grande **Detener** visible cuando `state != "idle"`.
      - Al tocar una tarjeta: `POST /api/play`; si 409 `busy`, mostrar el `message` ("detén primero") sin recargar; el resto de errores se muestran en un aviso (toast).
      - Sondeo con `fetch`: `/api/status` cada 2 s y `/api/matches` cada 15 s y al cambiar `state`. Si el fetch falla, mostrar "Sin conexión con la Raspberry" y reintentar. Pausar el sondeo con `document.hidden`.
      - Todo el texto remoto se inserta con `textContent`, nunca `innerHTML`.
      Files: `ui/remote_page.html`
      Verify: `python3 - <<'EOF'` que arranca `RemoteServer` con `FakeBackend` (reutilizar el de `tests/test_remote.py`) y `curl -s http://127.0.0.1:<puerto>/` devuelve el HTML; abrirlo en el navegador del escritorio (modo móvil de devtools) y comprobar a mano lista, play, stop y error 409. Dejar el resultado en el informe del implementador.

- [ ] 4. Cablear el backend en `KioskRoot` y arrancar el servidor.
      En `ui/app.py`:
      - `KioskRoot._run_on_ui(fn, timeout=5)` (D2) y `_remote_*` como adaptador del protocolo `RemoteBackend`: `remote_list_matches` (arma los dicts desde `self._cards`, `self._unavailable`, `self._signature`, y `source` desde un nuevo atributo `self._from_plugin` guardado en `_rebuild_cards`), `remote_status`, `remote_play(match_id)` (busca la tarjeta por `match_id(card.game)`; 404, luego 409 `busy` si hay `_busy` o overlay visible, luego 409 `not_playable` con el mismo texto de `_on_select`, y si todo está bien llama a `self._on_select(card)` y devuelve 202), `remote_stop` (idle → 200; resolving → 409; playing → `self._on_overlay_stop(self._overlay)` y 200).
      - En `MatchCard.set_status` guardar también `self.status_text = text` (atributo nuevo, inicializado en `__init__` con `game.status_text()`); `status.message` lo lee de ahí (solo para la tarjeta activa o la `_busy`).
      - En `ScrapperApp.build`, solo en modo `agenda` y si `root._config.remote.enabled`, crear `RemoteServer(...)` con `page_html` leído de `ui/remote_page.html` (ruta relativa a `Path(__file__)`), llamar a `start()` y guardar `self.remote`. Si arranca, `log.info("Control remoto en http://<bind>:<port>")`. En `on_stop`, `self.remote.stop()` antes de `player.stop()`.
      - No reordenar ni reescribir `_on_select`, `_resolve_and_play` ni `_on_overlay_stop`.
      Files: `ui/app.py`
      Verify: `python3 -m py_compile ui/app.py ui/remote.py` y `PYTHONPATH=. python3 -m unittest discover -s tests` y `PYTHONPATH=. python3 -m unittest discover -s scraper -p "test_*.py" -t .` — todo en verde. (Kivy no está instalado aquí, así que el cableado se prueba en la Pi en el paso 6.)

- [ ] 5. Documentar en `README.md`.
      Añadir una sección "Control remoto desde el móvil" (y entrada en el índice) en español: abrir `http://<ip-de-la-pi>:8080` (la IP de referencia del README es `10.10.0.246`) desde el móvil en la misma red Wi-Fi; qué hace (lista, elegir, detener, estado); que para cambiar de partido hay que detener antes (igual que en la táctil); tabla de claves `remote.enabled`, `remote.bind`, `remote.port` con ejemplo YAML; tabla breve de la API (`GET /api/matches`, `GET /api/status`, `POST /api/play`, `POST /api/stop`) con un ejemplo `curl`; nota de seguridad (sin autenticación, solo red local, no abrir el puerto en el router, `bind: "127.0.0.1"` para desactivar el acceso por LAN); alcance (volumen y pausa siguen en la TV/mando; app Tizen fuera de alcance; modo `canales` sin remoto). Añadir `ui/remote.py` y `ui/remote_page.html` al árbol de "Estructura del repositorio" y una línea al diagrama/lista de componentes.
      Files: `README.md`
      Verify: revisar que los comandos `curl` y las claves documentadas coinciden con el contrato de arriba (ejecutar los `curl` contra el servidor del paso 3 con `FakeBackend`).

- [ ] 6. Verificación end-to-end en la Pi (manual, no automatizable aquí).
      Con la app reiniciada en la Pi, desde el móvil: (a) `http://<pi-ip>:8080` carga la lista y coincide con la de la táctil; (b) tocar un partido en juego lo reproduce en la TV y la táctil muestra el overlay; (c) el estado pasa por "Buscando stream…" y "Reproduciendo"; (d) elegir otro partido con uno en curso da el aviso de detener; (e) Detener en el móvil devuelve la táctil a la lista y libera `/run/scrapper/playback.lock`; (f) Detener en la táctil se refleja en el móvil en ≤ 2 s; (g) un partido sin stream queda "No disponible" en ambos; (h) `curl -X POST -d '{"id":"x"}' -H 'Content-Type: application/json' http://<pi-ip>:8080/api/play` da 404 y sin el header da 415; (i) con `remote.port` ocupado el kiosk arranca igual y el log muestra el warning.
      Files: ninguno
      Verify: lista de comprobaciones anterior; anotar el resultado en el informe final. Si no hay acceso a la Pi, declarar este paso como pendiente en vez de darlo por hecho.

## Fuera de alcance

App Tizen, autenticación, volumen/pausa, botón "Siguiente fuente" remoto (la API puede añadirlo luego como `POST /api/next` sobre `_on_overlay_next`), cambios en el scraping, refactors de `ui/app.py`, cambios en `live-sport-plugin`, soporte del modo `canales`.
