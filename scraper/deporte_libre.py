"""Fuente de CANALES de Deporte-Libre (deporte-libre7.live).

A diferencia de StreamEast (partidos concretos), Deporte-Libre publica una lista
de CANALES de TV deportiva (ESPN, Fox Sports, DAZN…), cada uno con el país entre
paréntesis en el nombre: "ESPN (MX)", "Fox Sports (AR)". Este módulo:

  - list_channels(): descarga la home con Playwright y devuelve los canales
    (nombre limpio, país, url), cacheado en disco un rato.
  - resolve_channel(url): abre la página del canal y captura su .m3u8 siguiendo
    la cadena de iframes anidados (embed → mpd → zonatv → player → cdn).

El emparejamiento "partido → canal" NO existe aquí: es un mando de canales.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .resolvers import (
    _DEFAULT_UA,
    _chromium_host_rules,
    _import_playwright,
)

log = logging.getLogger(__name__)

HOME_URL = "https://deporte-libre7.live/"
# Los canales se listan en la subpágina /stream/ (antes estaban en la home).
CHANNELS_URL = "https://deporte-libre7.live/stream/"
_CHANNEL_HREF = re.compile(r"/total/stream-\d+\.php", re.IGNORECASE)
_STREAM_PATTERN = re.compile(r"\.m3u8")
# País entre paréntesis al final del nombre: "ESPN 2 (MX)" (formato antiguo).
_COUNTRY_RE = re.compile(r"\(([A-Za-z]{2,4})\)\s*$")

# El país ahora viene en la bandera de la tarjeta: assets/flags/<code>.svg.
# Mapea el código ISO del archivo al código que usa la UI (y el cache previo).
_FLAG_COUNTRY = {
    "mx": "MX", "ar": "AR", "co": "CO", "cl": "CH", "ec": "ECU",
    "pe": "PE", "uy": "UY", "br": "BR", "es": "ES", "us": "USA",
    "gb": "UK", "fr": "FR", "de": "DE", "it": "IT", "pt": "PT",
    "ca": "CA", "nl": "NL", "nz": "NZ",
    # Banderas regionales/sin país concreto -> sin código (como el formato previo).
    "sudamerica": "", "": "",
}


def _country_from_flag(flag_src: str) -> str:
    """De 'assets/flags/mx.svg' -> 'MX'. Desconocido/regional -> ''."""
    if not flag_src:
        return ""
    code = flag_src.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip().lower()
    return _FLAG_COUNTRY.get(code, code.upper() if len(code) <= 3 else "")
# Espera para que el reproductor del canal pida el manifiesto (ms).
_WAIT_MS = 15000

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = PROJECT_ROOT / "data" / "deporte_libre_channels.json"
_CACHE_TTL = 6 * 3600  # 6 h: la lista de canales cambia poco.


@dataclass
class Channel:
    """Un canal de Deporte-Libre."""

    name: str          # nombre sin el sufijo de país, p. ej. "ESPN 2"
    country: str       # código de país en mayúsculas, p. ej. "MX" ("" si no hay)
    url: str           # URL absoluta de la página del canal

    @property
    def display(self) -> str:
        return self.name


@dataclass
class ChannelStream:
    """Stream resuelto de un canal: URL del .m3u8 y el referrer con el que se pidió."""

    url: str
    referrer: str


def _split_country(raw: str) -> tuple[str, str]:
    """De 'ESPN 2 (MX)' -> ('ESPN 2', 'MX'). Sin paréntesis -> (raw, '')."""
    raw = re.sub(r"\s+", " ", raw).strip()
    m = _COUNTRY_RE.search(raw)
    if not m:
        return raw, ""
    country = m.group(1).upper()
    name = raw[: m.start()].strip()
    return name or raw, country


def _load_cache() -> list[Channel] | None:
    if not _CACHE_PATH.exists():
        return None
    try:
        if (time.time() - _CACHE_PATH.stat().st_mtime) > _CACHE_TTL:
            return None
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return [Channel(**c) for c in data.get("channels", [])]
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        log.debug("cache de canales no usable: %s", exc)
        return None


def _save_cache(channels: list[Channel]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"channels": [c.__dict__ for c in channels]}
        _CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except OSError as exc:
        log.debug("no se pudo guardar cache de canales: %s", exc)


def list_channels(force: bool = False, timeout: int = 45) -> list[Channel]:
    """Devuelve todos los canales de Deporte-Libre (cacheado en disco)."""
    if not force:
        cached = _load_cache()
        if cached:
            return cached

    sync_playwright = _import_playwright("deporte-libre")
    if sync_playwright is None:
        return _load_cache() or []

    channels: list[Channel] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            page = browser.new_context(user_agent=_DEFAULT_UA).new_page()
            # Los canales viven en /stream/ (cada uno en un div.tarjeta-canal con
            # nombre y bandera de país); la home ya no los lista.
            page.goto(CHANNELS_URL, timeout=timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            raw = page.eval_on_selector_all(
                "div.tarjeta-canal",
                "els => els.map(e => {"
                " const a = e.querySelector('a[href]');"
                " const nameEl = e.querySelector('.nombre-canal');"
                " const flag = e.querySelector('img.channel-country-flag, img');"
                " return {"
                "  href: a ? a.getAttribute('href') : '',"
                "  name: (nameEl ? nameEl.innerText : e.innerText || '')"
                "        .replace(/▶.*$/,'').replace(/\\s+/g,' ').trim(),"
                "  flag: flag ? (flag.getAttribute('src') || '') : ''"
                " };"
                "})",
            )
            browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Deporte-Libre: fallo al listar canales: %s", exc)
        return _load_cache() or []

    for item in raw:
        href = item.get("href") or ""
        name = (item.get("name") or "").strip()
        if not href or not _CHANNEL_HREF.search(href) or not name:
            continue
        url = urljoin(CHANNELS_URL, href)
        key = f"{url}|{name}"
        if key in seen:
            continue
        seen.add(key)
        country = _country_from_flag(item.get("flag") or "")
        channels.append(Channel(name=name, country=country, url=url))

    if channels:
        _save_cache(channels)
    log.info("Deporte-Libre: %d canales listados", len(channels))
    return channels


def channels_by_country(country: str, force: bool = False) -> list[Channel]:
    """Canales de un país concreto (código tipo 'MX'). Vacío si no hay."""
    country = country.upper()
    return [c for c in list_channels(force=force) if c.country == country]


def available_countries(force: bool = False) -> list[str]:
    """Lista ordenada de países disponibles (para poder cambiar de filtro)."""
    seen: set[str] = set()
    for c in list_channels(force=force):
        if c.country:
            seen.add(c.country)
    return sorted(seen)


def resolve_channel(url: str, timeout: int = 30) -> ChannelStream | None:
    """Abre la página del canal y devuelve su .m3u8 + el referrer real, o None.

    El CDN de Deporte-Libre exige el Referer del reproductor embebido (p. ej.
    'https://traitaunt.net/'); sin él responde 403. Por eso capturamos también
    el referrer con el que el navegador pidió el manifiesto y lo devolvemos para
    pasárselo a mpv.
    """
    sync_playwright = _import_playwright("deporte-libre")
    if sync_playwright is None:
        return None

    found: dict[str, str] = {}
    try:
        with sync_playwright() as p:
            from .dns_fallback import DEFAULT_DOH_HOSTS
            args = _chromium_host_rules(DEFAULT_DOH_HOSTS)
            browser = p.chromium.launch(headless=True, args=args)
            page = browser.new_context(user_agent=_DEFAULT_UA).new_page()

            def on_request(req) -> None:
                if _STREAM_PATTERN.search(req.url) and "url" not in found:
                    found["url"] = req.url
                    found["referrer"] = req.headers.get("referer", "")

            page.on("request", on_request)
            try:
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(_WAIT_MS)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Deporte-Libre: fallo al resolver %s: %s", url, exc)
        return None

    if "url" not in found:
        log.info("Deporte-Libre: sin .m3u8 para %s", url)
        return None

    # Si no se capturó referrer, caer al dominio del canal como último recurso.
    referrer = found.get("referrer") or referrer_for(url)
    return ChannelStream(url=found["url"], referrer=referrer)


def referrer_for(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/" if parsed.netloc else ""
