# live-sport-plugin — Reporte de API HTTP para integración `PluginClient` (Python)

**Alcance:** investigación de solo lectura del addon Stremio/Nuvio `live-sport-plugin`
(submódulo en `/home/karlos/Kiro/scrapper/live-sport-plugin/live-sport-plugin/`, versión
`3.1.3`). Objetivo: permitir escribir un cliente Python (`PluginClient`) que hable HTTP con
el plugin en `http://localhost:7000` y traduzca Stremio → un modelo de evento simple
(`id, title, league, sport, start_time, stream_url, source, status`), sin tener que releer
el código del plugin.

Todas las rutas y números de línea citados son relativas a la raíz del submódulo salvo
indicación contraria. _Content was rephrased for compliance; code snippets are quoted
verbatim as technical evidence._

---

## 0. Resumen ejecutivo (respuesta primero)

1. **El plugin es un addon Stremio v1** servido por Express + `stremio-addon-sdk`. Expone los
   recursos estándar `catalog`, `meta`, `stream` sobre `type = 'tv'` (también acepta `series`
   y `channel`). No hay endpoint REST propio para "listar partidos NFL": se usa el protocolo
   Stremio.

2. **No existe catálogo "NFL".** El deporte NFL cae bajo la categoría interna
   `american_football`. El catálogo relevante es `nuvio_sports_american_football`
   (todos los americanos) o `nuvio_sports_live` / `nuvio_sports_upcoming` filtrando por
   `genre=American Football`. **No hay campo `league` fiable** (muchos proveedores no lo
   traen); distinguir "NFL" en concreto requiere heurística sobre el título/`league`.

3. **La config en la URL (`/:config/...`) es OPCIONAL.** Las rutas sin config
   (`/catalog/tv/<id>.json`, `/stream/tv/<id>.json`, `/manifest.json`) funcionan y el SDK
   entrega `config = {}`. Para pedir sólo NFL **no hace falta** construir el segmento
   lz-string/base64url; basta con filtrar del lado Python o usar `?genre=...`. (La config
   sólo cambia qué catálogos aparecen en el manifest y defaults de orden/idioma.)

4. **Secuencia de uso recomendada para el kiosk:**
   - **(a) Listar NFL de hoy** → `GET /catalog/tv/nuvio_sports_american_football.json`
     (opcional `/catalog/tv/nuvio_sports_live/genre=American%20Football.json`). Filtrar en
     Python por `sport == american_football` y "de hoy"/live según `released`/estado.
   - **(b) Resolver + rankear streams** → tomar `meta.id` del paso (a)
     (`nuvio_sport_<matchId>`) y pedir `GET /stream/tv/nuvio_sport_<matchId>.json`. El plugin
     devuelve **varias fuentes ya rankeadas** (mejor primero).
   - **(c) Elegir el mejor ≤ tope** → parsear la resolución de cada stream, filtrar
     `height <= tope` (default 1080) y quedarse con el primero (ya vienen ordenados por
     calidad/velocidad). Guardar la lista completa para "siguiente fuente".

5. **La `url` de cada stream es, en la gran mayoría de casos, un proxy del propio plugin**
   (`http://localhost:7000/api/manifest?url=...&referer=...&origin=...`), **reproducible por
   mpv tal cual, sin cabeceras extra** — el proxy inyecta Referer/Origin aguas arriba y
   reescribe los segmentos para que pasen también por el plugin. Algunas fuentes devuelven
   `.m3u8` directo upstream (marcadas `notWebReady` con `behaviorHints.proxyHeaders`), que sí
   requieren Referer/Origin; mpv los acepta pasando `--http-header-fields`.

6. **Arranque:** `npm run build` (ncc → `dist/` + copia WASM/public/data), luego `npm start`
   (`node dist/index.js`). Puerto por defecto `7000` (`PORT`). Node >= 22.

---

## 1. Endpoints HTTP

### 1.1 Montaje de rutas (orden importa)

`src/index.js` registra, **en este orden**:

1. Handlers del SDK (`defineCatalogHandler/defineMetaHandler/defineStreamHandler`), líneas 48-50:
   ```js
   builder.defineCatalogHandler(({ type, id, extra, config }) => handleCatalog(type, id, extra, config));
   builder.defineMetaHandler(({ type, id, config })           => handleMeta(type, id, config));
   builder.defineStreamHandler(({ type, id, config })         => handleStream(type, id, config));
   ```
2. Rutas estáticas / de imágenes / colecciones / `/api/*` de servicio (`src/index.js`
   ~líneas 76-660).
3. **Middleware que normaliza el segmento de config** antes del router SDK (`src/index.js`
   líneas 1049-1062) — reescribe `/:config/(catalog|meta|stream)/...` a JSON URL-encoded.
4. Handler directo de sub-catálogos de colección (`/catalog/:type/:id.json`, líneas 1071-1101).
5. **El router del SDK Stremio**: `app.use(getRouter(builder.getInterface()))` (línea 1104).
   Éste sirve `/manifest.json`, `/catalog/...`, `/meta/...`, `/stream/...` (con y sin prefijo
   de config).
6. `routes/watch`, `routes/health` (líneas 1106-1107). `routes/manifest.js` y
   `routes/streamProxy.js` se montan vía el stack de rutas (proveen `/api/manifest`,
   `/api/hlschunk`, `/api/fastmp4`, `/api/mp4proxy`).

### 1.2 Tabla de endpoints

| Endpoint | Método | Params | Respuesta | Evidencia |
|---|---|---|---|---|
| `/manifest.json` y `/:config/manifest.json` | GET | `config` (ruta, opcional); query `rf`,`lg`,`ws` | Manifest Stremio JSON (ver §7) | `src/index.js:896`; `src/manifest.js` |
| `/catalog/:type/:id.json` | GET | `type`=`tv`; `id`=`nuvio_sports_*` | `{ metas: [...], cacheMaxAge, staleRevalidate }` | SDK router; `src/catalog.js:857` |
| `/catalog/:type/:id/:extra.json` | GET | `extra`= `genre=...&search=...&skip=N` (URL-encoded, estilo Stremio) | igual | `src/catalog.js:1259` (parse de `extra`) |
| `/:config/catalog/:type/:id.json` (+ variante `/:extra.json`) | GET | igual + `config` en ruta | igual | `src/index.js:1071` |
| `/meta/:type/:id.json` y `/:config/meta/...` | GET | `id`=`nuvio_sport_<matchId>` (match) o `nuvio_sport_replay_*` (hub) | `{ meta: {...}, cacheMaxAge, staleRevalidate }` | `src/catalog.js:1502` |
| `/stream/:type/:id.json` y `/:config/stream/...` | GET | `id`=`nuvio_sport_<matchId>` (admite sufijo `:hub_N:idx` para replays) | `{ streams: [...], cacheMaxAge:30, staleRevalidate:30, staleError:60 }` | `src/streams.js:665` |
| `/api/manifest` | GET | `url` (m3u8 upstream, obligatorio), `referer`, `origin`, `rck`, `proxyChunks` | m3u8 reescrito (`Content-Type: application/vnd.apple.mpegurl`); 404 si muerto | `src/routes/manifest.js:20` |
| `/api/hlschunk`, `/api/fastmp4`, `/api/mp4proxy` | GET | `url`, `referer`, ... | passthrough de segmentos/bytes | `src/routes/streamProxy.js:333,88,215` |
| `/img?url=...`, `/img/placeholder`, `/img/match`, `/img/sport/:sport`, `/img/collection/:sport`, `/img/date`, `/img/badge` | GET | varios | imagen (jpeg/png/svg); siempre 200 | `src/index.js:301-660` |
| `/collections.json`, `/nuvio-collections.json`, `/api/collections/download` (+ `/:config/...`) | GET | `replayFilter`, `languages` | Nuvio Collections JSON | `src/index.js:126,142` |
| `/api/server-info` | GET | — | `{ baseUrl, localIp?, port? }` (IP/puerto sólo a loopback directo) | `src/index.js:161` |
| `/api/options` | GET | — | `{ languages, englishAlwaysFirst, replayFilters }` | `src/index.js:189` |
| `/api/provider-health` | GET | — | salud de providers + circuit breakers | `src/index.js:218` |
| `/api/matches` | GET | — | **matches crudos del CacheService** (JSON interno), **sólo loopback directo** (403 si no) | `src/index.js:232` |
| `/`, `/configure`, `/:config/configure`, `/donate` | GET | — | HTML estático / redirect | `src/index.js:79-90` |
| `/health` (y afines) | GET | — | healthcheck | `src/routes/health.js` |
| `/watch` | GET | — | página HTML proxy para embeds (clientes web) | `src/routes/watch.js` |

> **Nota práctica para el kiosk:** `/api/matches` entrega el objeto `match` crudo (mismo modelo
> que consume `handleStream`) sin pasar por el mapeo Stremio de `catalog`. Es tentador para una
> integración local, pero es un endpoint de debug (restringido a loopback directo vía
> `isLocalDirectRequest`, `src/index.js:235-237`) y su forma no es contrato estable. **Se
> recomienda usar `/catalog` + `/stream`** (contrato Stremio) como superficie primaria.

---

## 2. Listar partidos NFL de hoy

### 2.1 De dónde salen los partidos
`handleCatalog` → `handleCatalogUncached` (`src/catalog.js:857,1259`) **no scrapea en caliente**:
lee del `CacheService` que un cron (`cronService.ensureFresh()`, SWR) mantiene poblado por
`MatchAggregator`. Sólo se consideran matches con fuentes:
```js
const matches = rawMatches.filter(m => m && Array.isArray(m.sources) && m.sources.length > 0);
```
(`src/catalog.js:1276`).

### 2.2 Qué catálogo corresponde a NFL / americanos
No hay id de catálogo "NFL". En el manifest (`src/manifest.js`) el id por deporte es
`nuvio_sports_american_football` → nombre "🏈 American Football". El filtrado dentro de
`handleCatalogUncached` mapea `categoryMatch = id.replace('nuvio_sports_', '')` y filtra
`m.category === categoryMatch` (`src/catalog.js:1298-1301`), con inclusión extra de canales
24/7 cuyo título contenga `nfl`/`american football`:
```js
if (categoryMatch === 'american_football' && (titleLower.includes('nfl') || titleLower.includes('american football'))) return true;
```
(`src/catalog.js:1320`).

Catálogos útiles para el kiosk:
- `nuvio_sports_american_football` → todos los americanos (incluye college si así vino).
- `nuvio_sports_live` con `extra.genre = "American Football"` → sólo en vivo ahora.
- `nuvio_sports_upcoming` con `extra.genre = "American Football"` → próximos (kickoff futuro).

El `genre` se mapea con `GENRE_TO_CATEGORY` (`src/catalog.js`, `'american football' →
'american_football'`).

### 2.3 Cómo se identifica deporte / liga de cada item
- **Deporte (`sport`):** campo `match.category` (ej. `american_football`, `football`,
  `networks`, ...). Es el que debe mapear el `PluginClient` a `sport`. Definido en
  `MatchEntity` (`src/domain/MatchEntity.js:19`): `this.category = category || 'other'`.
- **Liga (`league`):** `match.league` (string). **Frecuentemente vacío** — `MatchEntity`
  líneas 26-30 lo normaliza desde objeto o string, con default `''`. El propio código advierte
  que Streamed.pk no expone liga (`src/catalog.js:276-281`, comentario en
  `compareCatalogMatches`). **Para "NFL" en concreto, filtrar por título/`league`
  conteniendo "NFL" es la única señal fiable.**
- En la respuesta Stremio (`metas`), `league` **no** se expone como campo propio; aparece
  embebido en `name`/`description`/`genres`. Si el `PluginClient` necesita `league`, debe
  derivarlo del título o usar `/api/matches` (debug) donde `league` sí es un campo.

### 2.4 Live vs upcoming vs replay ("de hoy")
La lógica canónica está en `src/catalog.js`:
- **Live:** `isMatchLive(match)` (`src/catalog.js:118`). Reglas: 24/7 (`category==='networks'`)
  siempre live; estados terminales (`finished/ended/postponed/cancelled`) nunca; **el reloj de
  kickoff es autoritativo** — si `now < kickoff` no está live aunque el flag diga lo contrario;
  si `now <= kickoff + duración_máxima_del_deporte` está live. `american_football` usa ventana
  de 4 h (`SPORT_MAX_DURATION_MS`, `src/catalog.js:52-67`).
- **Upcoming:** `!isMatchLive(m) && getKickoff(m.date) > now` (`src/catalog.js:1281`).
- **Replay:** `isReplayMatch(match)` (`src/catalog.js:168`), requiere fuente
  `replayzone`/`livetv`. Para el kiosk NFL en vivo esto es irrelevante.
- **"De hoy":** el plugin **no** filtra por "today" en el catálogo live/upcoming (eso es un
  concepto de los hubs de replay). El `PluginClient` debe filtrar por fecha del día usando
  `start_time` (ver abajo).

### 2.5 ¿La respuesta de catalog ya trae horarios y equipos?
La respuesta de `catalog` son `metas` producidos por `mapMatchToMetaPreview`
(`src/catalog.js:390+`). Campos relevantes del `meta`:
- `id`: `nuvio_sport_<matchId>` (lo que se usa luego en `/stream`).
- `name`: título (ej. "Team A vs Team B" o nombre del canal).
- `type`: `tv` (o `series` para hubs de replay).
- `poster`, `background`, `logo`, `posterShape`.
- `description`, `genres`.
- **Horario:** `released` / `releaseInfo` cuando hay kickoff. El epoch real se calcula con
  `getKickoff(match.date)` y se formatea; `releasedIso = new Date(ko).toISOString()`
  (`src/catalog.js`, bloque de fecha ~líneas 560-620). Internamente `match.date` es **epoch en
  milisegundos como string** (`MatchEntity` → `toMillis`, `src/domain/MatchEntity.js:7-10,19`).
- **Equipos:** `match.team1`/`match.team2` (`{ name, logo }`) existen en el modelo crudo, y el
  mapper los deriva del título si faltan (`extractTeamsFromTitle`). En el `meta` Stremio los
  equipos **no** salen como campos estructurados; van dentro de `name`. Para equipos/logos
  estructurados hay que usar el modelo crudo (`/api/matches`) o parsear `name`.

**Conclusión del modelo de evento:** `catalog` da `id`, `title`, `sport` (vía heurística si se
quiere `league`), `start_time` (de `released`), `status` (derivable de live/upcoming según el
catálogo consultado o recomputando con el kickoff). Para `stream_url`/`source` hay que llamar a
`/stream` (paso 3).

---

## 3. Resolver streams de un partido

### 3.1 Endpoint y parseo de id
`GET /stream/tv/nuvio_sport_<matchId>.json`. `handleStream` (`src/streams.js:665`):
```js
if ((type !== 'tv' && type !== 'series' && type !== 'channel') || !id.startsWith('nuvio_sport_')) return { streams: [] };
let rawId = id.replace('nuvio_sport_', '');
// soporta sufijo ':hub_N:idx' para episodios de replay
const matchId = rawId; // (parte antes del primer ':')
const match = cacheService.findMatch(matchId);
```
Si el match no existe o no tiene `sources`, devuelve `{ streams: [] }`.

### 3.2 Flujo de resolución
1. `selectSources(match.sources, config)` filtra/ordena las fuentes por prioridad
   (`SOURCE_PRIORITY`, `src/streams.js:30`). Sin config se permiten todos los providers activos.
2. Para cada fuente se mintea en paralelo con `mintVerifiedSources` → `resolveSource`
   (despacha al provider correspondiente) → `verifyStreams` (ping de salud + parseo de
   calidad + sonda de velocidad). (`src/streams.js:120-170`, `resolveSource` ~124,
   `verifyStreams` ~410).
3. Hay **deadlines**: `STREAM_SOFT_DEADLINE_MS` (default 6 s) y `STREAM_HARD_DEADLINE_MS`
   (default 15 s) (`src/streams.js:717-719`). Las fuentes lentas siguen minteándose en
   background y quedan en caché para la siguiente petición → **una segunda llamada suele
   traer más fuentes**.
4. Scoring por stream: `s.score = streamScorer.calculateScore(s, sourceName)` (`src/streams.js`
   dentro de `resolveSource`), más `speedScore` de la sonda.
5. Dedupe, supresión de canales por-equipo pre-partido, ocultar web fallbacks si hay directos.
6. Ordenación final: `streams.sort((a,b) => compareStreams(a, b, languagePriorities))`
   (`src/streams.js:982`).

### 3.3 Forma de cada stream en la respuesta
Respuesta: `{ streams: [...], cacheMaxAge: 30, staleRevalidate: 30, staleError: 60 }`
(`src/streams.js:978`). Cada stream es un objeto Stremio con estos campos observados
(`src/streams.js` bloque `streams.forEach` ~830-960 y `StreamEntity`
`src/domain/StreamEntity.js`):

| Campo | Significado |
|---|---|
| `name` | Grupo/etiqueta corta: `⚡ Direct Stream`, `🌐 Web Stream`, `▶️ YouTube`, o `🧿 Support Us` (entrada de donación en índice 0). |
| `title` | Multilínea legible: `"<icono> <Provider> \| 🎬 <parte> \| 📺 <canal> <bandera>\n📺 Quality: <quality>[ · <bitrate>][\n👥 viewers][\n⏱ <Fast/Average/Slow>]"`. |
| `url` | URL reproducible (normalmente `http://<base>/api/manifest?url=...` proxied; a veces `.m3u8` directo upstream). Presente en streams directos. |
| `externalUrl` | Para web/YouTube (no directos): URL a abrir en navegador. |
| `resolution` | p.ej. `"1920x1080"`, `"1080p"`, `"720p60"`, `"HD"`, `"SD"` (ver §4). |
| `bitrate` | p.ej. `"3.5 Mbps"` / `"850 kbps"` cuando se midió/parseó. |
| `language`, `country` | Si se detectó el canal (`detectChannelCountry`). |
| `score` | Score 0-100 (StreamScoringService). |
| `speedScore`, `downloadMbps`, `segmentTtfbMs`, `targetDuration` | De la sonda de velocidad (opcional). |
| `behaviorHints` | `{ bingeGroup: "nuvio_sport_<matchId>", notWebReady?: true, proxyHeaders?: { request: { Referer, Origin } } }`. |

> El primer elemento de `streams` puede ser la entrada **"🧿 Support Us"** (sólo `name`,
> `title`, `externalUrl`, sin `url`). El `PluginClient` debe **ignorar/filtrar** cualquier
> stream sin `url` reproducible y sin `.m3u8`/externalUrl de vídeo (ver `src/streams.js:985-993`).

### 3.4 ¿Varias fuentes, rankeadas?
Sí. `handleStream` devuelve múltiples streams de múltiples providers, **ya ordenados**. Orden
(de mejor a peor), definido en `compareStreams` (`src/services/LanguagePriorityService.js:259`):
1. **Directos antes que web** (`name === '⚡ Direct Stream'`).
2. **Rango de idioma** (inglés primero; luego los de `config.languages`; luego desconocido;
   luego otros). Sin config → inglés → desconocido → otros.
3. **`rankScore = score*0.8 + speedScore*0.2`** (desc), `streamRankScore`
   (`LanguagePriorityService.js:244-247`).

### 3.5 Dónde está la lógica de ranking/scoring
- Score por stream: `src/services/StreamScoringService.js` (`calculateScore`): base 50;
  +30 si `url` directo / −20 si sólo `externalUrl`; **+20 si 1080p / +10 si 720p / −5 si SD**;
  +15 fuentes fiables (`admin/echo/delta/golf`), +5 `timstreams`.
- Prioridad de **fuente**: `SOURCE_PRIORITY` en `selectSources` (`src/streams.js:30`).
- Orden final (idioma + rankScore): `compareStreams` + `streamRankScore`
  (`src/services/LanguagePriorityService.js`).
- `MainstreamRankingService` (`getMatchTier`) ordena **los partidos del catálogo** (no los
  streams); se usa en `sortCatalogMatches` (`src/catalog.js`).

**Para el kiosk:** basta tomar `streams` en el orden recibido; `streams[0]` reproducible es "la
mejor fuente". Para "siguiente fuente" (Requisito 7) iterar la lista ya resuelta sin volver a
llamar.

---

## 4. Resolución / Calidad

**No hay un único campo numérico limpio.** Fuentes de la resolución, en orden de fiabilidad:

1. **`stream.resolution` desde el master playlist.** `M3U8ParserService.parseManifestText`
   (`src/services/M3U8ParserService.js:78`) lee `#EXT-X-STREAM-INF` y produce
   `resolution: "<w>x<h>"` (ej. `"1920x1080"`) y `qualityTag: "<h>p[fps]"` (ej. `"1080p"`,
   `"1080p60"`), o `qualityTag: 'HD'` si no hay `RESOLUTION`. Esto se aplica en `verifyStreams`
   (`src/streams.js`, bloque `parseManifestText` ~líneas 540-560):
   ```js
   if (parsedQuality.qualityTag) s.quality = parsedQuality.qualityTag;
   if (parsedQuality.resolution) s.resolution = parsedQuality.resolution;
   ```
2. **Upgrade por bitrate** cuando sólo había `'HD'`, dentro de `measureStreamSpeed`
   (`src/streams.js`, ~líneas 350-360):
   ```js
   if (stream.resolution === 'HD' && impliedBitrate > 0) {
     if (impliedBitrate >= 5500000)      stream.resolution = '1080p60';
     else if (impliedBitrate >= 3800000) stream.resolution = '1080p';
     else if (impliedBitrate >= 2000000) stream.resolution = '720p';
     else                                stream.resolution = 'SD';
   }
   ```
3. **Texto en `title`.** El `title` final incluye `📺 Quality: <quality>` donde
   `quality = s.resolution || s.quality || 'Auto'`, y si viene como `"1920x1080"` el código lo
   convierte a `"1080p"` (`src/streams.js:838-842`):
   ```js
   let quality = s.resolution || s.quality || 'Auto';
   if (String(quality).includes('x')) { const h = String(quality).split('x')[1]; quality = h + 'p'; }
   ```

**Recomendación de parseo para `PluginClient` (filtrar ≤ tope, default 1080):**
Normalizar la altura en píxeles a partir de, en orden:
- `stream.resolution`: si matchea `(\d+)x(\d+)` → usar el segundo grupo (altura);
  si matchea `(\d+)p` → esa altura; `'HD'` → tratar como desconocida (≈720, conservador);
  `'SD'` → < 720.
- fallback: regex sobre `title` buscando `(\d{3,4})p`.
Luego: descartar `height > tope`; de los restantes, como ya vienen ordenados por calidad, tomar
el primero. Si **todos** exceden el tope, elegir el de menor altura (Requisito 6.5). Si no hay
dato de resolución, tratar como aceptable (no bloquear por falta de metadato) pero de menor
prioridad.

> **Ambigüedad:** la resolución sólo se conoce **después** de que `verifyStreams` haya hecho el
> ping + (opcional) sonda de velocidad. Si `ENABLE_SPEED_PROBE=false` o el ping no trajo master
> playlist, muchos streams quedan con `resolution` nula o `'HD'`. El tope de 1080 no siempre es
> aplicable con certeza; documentarlo como best-effort.

---

## 5. Proxy de stream (¿reproducible por mpv?)

- **La mayoría de `url` son proxied por el propio plugin:** los providers construyen
  `${BASE_URL}/api/manifest?url=<m3u8>&referer=<ref>&origin=<org>` (evidencia: DaddyLive
  `src/providers/DaddyLiveProvider.js:497`, WatchFooty `:200`, EmbedSt `:128`, EmbedIndia `:99`,
  DamiTv `:287`, TimStreams `:206`, PpvSt `:131`, ReplayZone `:481`). El rewriter universal de
  `src/index.js:772-820` reescribe estas URLs relativas/absolutas al Host de la petición
  entrante, por lo que llamando desde `localhost:7000` saldrán como
  `http://<host-local>:7000/api/manifest?...`.
- **`/api/manifest`** (`src/routes/manifest.js:20`): fetch del m3u8 upstream con el `Referer`/
  `Origin` dados, valida que contenga `#EXT`, y **reescribe cada URI de segmento/variante**
  (`rewriteHlsUri`, `src/services/HlsRewriteService.js`) para que los segmentos también pasen
  por el plugin (`/api/manifest` anidado para sub-playlists, workers CF o `/api/hlschunk` para
  `.ts`/imagen). Devuelve `Content-Type: application/vnd.apple.mpegurl`. **mpv puede reproducir
  esta URL tal cual, sin cabeceras** — el plugin se encarga del Referer/Origin aguas arriba.
  Incluye re-minting transparente de tokens expirados (`rck`) y 404 en caso de muerto para que
  el cliente haga failover.
- **Streams `.m3u8` directos upstream (no proxied):** si `s.url` contiene `.m3u8` y **no**
  `/api/manifest`, el plugin marca `behaviorHints.notWebReady = true` y añade
  `behaviorHints.proxyHeaders.request = { Referer, Origin }` (`src/streams.js:921-955`). Esos
  **sí** requieren cabeceras; mpv necesitaría `--http-header-fields='Referer: ...','Origin:
  ...'`. El `PluginClient` debe leer `behaviorHints.proxyHeaders.request` y pasárselas a mpv.
- **`externalUrl` (web/YouTube):** no son HLS directos; para un kiosk headless con mpv, lo más
  robusto es **preferir streams con `url`** y, para YouTube, pasar la `externalUrl` a mpv
  (yt-dlp). Los `🌐 Web Stream` (iframe) no son reproducibles directamente por mpv.

**Resumen mpv:** usar `stream.url`. Si existe `behaviorHints.proxyHeaders.request`, pasar esos
headers a mpv; si no (caso `/api/manifest`), reproducir sin headers.

---

## 6. Arranque y build

Fuentes: `package.json`, `src/config.js`, `src/index.js`, `.env.example`,
`.kiro/steering/live-sport-plugin.md`.

- **Build:** `npm run build` →
  `npx @vercel/ncc build src/index.js -o dist --minify -e sharp` + copia de
  `*.wasm`/`*.js` de providers, subproyecto `resolver/`, `public/`, `src/data/*.json` a `dist/`
  (`package.json:scripts.build`). Escribe `dist/src/package.json` con `{"type":"module"}`.
  **El build debe completar (incluyendo WASM) antes de arrancar** (coincide con Requisito 3.5).
- **Arranque producción:** `npm start` = `node dist/index.js` (`package.json:scripts.start`).
- **Dev:** `npm run dev` = `node --watch-path=src src/index.js`.
- **Puerto:** `PORT` (default **7000**): `const PORT = parseInt(process.env.PORT,10) || 7000`
  (`src/config.js:39`). Bind host: `process.env.HOST || process.env.IP || '0.0.0.0'`
  (`src/index.js:1118`), por lo que escucha en todas las interfaces → accesible en
  `localhost:7000`.
- **Node:** `>=22.0.0` (`package.json:engines`).
- **Variables de entorno relevantes** (`.env.example`, `config.js`):
  - `PORT` (7000).
  - `ADDON_URL` — base URL pública; **si se setea, fuerza `BASE_URL` y
    `getRequestBaseUrl` lo devuelve siempre** (`src/config.js:44,78`). Para el kiosk local
    conviene **dejarlo sin setear** (auto-detección a `http://<LAN-IP>:7000`) o ponerlo a
    `http://localhost:7000` para que las URLs de stream apunten a localhost.
  - `RESIDENTIAL_PROXY`, `CF_PROXY_URL`, `WARP_PROXY_URL` (opcionales, scraping).
  - Deadlines/tuning de streams (opcionales): `STREAM_SOFT_DEADLINE_MS` (6000),
    `STREAM_HARD_DEADLINE_MS` (15000), `RESOLVE_ATTEMPTS` (2), `VERIFY_*`, `ENABLE_SPEED_PROBE`,
    `PREWARM_*`, `DONATION_URL` (`disabled` para quitar la entrada "Support Us").

> **Recomendación kiosk:** correr `node dist/index.js` bajo systemd (Requisito 3), con
> `PORT=7000` y `ADDON_URL=http://localhost:7000` (o sin `ADDON_URL`), y `DONATION_URL=disabled`
> para que el primer stream no sea la entrada de donación.

---

## 7. Config en la URL (`/:config/...`)

### 7.1 ¿Obligatoria?
**No.** El router del SDK sirve las rutas **sin** prefijo de config (`/manifest.json`,
`/catalog/tv/<id>.json`, `/stream/tv/<id>.json`) y el handler recibe `config = {}`
(`src/index.js:48-50`; `handleCatalog`/`handleStream` usan `config || {}`). El middleware de
`src/index.js:1049-1062` sólo reescribe la ruta **si ya hay** un segmento de config o params
compactos; si no hay, no toca nada y el SDK responde igual.

### 7.2 Formatos aceptados del segmento config
`decodeConfigSegment` (`src/index.js:878-896`) acepta **dos** formas:
```js
if (configStr.startsWith('%7B') || configStr.startsWith('{')) {
  parsed = JSON.parse(decodeURIComponent(configStr));   // JSON URL-encoded
} else {
  let base64 = configStr.replace(/-/g, '+').replace(/_/g, '/'); // base64url
  while (base64.length % 4) base64 += '=';
  parsed = JSON.parse(Buffer.from(base64, 'base64').toString('utf-8'));
}
```
Es decir: **JSON plano base64url** o **JSON URL-encoded**. (Aunque el stack usa `lz-string` en
otras partes, este decodificador de la ruta de catalog/meta/stream espera JSON base64url, no
lz-string. El middleware interno además reescribe cualquier config a `encodeURIComponent(JSON)`
antes de pasarla al SDK, `src/index.js:1058`.)

### 7.3 Qué contiene la config
Claves del objeto config (del `manifest.config`, `src/manifest.js`, y uso en handlers):
- `teams` (favoritos, coma-separados).
- `sports` (habilitados, coma-separados; default `'all'`). Filtra el catálogo:
  `src/catalog.js:1344-1348` deja pasar sólo `m.category ∈ sports` (más `networks`).
- `timezone` (default `'UTC'`) — formateo de horarios en meta.
- `languages` (orden de preferencia de idioma de los streams; inglés siempre primero).
- `replayFilter` (`all`/`mainstream`/`disabled`).
- También aceptados vía query/compactos: `webStreams` (`disabled` oculta web streams,
  `src/streams.js:995-1006`), y params cortos `rf`/`lg`/`ws`.

### 7.4 Config mínima para "solo NFL"
No hace falta config para pedir NFL — se puede llamar al catálogo
`nuvio_sports_american_football` directamente. Si aun así se quisiera restringir el manifest a
americanos, la config mínima sería:
```json
{ "sports": "american_football" }
```
y la URL sería `/<base64url(JSON)>/catalog/tv/nuvio_sports_american_football.json`.
**Para el kiosk se recomienda NO usar config** y filtrar en Python (más simple, y evita
depender del formato de codificación).

---

## 8. Secuencias exactas de llamadas (receta para `PluginClient`)

Asumir `BASE = http://localhost:7000`.

### (a) Listar NFL de hoy
1. `GET {BASE}/catalog/tv/nuvio_sports_american_football.json`
   (o, para sólo en vivo: `GET {BASE}/catalog/tv/nuvio_sports_live/genre=American%20Football.json`).
2. Respuesta: `{ "metas": [ { id, name, type, poster, released?, ... }, ... ] }`.
3. En Python, por cada `meta`:
   - `id` → tal cual (`nuvio_sport_<matchId>`).
   - `title` ← `meta.name`.
   - `sport` ← `american_football` (del catálogo pedido).
   - `league` ← derivar de `name`/`description` (buscar "NFL"); default `None`.
   - `start_time` ← `meta.released` (ISO 8601) si existe; si no, `None` (24/7).
   - `status` ← `live`/`upcoming` según el catálogo consultado, o recomputar con `start_time`
     y la ventana de 4 h de americanos.
   - Filtrar "de hoy": `start_time.date() == today` (en la zona del kiosk).
   - **Opcional NFL estricto:** descartar los que claramente no sean NFL (college, etc.) por
     título. (El plugin no separa NFL de NCAA en el catálogo.)

### (b) Resolver y rankear streams de un partido
1. `GET {BASE}/stream/tv/{meta.id}.json` (ej. `/stream/tv/nuvio_sport_streamedpk_123_team-a-vs-team-b.json`).
2. Respuesta: `{ "streams": [ {name,title,url?,externalUrl?,resolution?,bitrate?,language?,behaviorHints?}, ... ] }`
   — **ya ordenada mejor→peor**.
3. En Python: descartar la entrada "🧿 Support Us" y cualquier stream sin `url` ni vídeo
   reproducible. Mantener la lista completa (orden preservado) para "siguiente fuente"
   (Requisito 7). Mapear cada stream a `source` (parsear el provider del `title`, p.ej.
   "DaddyLive", "Streamed.pk", "WatchFooty"...) y `stream_url` ← `url`.
4. **Reintento:** si la primera llamada trae pocas fuentes, repetir `GET /stream/...` tras ~2-6 s
   (las fuentes lentas se cachean entre llamadas; §3.2).

### (c) Elegir el mejor ≤ tope de resolución (default 1080)
1. Para cada stream reproducible, calcular `height` (ver §4: de `resolution` `"WxH"`/`"Np"`,
   o `title` `(\d{3,4})p`; `'HD'`/desconocida → tratar como aceptable y de baja prioridad).
2. Candidatos = streams con `height <= tope` (o sin dato de altura).
3. Como la lista ya está ordenada por calidad+velocidad, elegir el **primer** candidato.
4. Si no hay candidatos (todos > tope): elegir el de **menor** altura (Requisito 6.5).
5. Reproducir con mpv:
   - URL = `stream.url`.
   - Si `stream.behaviorHints.proxyHeaders.request` existe → pasar `Referer`/`Origin` a mpv
     (`--http-header-fields`). Si no (caso `/api/manifest`) → sin headers.
6. Timeout de 30 s (Requisito 6.6): si `/stream` no responde o no hay stream reproducible en
   30 s, abortar, volver a la pantalla y marcar el partido "no disponible". Nota: el plugin
   tiene su propio hard deadline de ~15 s, por lo que 30 s del lado Python lo cubre.

---

## 9. Mapeo al modelo `PluginClient` (resumen)

| Campo modelo | Origen en el plugin |
|---|---|
| `id` | `meta.id` del catálogo = `nuvio_sport_<matchId>`. Reusar íntegro en `/stream`. |
| `title` | `meta.name`. |
| `league` | **Derivado** del título/`description` (no es campo del `meta` Stremio). `None` por defecto. |
| `sport` | Categoría del catálogo consultado (`american_football`), o `match.category` si se usa `/api/matches`. |
| `start_time` | `meta.released` (ISO) → epoch. Internamente `match.date` es epoch ms string. |
| `stream_url` | `stream.url` de `/stream/...` (normalmente proxy `/api/manifest`). |
| `source` | Parsear del `stream.title` (nombre del provider) o `niceNames` (`src/streams.js:845-856`). |
| `status` | `live`/`upcoming`/`unavailable` derivado del catálogo + reloj de kickoff + resultado de `/stream`. |

---

## 10. Ambigüedades / cosas no confirmables desde el código

1. **"NFL" no está separado de otros americanos** en el catálogo; la única separación es la
   categoría `american_football`. Distinguir NFL de NCAA requiere heurística de título. (El
   usuario ya decidió "sólo NFL, eliminar NCAA" en los requisitos; el filtro vive en Python.)
2. **`league` casi siempre vacío** en datos crudos de varios providers (Streamed.pk no lo da).
3. **Resolución incierta** hasta que `verifyStreams`/sonda corren; con `ENABLE_SPEED_PROBE=false`
   o sin master playlist, `resolution` puede ser nula/`'HD'`. El tope ≤1080 es best-effort.
4. **No ejecuté el servidor** (investigación de solo lectura): la forma exacta del JSON de
   respuesta se infiere del código (handlers + SDK), no de una captura en vivo. Las claves y la
   secuencia están confirmadas en código; valores concretos (ej. un `meta` real de NFL) no se
   capturaron.
5. **`/api/matches`** da el modelo crudo con `league`/`team1`/`team2` estructurados, pero es
   endpoint de debug restringido a loopback directo y sin contrato estable — útil como atajo
   local, no recomendado como superficie primaria.
6. **Subproyecto `resolver/`** corre en otro puerto (`RESOLVER_PORT`, `src/resolverManager.js`)
   y lo usan algunos providers internamente; **no** es necesario para el `PluginClient` (mpv
   reproduce la `url` que devuelve `/stream`).

---

## 11. Recomendaciones (sin implementar)

- `PluginClient` debe usar **`/catalog` + `/stream`** (contrato Stremio), **sin** segmento de
  config, y filtrar NFL/"hoy"/resolución en Python.
- Setear `ADDON_URL=http://localhost:7000` (o dejarlo sin setear) y `DONATION_URL=disabled` en
  el `.env`/systemd del plugin para URLs de stream limpias apuntando a localhost y sin la
  entrada "Support Us" como primer stream.
- Implementar **reintento** de `/stream` (una repetición tras ~3-5 s) para capturar fuentes que
  llegan tras el soft deadline del plugin.
- Al reproducir con mpv, **leer `behaviorHints.proxyHeaders.request`** y pasar Referer/Origin
  sólo cuando existan (streams `.m3u8` directos); los `/api/manifest` van sin headers.
- Preferir streams con `url` directo (`⚡ Direct Stream` / `▶️ YouTube`); descartar
  `🌐 Web Stream` (iframe no reproducible por mpv) y la entrada de donación.
