"""Resolución recursiva de enlaces hasta encontrar URLs de stream "limpias".

Dos estrategias:
- static:  descarga HTML con requests y lo parsea con BeautifulSoup.
- dynamic: renderiza la página con Playwright (Chromium) para exponer streams
           inyectados por JavaScript. Playwright se importa de forma perezosa
           para que el modo static no dependa de él.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .sources import Source
from .dns_fallback import make_session

log = logging.getLogger(__name__)

_DEFAULT_UA = os.environ.get(
    "SCRAPPER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux aarch64) Scrapper/1.0",
)


@dataclass
class StreamCandidate:
    """Una URL de stream resuelta, con el título asociado si se conoce."""

    url: str
    title: str | None = None


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc == urlparse(b).netloc


def _title_from_url(url: str) -> str | None:
    """Deriva un título legible del slug de la URL de un partido.

    Ej.: '/nfl/dallas-cowboys-vs-new-york-giants-1/' -> 'Dallas Cowboys Vs New York Giants'.
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    slug = path.split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)          # quita sufijos numéricos: '-1'
    words = slug.replace("-", " ").strip()
    if not words:
        return None
    return words.title()


def _extract_stream_urls(html: str, base_url: str, stream_re: re.Pattern) -> list[str]:
    """Extrae URLs completas de stream del HTML.

    Busca en atributos comunes (src/href/data-src) y en URLs sueltas del texto,
    resolviendo relativas contra base_url y filtrando por stream_re.
    """
    urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    # 1) Atributos que suelen contener la URL del stream.
    for tag in soup.find_all(True):
        for attr in ("src", "href", "data-src", "data-file"):
            val = tag.get(attr)
            if val and stream_re.search(val):
                urls.append(urljoin(base_url, val))

    # 2) URLs completas sueltas en el texto (p. ej. dentro de <script>).
    #    Captura una URL http(s) que contenga el patrón de stream.
    url_re = re.compile(r"https?://[^\s\"'<>()]+", re.IGNORECASE)
    for candidate in url_re.findall(html):
        if stream_re.search(candidate):
            urls.append(candidate)

    # Deduplicar conservando el orden.
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def resolve_source(source: Source, timeout: int, max_depth: int) -> list[StreamCandidate]:
    """Punto de entrada: devuelve los streams limpios encontrados en una fuente."""
    if source.strategy == "dynamic":
        return _resolve_dynamic(source, timeout)
    if source.strategy == "hybrid":
        return _resolve_hybrid(source, timeout)
    return _resolve_static(source, timeout, max_depth)


# --------------------------------------------------------------------------- #
# Estrategia estática (requests + BeautifulSoup)
# --------------------------------------------------------------------------- #
def _resolve_static(source: Source, timeout: int, max_depth: int) -> list[StreamCandidate]:
    stream_re = re.compile(source.stream_pattern)
    follow_re = re.compile(source.follow_pattern)
    session = make_session()
    session.headers.update({"User-Agent": _DEFAULT_UA})

    found: list[StreamCandidate] = []
    seen: set[str] = set()

    def crawl(url: str, depth: int, title: str | None) -> None:
        if depth > max_depth or url in seen:
            return
        seen.add(url)

        # ¿La propia URL ya es un stream limpio?
        if stream_re.search(url):
            found.append(StreamCandidate(url=url, title=title))
            return

        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("[%s] fallo al pedir %s: %s", source.name, url, exc)
            return

        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return

        soup = BeautifulSoup(resp.text, "html.parser")

        # 1) Streams incrustados directamente en el HTML (src, href, texto).
        #    El título se hereda del enlace que nos trajo aquí, o del <title>/<h2>.
        page_title = title
        if page_title is None:
            heading = soup.find(["h1", "h2", "title"])
            if heading:
                page_title = heading.get_text(strip=True)
        for abs_url in _extract_stream_urls(resp.text, url, stream_re):
            if abs_url not in seen:
                seen.add(abs_url)
                found.append(StreamCandidate(url=abs_url, title=page_title))

        # 2) Seguir enlaces que casen con follow_pattern (misma profundidad + 1).
        for a in soup.select(source.link_selector):
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(url, href)
            if not abs_url.startswith(("http://", "https://")):
                continue
            link_title = a.get_text(strip=True) or title
            if stream_re.search(abs_url):
                if abs_url not in seen:
                    seen.add(abs_url)
                    found.append(StreamCandidate(url=abs_url, title=link_title))
            elif follow_re.search(abs_url) and _same_host(source.url, abs_url):
                crawl(abs_url, depth + 1, link_title)

    crawl(source.url, 0, None)
    log.info("[%s] streams encontrados (static): %d", source.name, len(found))
    return found


# --------------------------------------------------------------------------- #
# Estrategia dinámica (Playwright)
# --------------------------------------------------------------------------- #
def _chromium_host_rules(hosts: set[str]) -> list[str]:
    """Construye args de Chromium para forzar la IP (vía DoH) de hosts concretos.

    Chromium usa su propio resolver (no el fallback DoH de requests), así que le
    pasamos --host-resolver-rules con 'MAP host ip' para los dominios que AdGuard
    filtra intermitentemente.
    """
    from .dns_fallback import resolve_doh

    rules: list[str] = []
    for h in hosts:
        ip = resolve_doh(h)
        if ip:
            rules.append(f"MAP {h} {ip}")
    if not rules:
        return []
    return [f"--host-resolver-rules={','.join(rules)}"]


def _import_playwright(source_name: str):
    """Importa Playwright de forma perezosa; devuelve None si no está instalado."""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        log.error(
            "[%s] strategy dynamic/hybrid requiere Playwright. "
            "Instálalo con: pip install playwright && python -m playwright install chromium",
            source_name,
        )
        return None


def _capture_streams_on_page(
    page, page_url: str, stream_re: re.Pattern, timeout: int, wait_ms: int,
    wait_for: str | None,
) -> list[str]:
    """Abre page_url, escucha peticiones de red y devuelve las URLs que casan con stream_re.

    Captura también las peticiones hechas por iframes anidados (Playwright las ve
    todas). Espera wait_for (si se indica) y/o wait_ms para dar tiempo al reproductor.
    """
    hits: list[str] = []
    seen: set[str] = set()

    def on_request(request) -> None:
        if stream_re.search(request.url) and request.url not in seen:
            seen.add(request.url)
            hits.append(request.url)

    page.on("request", on_request)
    try:
        page.goto(page_url, timeout=timeout * 1000, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        log.warning("goto %s falló: %s", page_url, exc)
        return hits

    if wait_for:
        try:
            page.wait_for_selector(wait_for, timeout=timeout * 1000)
        except Exception as exc:  # noqa: BLE001
            log.debug("wait_for %r no apareció en %s: %s", wait_for, page_url, exc)

    if wait_ms:
        page.wait_for_timeout(wait_ms)

    # También mirar el HTML renderizado por si el stream está incrustado.
    try:
        for url in _extract_stream_urls(page.content(), page_url, stream_re):
            if url not in seen:
                seen.add(url)
                hits.append(url)
    except Exception:  # noqa: BLE001
        pass

    return hits


def _resolve_dynamic(source: Source, timeout: int) -> list[StreamCandidate]:
    sync_playwright = _import_playwright(source.name)
    if sync_playwright is None:
        return []

    stream_re = re.compile(source.stream_pattern)
    found: list[StreamCandidate] = []

    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            page = browser.new_context(user_agent=_DEFAULT_UA).new_page()
            urls = _capture_streams_on_page(
                page, source.url, stream_re, timeout, source.wait_ms, source.wait_for
            )
            for u in urls:
                found.append(StreamCandidate(url=u))
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] fallo en Playwright: %s", source.name, exc)

    log.info("[%s] streams encontrados (dynamic): %d", source.name, len(found))
    return found


# --------------------------------------------------------------------------- #
# Estrategia híbrida: listado estático de partidos + resolución dinámica del stream
# --------------------------------------------------------------------------- #
def _resolve_hybrid(source: Source, timeout: int) -> list[StreamCandidate]:
    """1) Descarga la página de listado y extrae enlaces a partidos (match_pattern).
    2) Abre cada partido con Playwright y captura su .m3u8.
    """
    sync_playwright = _import_playwright(source.name)
    if sync_playwright is None:
        return []

    match_re = re.compile(source.match_pattern or source.follow_pattern)
    stream_re = re.compile(source.stream_pattern)

    # Paso 1: listado (estático) -> (url_partido, título)
    session = make_session()
    session.headers.update({"User-Agent": _DEFAULT_UA})
    try:
        resp = session.get(source.url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("[%s] no se pudo cargar el listado %s: %s", source.name, source.url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    matches: list[tuple[str, str | None]] = []
    seen_urls: set[str] = set()
    for a in soup.select(source.link_selector):
        href = a.get("href")
        if not href:
            continue
        abs_url = urljoin(source.url, href)
        if not abs_url.startswith(("http://", "https://")):
            continue
        if match_re.search(abs_url) and abs_url not in seen_urls:
            seen_urls.add(abs_url)
            title = a.get_text(strip=True) or _title_from_url(abs_url)
            matches.append((abs_url, title))

    matches = matches[: source.max_matches]
    log.info("[%s] partidos encontrados en el listado: %d", source.name, len(matches))

    # Paso 2: resolver el stream de cada partido con Playwright (un solo navegador).
    found: list[StreamCandidate] = []
    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            ctx = browser.new_context(user_agent=_DEFAULT_UA)
            for match_url, title in matches:
                page = ctx.new_page()
                try:
                    urls = _capture_streams_on_page(
                        page, match_url, stream_re, timeout, source.wait_ms, source.wait_for
                    )
                    for u in urls:
                        found.append(StreamCandidate(url=u, title=title))
                        break  # un stream por partido basta
                finally:
                    page.close()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] fallo en Playwright (hybrid): %s", source.name, exc)

    log.info("[%s] streams encontrados (hybrid): %d", source.name, len(found))
    return found
