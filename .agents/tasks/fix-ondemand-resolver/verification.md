# Verificación — fix on-demand resolver

## Causa raíz confirmada (coincide con el plan)
El markup del listado NO cambió. Lo que falla es **cómo se descarga** el
listado: `_find_match_url` lo pedía con `requests` + `make_session()`, y
`https://v2.streameast.ga/nfl-streams/` ahora responde con el reto "managed
challenge" de Cloudflare (HTTP 403), que `requests` no puede resolver. Al no
recibir el listado real, no hay enlaces `/nfl/<a>-vs-<b>/`, `_find_match_url`
devuelve `None` y la UI muestra "Stream no disponible aún".

## El cambio (mínimo)
`scraper/ondemand.py`:
- Nuevo `_fetch_listing_html(listing_url, timeout)`: lanza Chromium headless
  (mismo patrón que `resolve_match`: `_chromium_host_rules(DEFAULT_DOH_HOSTS)` +
  `user_agent=_DEFAULT_UA`), hace `goto(..., wait_until="domcontentloaded")`,
  espera `_LISTING_WAIT_MS` (12 s) para que Cloudflare libere y rendericen las
  tarjetas, y devuelve `page.content()`; `None` ante cualquier fallo.
- Lógica de parseo extraída a `_pick_match_url(html, listing_url, a_terms,
  b_terms)` — regex `_MATCH_PATTERN` y matching por términos del slug SIN
  cambios.
- `_find_match_url` ahora: 1) intenta el fetch con navegador; 2) si devuelve
  `None`, cae al fetch con `requests` (igual que antes, útil en redes sin reto);
  3) parsea con `_pick_match_url`.
- Comentarios en español. **No** se tocó `config/sources.yaml` (la ruta
  on-demand usa las constantes de `ondemand.py` y los patrones siguen correctos;
  cambiarlo afectaría la ruta híbrida, fuera de alcance).

Opción tomada para el paso 3 del plan: se mantuvo el helper independiente
(`_fetch_listing_html` lanza su propio Chromium, y `resolve_match` lanza otro
para la captura del stream). Dos arranques cortos son aceptables para una acción
on-demand iniciada por el usuario, y mantiene el flujo de control simple.

## Qué se ejecutó y resultado

### 1) Parseo del módulo
```
$ python -c "import ast; ast.parse(open('scraper/ondemand.py').read()); print('OK parse')"
OK parse
```

### 2) Reachability de la fuente desde este entorno (confirma la causa)
```
$ python - <<'PY'  # requests al listado con el UA del scraper
status 403 len 5485
cloudflare_challenge: True
has_vs_link: False
PY
```
→ La fuente SÍ es alcanzable, pero devuelve el reto Cloudflare (403, sin enlaces
`-vs-`). Exactamente lo que rompe el fetch con `requests`.

### 3) Evidencia ANTES=None / DESPUÉS=StreamResult(url) sobre la lógica real
```
$ PYTHONPATH=. python -   # _pick_match_url, la MISMA lógica de parseo
ANTES (cuerpo Cloudflare via requests): None
DESPUES (markup renderizado por navegador): https://v2.streameast.ga/nfl/las-vegas-raiders-vs-new-england-patriots/
```
- ANTES: con el cuerpo que `requests` obtiene hoy (reto Cloudflare) → `None`
  (el fallo que ve el usuario).
- DESPUÉS: con el markup que un navegador real renderiza → la URL de partido
  correcta. El fix enruta la descarga por Playwright, de modo que lo que llega al
  parser es este markup y no la página de reto.

### 4) Prueba OFFLINE con fixture del markup real
No hay runner de tests en el proyecto y `pytest` no está instalado, así que la
prueba es un script autónomo con `assert` (también compatible con pytest).
```
$ PYTHONPATH=. python tests/test_ondemand_find_match.py
OK: 3 comprobaciones pasaron
```
Fixture: `tests/fixtures/streameast_nfl_listing.html` (anclas reales de hoy +
un decoy `/nfl/teams/las-vegas-raiders-live-streams/`). Asserts:
(a) un partido en vivo resuelve a `/nfl/<a>-vs-<b>/`;
(b) el decoy `/nfl/teams/...` nunca se devuelve;
(c) términos que no casan devuelven `None`.

### Otros tests existentes
No había tests previos en el repo (`find … test_*.py` solo encuentra el nuevo).

## Limitaciones (verificación en el dispositivo — REQUERIDA)
- **Playwright NO está instalado en este entorno de desarrollo**, por lo que no
  se pudo ejecutar `resolve_match` de extremo a extremo (ni el fetch del listado
  con navegador ni la captura del `.m3u8`). La causa raíz y la lógica de parseo
  sí se validaron en vivo (secciones 2 y 3) y offline (sección 4).
- El token del `.m3u8` es efímero y solo se expone cuando el reproductor del
  iframe pide el manifiesto, lo que requiere un partido **en curso** y la red +
  DoH de la Raspberry Pi.

### Comprobación en vivo pendiente EN LA PI (kiosk parado, venv activo)
```
cd ~/scrapper
PYTHONPATH=~/scrapper ~/scrapper-venv/bin/python - <<'PY'
from scraper.ondemand import resolve_match
r = resolve_match(["lv","las","vegas","raiders"],
                  ["ne","new","england","patriots"])   # usar un partido EN VIVO hoy
print(r)
PY
```
Esperado: `StreamResult(url="https://....m3u8", referrer="https://v2.streameast.ga/")`
para un partido realmente en vivo (un partido que no ha empezado devuelve `None`
de forma legítima). Confirmar en la UI que tocar una tarjeta en vivo ahora
reproduce en lugar de mostrar "Stream no disponible aún".

## Nota (fuera de alcance)
`scraper/resolvers.py` `_resolve_hybrid` (pre-scrape horario) descarga el mismo
listado con `requests` + `make_session`, así que sufre el mismo 403 de
Cloudflare. No se tocó en esta tarea; conviene programarlo por separado.
