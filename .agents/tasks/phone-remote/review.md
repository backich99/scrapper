# Control remoto web desde el móvil para el kiosk de la Pi

El cambio añade un servidor HTTP embebido (`ui/remote.py`), una página móvil, claves `remote.*` en la configuración y documentación en el README. Reutiliza `_on_select` y `_on_overlay_stop` sin duplicar scraping. No hay evidencia ejecutable de que funcione, y según `verification.md` los cambios no están commiteados.

Watch for: (1) no se ejecutó ningún test, `py_compile`, `curl` ni render; el propio coder lo declara (confirmed). (2) No existe commit: `verification.md` deja `git add`/`git commit` como pendientes del usuario, así que no hay "último commit" que aprobar (confirmed). (3) `tests/test_kiosk_config.py` usa funciones sueltas que `unittest discover` no recoge, así que los tests de configuración no están en el comando de verificación del plan (confirmed, según el coder).

**Verdict**: NEEDS_CHANGES

## High-level view

El diseño sigue el plan: servidor stdlib en un hilo daemon dentro del proceso Kivy, operaciones ejecutadas en el hilo de UI mediante `_run_on_ui`, y `play`/`stop` delegados a los mismos métodos que usa la pantalla táctil. El id de partido es solo una clave de búsqueda sobre `_cards`, así que se valida contra la lista actual. La seguridad en LAN se apoya en exigir `application/json` y en no emitir cabeceras CORS. Bind y puerto son configurables (`remote.bind`, `remote.port`, defecto `0.0.0.0:8080`) y el README lo documenta.

El problema es la evidencia. Según `verification.md`, la sesión del coder no pudo ejecutar ningún comando, y la conformidad con el plan se afirma solo por lectura estática. Esta revisión tampoco pudo ejecutar comandos: el shell devolvió código -1 sin salida, y no se pudo comprobar el estado de git ni hacer una prueba puntual. Sin resultados de tests, de `curl` ni de `git diff` sobre `live-sport-plugin`, no se puede confirmar que el código funcione ni que el alcance esté limpio.

<details>
<summary>Issues (4)</summary>

1. **Sin evidencia de ejecución** — el coder no corrió tests, `py_compile`, servidor con `curl` ni render de la página. Ejecutar los comandos de verificación y pegar las salidas reales en `verification.md`.
2. **Sin commit** — los cambios están sin commitear (`git add`/`git commit` listados como pendientes). Crear el commit (sin push) para que pueda revisarse.
3. **Alcance sin verificar** — la ausencia de cambios en `live-sport-plugin` y de cambios no relacionados solo se comprobó con una búsqueda de texto. Aportar la salida de `git status --short` y de `git diff --stat HEAD~1 -- live-sport-plugin` (debe estar vacía).
4. **Tests de configuración fuera de `unittest discover`** — `tests/test_kiosk_config.py` usa funciones sueltas que `unittest` no recoge. Convertirlos a `unittest.TestCase`, o documentar y ejecutar `python3 tests/test_kiosk_config.py` y adjuntar la salida.

</details>

<details>
<summary>Details</summary>

## Evidencia de verificación ausente

`verification.md` abre con "Nada se pudo ejecutar" y lista como no ejecutados los tests, `py_compile`, el servidor con `curl`, el render, y `git status`/`diff`/`log`/`commit`. Las instrucciones de esta revisión indican rechazar si falta evidencia (confirmed). El cableado de `ui/app.py` (`_run_on_ui`, `remote_play`, `remote_stop`, arranque en `build`) no puede probarse aquí porque Kivy no está instalado. Por eso un `py_compile` y una lectura cuidadosa son lo mínimo exigible antes de aprobar (confirmed).

## Cobertura de tests de configuración

Las pruebas nuevas de `remote.*` en `tests/test_kiosk_config.py` no corren con el comando que fija el plan. Una regresión en `_as_port` o `_as_bool` pasaría inadvertida en `unittest discover -s tests` (confirmed, según el coder; no se pudo abrir el archivo para comprobarlo).

## Lo que no se pudo comprobar

Por la falta de shell, no se verificó: que `start()` devuelva `False` con el puerto ocupado, el 415 y el 413 reales, la ausencia de `Access-Control-Allow-Origin`, ni el arranque del kiosk con `remote.enabled: false`. La revisión estática del coder los describe conformes al plan, pero sin ejecución es solo una afirmación (possible: la implementación es correcta, falta demostrarlo).

</details>

<details>
<summary>File map</summary>

Según `verification.md`: `scraper/kiosk_config.py` (RemoteConfig, `_as_bool`, `_as_port`), `config/kiosk.yaml` (sección `remote:`), `tests/test_kiosk_config.py` (tests de `remote.*`), `ui/remote.py` (servidor y lógica sin Kivy), `ui/remote_page.html` (página móvil), `tests/test_remote.py` (tests HTTP), `ui/app.py` (cableado), `README.md` (sección nueva). Diff completo: `git diff` una vez exista el commit.

</details>
