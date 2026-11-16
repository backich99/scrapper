# Verificación: control remoto web desde el móvil

Ejecutado en `/home/karlos/Kiro/scrapper` (Python 3.15, sin Kivy). Salidas reales.

## Compilación y tests

```
$ python3 -m py_compile ui/app.py ui/remote.py scraper/kiosk_config.py
(exit 0)

$ PYTHONPATH=. python3 -m unittest discover -s tests
Ran 69 tests in 1.235s
OK

$ python3 tests/test_kiosk_config.py | tail -1
OK: 9 comprobaciones pasaron

$ PYTHONPATH=. python3 -m unittest discover -s scraper -p "test_*.py" -t .
Ran 160 tests in 0.018s
OK
```

Antes de convertir `tests/test_kiosk_config.py` a `unittest` (clase `KioskConfigTests` que envuelve las funciones) el descubrimiento daba 60 tests; ahora 69 (los 9 de configuración quedan incluidos).

## API con curl (RemoteServer real + FakeBackend de tests/test_remote.py, 127.0.0.1:8765)

```
GET /            -> 200, Content-Type text/html; charset=utf-8, Cache-Control: no-store, 5996 bytes
GET /api/matches -> {"ok": true, "loaded": true, "source": "plugin", "matches": [{"id": "abc", "title": "Dallas vs Philadelphia", ... "state": "in", "playable": true, "unavailable": false}]}
GET /api/status  -> {"ok": true, "state": "idle", "match": null, "message": null, "source_label": null}
POST /api/play {"id":"abc"}            -> 202 {"ok": true, "state": "resolving", "match": null}
POST /api/play {"id":"x"}              -> 404 unknown_match
POST /api/play sin Content-Type JSON   -> 415 unsupported_media_type
POST /api/play {                       -> 400 bad_request (JSON inválido)
POST /api/stop {}                      -> 200 {"ok": true, "state": "idle"}
GET /nope                              -> 404 not_found
Cabecera Access-Control-Allow-Origin   -> ausente
```

Nota: el `match: null` de `play` viene del `FakeBackend`, no del código real de `ui/app.py`.

## Alcance de git

- `git diff --stat HEAD -- live-sport-plugin`: vacío. `git status --short` no muestra nada del submódulo.
- Archivos del commit: `README.md`, `config/kiosk.yaml`, `scraper/kiosk_config.py`, `tests/test_kiosk_config.py`, `tests/test_remote.py`, `ui/app.py`, `ui/remote.py`, `ui/remote_page.html`, `.agents/tasks/phone-remote/`. `.agents/tasks/failover-sources/` queda fuera.

## No ejecutado

- `ui/app.py` (cableado Kivy: `_run_on_ui`, `remote_play`, `remote_stop`, arranque en `build`/`on_stop`) solo se compiló; Kivy no está instalado aquí.
- La página no se abrió en un navegador (solo se comprobó que se sirve).
- Paso 6 del plan (prueba en la Pi desde el móvil): pendiente, requiere hardware.
