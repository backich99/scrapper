---
inclusion: fileMatch
fileMatchPattern: 'live-sport-plugin/**'
---

# Nuvio Live Sports Plugin — Contexto del proyecto

Nota de ruta: el proyecto vive anidado en `live-sport-plugin/live-sport-plugin/`
(doble carpeta) dentro de este workspace. Todas las rutas de abajo son
relativas a esa raíz salvo que se indique lo contrario.

## Qué es

Addon de deportes en vivo para **Nuvio** / **Stremio** (protocolo Stremio v1).
Es un agregador multi-fuente de alto rendimiento: scrapea y unifica fixtures en
vivo, repeticiones de partidos y canales 24/7 de 13+ proveedores, los
transforma en catálogos/metadata/streams de Stremio, y proxea los streams
HLS e imágenes para esquivar CORS y restricciones de ISP. Versión actual: 3.1.3.

**Uso y legalidad:** proyecto experimental, personal/educativo, MIT. No aloja
media; solo parsea manifests públicos. No desplegar en PaaS gratuitas/compartidas
(Render, Vercel, Railway) — sus scanners de AUP suspenden la cuenta. Self-host
en VPS no gestionado, PC propio o Raspberry Pi vía Docker o Cloudflare Tunnel.

## Stack

- **Runtime:** Node.js >= 22, Express 4. Módulos ESM (`"type":"module"`).
- **Addon:** stremio-addon-sdk (protocolo v1).
- **IoC / arquitectura:** Awilix (DI container en `src/container.js`), DDD.
- **HTTP/TLS:** Impit (fingerprint de navegador), Undici, got-scraping.
- **Scraping/DOM:** Cheerio, Happy DOM, jsdom.
- **Resiliencia:** Opossum (circuit breakers), coalescing de requests, negative cache.
- **Streaming:** m3u8-parser, reescritor dinámico de segmentos M3U8.
- **Descifrado:** WebAssembly nativo (`stream-lock.wasm`, `gasm.wasm`, `gasm_india.wasm`).
- **Scheduling:** node-cron (sync de partidos y pre-warming).
- **Encoding:** lz-string (compresión base64url de config en la URL).
- **Bundler de producción:** @vercel/ncc (CJS único con copia de assets WASM).
- **Testing:** Jest (52 suites / 656+ tests).

## Layout del código

- `src/index.js` — Express, `trust proxy`, middleware "universal response
  rewriter" que reescribe URLs de assets/streams al host de la request, rutas
  `/img` e `/img/placeholder`.
- `src/config.js` — detección dinámica de IPv4 no interna y
  `getRequestBaseUrl(req)` (resuelve `Host`, `X-Forwarded-Host`,
  `X-Forwarded-Proto`, `cf-visitor`, `x-forwarded-ssl`). **Cero IPs hardcodeadas.**
- `src/container.js` — contenedor Awilix (registro de servicios/providers).
- `src/manifest.js`, `src/catalog.js`, `src/collections.js`, `src/streams.js`,
  `src/api.js` — endpoints del protocolo Stremio.
- `src/manifestProxy.js`, `src/remint.js`, `src/resolverManager.js`,
  `src/impitClient.js` — proxy HLS, re-minting de tokens, resolver de streams.
- `src/domain/` — entidades DDD: `MatchEntity.js`, `StreamEntity.js`.
- `src/services/` — capa de servicios (ver abajo).
- `src/providers/` — scrapers por proveedor + runtimes WASM.
- `src/providers/yaml/` + `YamlProviderBuilder.js` — motor declarativo de
  providers vía YAML (sin boilerplate).
- `src/routes/` — `embed.js`, `health.js`, `manifest.js`, `streamProxy.js`, `watch.js`.
- `src/data/` — JSON de datos (copiado al build).
- `resolver/` — subproyecto resolver de streams (su propio package.json).
- `scripts/` — e2e, generación de providers, check-sources, prewarm/sync cron, benchmarks.
- `tests/` — suites Jest (load tests excluidos de Jest).
- `cloudflare-worker.js` / `cloudflare-worker/` — proxy edge opcional.

### Servicios clave (`src/services/`)
`CacheService`, `StreamResolveCache`, `MatchAggregator` (dedup preservando
logos/thumbnails/background), `ImageService` (proxy `/img`, LRU, fallback SVG,
100% 200 OK), `CircuitBreakerService`, `ProviderHealthService`,
`StreamScoringService` + `MainstreamRankingService` (scoring/ranking de streams),
`HlsRewriteService`, `M3U8ParserService`, `OutboundUrlGuard` (guardia SSRF de
egreso), `TeamLogoService`/`TeamNameExtractor`, `LanguagePriorityService`,
`ReplayFilterService`, `IframeDomainRegistry`, `EmbedExtractorChain`/`EmbedBase`.

### Providers (`src/providers/`)
DaddyLive, StreamedPk, StreamFree, WatchFooty, SportyHunter, TimStreams,
StreamSports99, Streamic, CdnLive, DamiTv, EmbedIndia, PpvSt, ReplayZone,
LiveTV + `BaseProvider`. Runtimes WASM (`gasm*`, `lock`/`stream-lock`) con
scripts `run_gasm_*` para descifrado nativo de tokens.

## Scripts npm

- `npm run dev` — dev con `--watch-path=src`.
- `npm run build` — bundle con ncc a `dist/` + copia WASM/public/data.
- `npm start` — corre `dist/index.js` (producción).
- `npm test` — Jest.
- `npm run test:e2e-client` (= `test:sanity`) — cliente Stremio simulado E2E.
- `npm run test:247` — valida canales 24/7.
- `npm run check-sources` — health check de scrapers upstream.
- `npm run generate:provider` — scaffold de un provider nuevo.
- `npm run prewarm:cron` / `sync:matches` — tareas cron.
- `npm run bench*` — benchmarks de rendimiento/heap.

## Variables de entorno (`.env`)

- `PORT` (7000), `ADDON_URL` (auto si vacío), `RESIDENTIAL_PROXY`,
  `CF_PROXY_URL`, `WARP_PROXY_URL`, `PREWARM_CRON_SCHEDULE` (`*/3 * * * *`),
  `PREWARM_MAX_MATCHES` (12).

## Convenciones del proyecto

- **Host routing dinámico:** nunca hardcodear IPs/hosts; todo pasa por
  `getRequestBaseUrl(req)` y el rewriter universal.
- **Resiliencia por defecto:** cada scraper aislado tras un circuit breaker
  Opossum; upstreams muertos van a negative cache (~15s) para failover sin freeze.
- **Imágenes 100% 200 OK:** servir vía `/img` con fallback SVG; preservar
  `thumbnail_url`, `team1.logo`, `team2.logo`, `background` al deduplicar.
- **Commits (ver `GEMINI.md`):** Conventional Commits (`feat/fix/perf/refactor`),
  con sección obligatoria **Before & After** + impacto medido + verificación.
  Nada de mensajes de una línea vagos.
- Antes de tocar `public/posters/`, los mounts `/posters` o las rutas `/img*`,
  leer `docs/BANDWIDTH_AND_CACHING.md`.

## Documentación de referencia

- `PROJECT.md` — arquitectura, inventario de features, milestones, write boundaries.
- `README.md` — hosting, features, stack, config, dev/test.
- `GEMINI.md` — estándares de commit/push.
- `docs/DEPLOYMENT_GUIDE.md` — topología VPS (Cloudflare → Caddy → PM2), DNS/TLS.
- `docs/DADDYLIVE_IFRAME_DOMAINS.md` — resolución de streams DaddyLive y decoders.
- `docs/BANDWIDTH_AND_CACHING.md` — egreso, caching de Cloudflare, fixes de artwork.
- `docs/reviews/` — notas de revisión de caché/claims/expiry.
