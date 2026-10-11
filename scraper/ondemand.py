"""Resolución on-demand del stream de UN partido concreto en StreamEast.

Se invoca desde el tablero cuando el usuario pulsa una tarjeta: en ese momento
se busca el partido en el listado de StreamEast (por coincidencia de equipos en
el slug de la URL) y se resuelve su .m3u8 con Playwright.

Diseñado para bloquear pocos segundos y devolver un resultado claro para la UI:
    resolve_match(["den", "denver", "broncos"], ["kc", "kansas", "chiefs"])
    -> StreamResult(url="https://.../index.m3u8", referrer="https://v2.streameast.ga/")
    o None si no se encuentra / no se resuelve todavía.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .dns_fallback import make_session
from .resolvers import (
    _DEFAULT_UA,
    _capture_streams_on_page,
    _chromium_host_rules,
    _extract_stream_urls,
    _import_playwright,
)

log = logging.getLogger(__name__)

# Listado NFL de StreamEast y patrón de página de partido.
STREAMEAST_LISTING = "https://v2.streameast.ga/nfl-streams/"
_MATCH_PATTERN = re.compile(r"/nfl/[a-z0-9-]+-vs-[a-z0-9-]+")
_STREAM_PATTERN = re.compile(r"\.m3u8")
# Espera para que el reproductor del iframe pida el manifiesto (ms).
_WAIT_MS = 30000
# Espera al cargar el listado con el navegador: deja que el reto de Cloudflare se
# resuelva y que las tarjetas de partido se rendericen antes de leer el HTML (ms).
_LISTING_WAIT_MS = 12000


@dataclass
class StreamResult:
    """Resultado de la resolución on-demand."""

    url: str
    referrer: str


def _referrer_for(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/" if parsed.netloc else ""


def _fetch_listing_html(listing_url: str, timeout: int) -> str | None:
    """Descarga el HTML del listado con el navegador (Playwright).

    El listado está detrás del reto "managed challenge" de Cloudflare, que
    `requests` no puede resolver (devuelve 403 con la página de reto). Un
    Chromium real sí lo pasa, igual que en la captura del stream. Reutilizamos el
    mismo patrón de arranque (reglas DoH + _DEFAULT_UA) que `resolve_match`.

    Devuelve el HTML renderizado o None si Playwright no está disponible o la
    página no se pudo cargar (el llamador hará el fallback con requests).
    """
    sync_playwright = _import_playwright("ondemand")
    if sync_playwright is None:
        return None

    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            page = browser.new_context(user_agent=_DEFAULT_UA).new_page()
            try:
                page.goto(listing_url, timeout=timeout * 1000,
                          wait_until="domcontentloaded")
                # Dar tiempo a que Cloudflare libere y rendericen las tarjetas.
                page.wait_for_timeout(_LISTING_WAIT_MS)
                return page.content()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("on-demand: no se pudo cargar el listado con navegador %s: %s",
                    listing_url, exc)
        return None


def _pick_match_url(html: str, listing_url: str, team_a_terms: list[str],
                    team_b_terms: list[str]) -> str | None:
    """Dado el HTML del listado, devuelve la URL de partido que menciona ambos equipos.

    Cada equipo aporta varios términos candidatos (abreviatura, ciudad, apodo);
    basta con que el slug contenga AL MENOS uno de cada equipo.
    """
    a_terms = [t.lower() for t in team_a_terms if t]
    b_terms = [t.lower() for t in team_b_terms if t]

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a"):
        href = a.get("href")
        if not href:
            continue
        abs_url = urljoin(listing_url, href)
        if not abs_url.startswith(("http://", "https://")):
            continue
        if not _MATCH_PATTERN.search(abs_url):
            continue
        slug = urlparse(abs_url).path.lower()
        if any(t in slug for t in a_terms) and any(t in slug for t in b_terms):
            log.info("on-demand: partido encontrado en el listado -> %s", abs_url)
            return abs_url

    log.info("on-demand: ningún enlace del listado casa con %s / %s", a_terms, b_terms)
    return None


def _find_match_url(listing_url: str, team_a_terms: list[str],
                    team_b_terms: list[str], timeout: int) -> str | None:
    """Obtiene el listado y devuelve la URL de partido que menciona ambos equipos.

    El listado se descarga con el navegador porque está tras Cloudflare; si eso
    falla (Playwright ausente o render fallido) se cae al fetch con requests, que
    sigue sirviendo en redes sin el reto.
    """
    html = _fetch_listing_html(listing_url, timeout)

    if html is None:
        # Fallback: fetch estático con requests (suele dar 403 bajo Cloudflare,
        # pero en redes sin el reto funciona y no cuesta nada intentarlo).
        session = make_session()
        session.headers.update({"User-Agent": _DEFAULT_UA})
        try:
            resp = session.get(listing_url, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
        except requests.RequestException as exc:
            log.warning("on-demand: no se pudo cargar el listado %s: %s", listing_url, exc)
            return None

    return _pick_match_url(html, listing_url, team_a_terms, team_b_terms)


def resolve_match(team_a_terms: list[str], team_b_terms: list[str],
                  timeout: int = 30) -> StreamResult | None:
    """Resuelve el .m3u8 del partido descrito por los términos de ambos equipos.

    Devuelve None si no se encuentra el partido en el listado o si el stream aún
    no está disponible (p. ej. el reproductor no ha pedido el manifiesto).
    """
    match_url = _find_match_url(STREAMEAST_LISTING, team_a_terms, team_b_terms, timeout)
    if not match_url:
        return None

    sync_playwright = _import_playwright("ondemand")
    if sync_playwright is None:
        return None

    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            page = browser.new_context(user_agent=_DEFAULT_UA).new_page()
            try:
                urls = _capture_streams_on_page(
                    page, match_url, _STREAM_PATTERN, timeout, _WAIT_MS, wait_for=None
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("on-demand: fallo en Playwright: %s", exc)
        return None

    if not urls:
        log.info("on-demand: sin .m3u8 todavía para %s", match_url)
        return None

    return StreamResult(url=urls[0], referrer=_referrer_for(match_url))
