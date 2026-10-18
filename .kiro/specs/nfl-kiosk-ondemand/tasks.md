# Plan de implementación — NFL Kiosk bajo demanda

Tareas incrementales para implementar el diseño. El orden construye de abajo
hacia arriba (config → clientes → selección → UI/controles → operación), de modo
que cada tarea sea verificable sobre la anterior. Cada tarea referencia los
requisitos que cubre.

Convenciones:
- Rutas relativas a la raíz del workspace `/home/karlos/Kiro/scrapper/`.
- El submódulo del plugin está en `live-sport-plugin/live-sport-plugin/`.
- No se añaden dependencias de Python nuevas (CSV con `requests` + `csv`; YAML con
  PyYAML ya presente).

---

- [x] 1. Configuración centralizada del kiosk
  - Crear `config/kiosk.yaml` con las secciones `plugin`, `playback`, `schedule`
    y sus defaults (url `http://localhost:7000`, resolve_timeout 30, retry_delay 4,
    max_resolution 1080, csv_url de nflverse, cache_ttl 1 semana, timezone
    `America/Mexico_City`).
  - Crear `scraper/kiosk_config.py`: lector con PyYAML que devuelve un objeto de
    config con defaults seguros y registra advertencia si falta el archivo o un
    valor es inválido.
  - _Requisitos: R8.1, R8.2, R8.3; R6.3; R11.5_

- [x] 2. PluginClient — cliente del addon (protocolo Stremio)
- [x] 2.1 Esqueleto y healthcheck
  - Crear `scraper/plugin_client.py` con `PluginClient(base_url, timeout)` y
    `is_up()` (GET `/health` o `/manifest.json`), devolviendo False sin excepción
    si el plugin no responde.
  - Definir el `@dataclass Stream` (url, source, resolution_height, referer,
    origin, label).
  - _Requisitos: R3.4; R6.1_
- [x] 2.2 Listado de la jornada NFL
  - Implementar `list_nfl_gameweek()`: GET
    `/catalog/tv/nuvio_sports_american_football.json`, mapear `metas` al esquema de
    evento del proyecto (id, title, sport, start_time desde `released`, status),
    aplicar heurística de título para quedarse con NFL, y filtrar por la ventana de
    jornada (martes→lunes que contiene hoy, en la zona del kiosk).
  - _Requisitos: R4.1; R11.1; (encapsulación Stremio)_
- [x] 2.3 Resolución de streams de un partido
  - Implementar `resolve_streams(event_id)`: GET `/stream/tv/<event_id>.json`,
    descartar la entrada "Support Us" y los streams sin `url` reproducible y los
    `Web Stream` (iframe); parsear `source` (provider) del title; leer
    `behaviorHints.proxyHeaders.request` → `referer`/`origin`; preservar el orden
    (ya viene rankeado). Añadir un reintento tras `retry_delay` si la primera
    llamada trae pocas fuentes.
  - _Requisitos: R6.1; R7.1_

- [x] 3. ResolutionPolicy — selección del mejor stream ≤ tope
  - Crear `scraper/resolution_policy.py` con `pick_best(streams, max_height)`:
    parsear altura (`WxH`→H, `Np`→N, `HD`/None→desconocida, fallback regex sobre
    label), candidatos = altura ≤ tope o desconocida, devolver el primero (ya
    ordenados); si todos exceden el tope, el de menor altura; `None` si lista vacía.
  - _Requisitos: R6.2, R6.3, R6.5_

- [x] 4. SourceSwitcher — lista rankeada y salto de fuente
  - Crear `scraper/source_switcher.py` con `current()`, `next()`, `has_next()`,
    `index()`. Selección inicial vía `ResolutionPolicy.pick_best`; `next()` avanza
    respetando el tope; `None` cuando se agotan.
  - _Requisitos: R7.1, R7.2, R7.4, R7.5; R9.3, R9.4, R9.5_

- [x] 5. NflScheduleClient — fixtures de nflverse (CSV)
- [x] 5.1 Descarga y caché semanal del CSV
  - Crear `scraper/nfl_schedule.py` con `NflScheduleClient(csv_url, cache_ttl, tz)`.
    Descargar el CSV con `requests`, guardarlo en `data/` con marca de tiempo, y
    reutilizar la caché mientras no supere el TTL (1 semana). Degradación suave: si
    la descarga falla, operar con `[]` y registrar en log.
  - _Requisitos: R11.5, R11.6_
- [x] 5.2 Jornada en curso y modelo ScheduleGame
  - Definir `@dataclass ScheduleGame` (game_id, gameday, start_time, home_team,
    away_team). Implementar `gameweek_games()`: calcular la ventana martes→lunes que
    contiene hoy (en `tz`), filtrar `gameday` dentro de la ventana, y construir
    `start_time` combinando `gameday`+`gametime` convertido de ET a `tz`.
  - _Requisitos: R11.1, R11.3; R4.1_
- [x] 5.3 Enriquecimiento de eventos
  - Implementar `enrich(event)`: emparejar el evento del plugin con un
    `ScheduleGame` por código/nombre de equipo (reutilizar la lógica de términos de
    `ui/teams.py`), y rellenar `start_time` y nombres de equipo faltantes. No usar
    scores para estado live.
  - _Requisitos: R11.2, R11.4_

- [x] 6. Integración en la UI — listado de la jornada
  - En `ui/app.py`, cambiar `KioskRoot.refresh()` para: obtener partidos vía
    `PluginClient.list_nfl_gameweek()` (con fallback a `events.json`/scraper si el
    plugin no responde o devuelve vacío), enriquecer con
    `NflScheduleClient.enrich()`, ordenar híbrido (en vivo/hoy arriba, próximos
    debajo) y pintar `MatchCard`. Guardar el `event_id` del plugin en cada tarjeta.
    Mostrar el estado "no disponible" de tarjetas previamente marcadas.
  - _Requisitos: R4.1, R4.2, R4.3, R4.4, R4.5, R4.6; R5.1; R11.7_

- [x] 7. Integración en la UI — resolución y reproducción bajo demanda
  - En `ui/app.py`, cambiar `KioskRoot._resolve_and_play` para: resolver con
    `PluginClient.resolve_streams(event_id)` (con reintento); si vacío, fallback a
    `scraper/ondemand.resolve_match(a_terms, b_terms)` envuelto como `Stream`;
    construir `SourceSwitcher`; elegir el mejor con `ResolutionPolicy`; reproducir
    en mpv. Aplicar timeout de 30s: si no hay stream reproducible, marcar la tarjeta
    "no disponible" (con `set_status` existente) y volver a la lista.
  - _Requisitos: R5.2, R5.4; R6.1, R6.2, R6.4, R6.6_

- [x] 8. Soporte de Origin en el reproductor mpv
  - En `player/mpv_player.py`, añadir soporte opcional para `Origin` (además del
    `referrer` ya soportado): si el `Stream` trae `origin`, pasar
    `--http-header-fields='Origin: <origin>'`. Sin cambios en la salida de pantalla.
  - _Requisitos: R6.4_

- [x] 9. Overlay de controles de reproducción (pantalla 7")
  - En `ui/app.py`, añadir un overlay con dos botones grandes mientras hay
    reproducción activa: **Detener** (`player.stop()`, liberar lock, volver a la
    lista) y **Siguiente streaming** (`SourceSwitcher.next()`; si no hay más,
    deshabilitar/avisar). Opcional: indicador "fuente N de M" vía `index()`.
  - _Requisitos: R7.2, R7.3, R7.4; R9.1, R9.2, R9.3, R9.4, R9.5_

- [x] 10. Lock de reproducción en /run/
- [x] 10.1 Gestión del lock desde la app
  - Crear un helper (p. ej. `scraper/playback_lock.py`) que escriba el PID de mpv
    en `/run/scrapper/playback.lock` al iniciar reproducción y lo limpie al detener
    o al terminar el stream (la UI vigila `player.is_playing()`). Crear
    `/run/scrapper/` si no existe.
  - _Requisitos: R10.1, R10.2_
- [x] 10.2 Lectura y validación de lock obsoleto
  - Añadir al helper una función que lea el lock y verifique que el PID sigue vivo;
    lock con PID muerto = obsoleto (se ignora). Esta función la consumirán los
    scripts de update.
  - _Requisitos: R10.3, R10.4_

- [x] 11. Servicio Node del plugin (systemd)
  - Crear `setup/plugin.service` (según el diseño: WorkingDirectory del submódulo,
    `PORT=7000`, `ADDON_URL=http://localhost:7000`, `DONATION_URL=disabled`,
    `ExecStart=node dist/index.js`, `Restart=always`).
  - Crear `setup/10-plugin-service.sh`: instalar Node ≥ 22 si falta, `npm ci` +
    `npm run build` (ncc + WASM) ANTES de activar el servicio, e instalar/activar la
    unidad.
  - _Requisitos: R3.1, R3.2, R3.3, R3.5_

- [x] 12. Auto-update del repo propio (timer + script + aplicar pendiente)
- [x] 12.1 Script de actualización
  - Crear `setup/update-repo.sh`: comprobar lock (usando el helper de 10.2); si hay
    reproducción activa → marcar PENDING y salir. Si no: `git fetch origin main`,
    salir si no hay cambios, abortar si el working tree impide fast-forward,
    `merge --ff-only`, `git submodule update --init` si cambió, reiniciar servicios
    (UI + plugin), limpiar PENDING. Capturar fallos sin dejar el kiosk caído.
  - Crear el enlace `update-now` (invocación manual del mismo script; `--force`
    respeta igualmente el lock de reproducción).
  - _Requisitos: R1.1, R1.2, R1.3, R1.4, R1.5, R1.6, R1.7; R1.8; R10.3_
- [x] 12.2 Unidades systemd del timer
  - Crear `setup/scrapper-update.service` (oneshot que corre el script) y
    `setup/scrapper-update.timer` (`OnCalendar=hourly`, `Persistent=true`), más un
    script de instalación/activación.
  - _Requisitos: R1.1_
- [x] 12.3 Aplicar actualización pendiente al terminar la reproducción
  - En la UI, al liberar el lock (Detener o fin de stream), si existe marca PENDING,
    disparar `scrapper-update.service` de inmediato (sin esperar al siguiente tick).
  - _Requisitos: R1.9, R1.10; R10.2_

- [x] 13. Update controlado del plugin desde upstream
  - Crear `setup/update-plugin.sh`: comprobar lock (marcar PENDING plugin si hay
    reproducción); `git -C <submódulo> fetch upstream`, `merge --ff-only
    upstream/main`, `push origin main` (fork), `npm ci && npm run build` (rebuild
    WASM) antes de reiniciar, en el repo raíz `git add <submódulo>` + commit del
    bump, `systemctl restart plugin.service`. NO se invoca desde el timer horario.
  - _Requisitos: R2.1, R2.2, R2.3, R2.4, R2.5; R10.3_

- [x] 14. Verificación end-to-end
  - Con el plugin corriendo localmente, verificar el flujo completo: listar la
    jornada NFL (con enriquecimiento de equipos/horarios), tocar un partido →
    reproducir el mejor stream ≤ 1080 en mpv, usar "Siguiente streaming", "Detener",
    y el timeout de 30s con marcado "no disponible".
  - Verificar que una actualización disparada durante una reproducción queda
    pendiente y se aplica al pulsar Detener.
  - Verificar degradación suave: plugin caído → fallback al scraper; CSV no
    disponible → lista sin enriquecer.
  - _Requisitos: R1.9; R4; R5; R6; R7; R9; R10; R11.6_
