"""Resolución DNS-over-HTTPS (DoH) de respaldo para dominios concretos.

Motivación: el DNS de la red (AdGuard) filtra de forma intermitente algunos
dominios de streaming (streameast, streame.center, edgestream*, ESPN). Para no
cambiar el DNS del sistema (y conservar el filtrado global), este módulo resuelve
esos dominios vía DoH de Cloudflare y crea una Session de requests que conecta a
la IP resuelta manteniendo el nombre de host (Host header + SNI TLS).

Uso:
    from scraper.dns_fallback import make_session
    session = make_session()
    r = session.get("https://v2.streameast.ga/nfl-streams/", timeout=20)
"""

from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import connection as urllib3_connection

log = logging.getLogger(__name__)

# DoH de Cloudflare (JSON API). Se resuelve por IP fija para no depender del DNS local.
_DOH_ENDPOINT = "https://1.1.1.1/dns-query"
_CACHE_TTL = 300  # segundos

# Caché simple: host -> (ip, timestamp)
_cache: dict[str, tuple[str, float]] = {}


def resolve_doh(host: str, timeout: int = 8) -> str | None:
    """Resuelve un host a una IPv4 usando DoH de Cloudflare. None si falla."""
    now = time.time()
    cached = _cache.get(host)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        r = requests.get(
            _DOH_ENDPOINT,
            params={"name": host, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        for ans in data.get("Answer", []):
            if ans.get("type") == 1:  # registro A
                ip = ans.get("data")
                if ip:
                    _cache[host] = (ip, now)
                    return ip
    except (requests.RequestException, ValueError) as exc:
        log.debug("DoH no pudo resolver %s: %s", host, exc)
    return None


class _DoHAdapter(HTTPAdapter):
    """HTTPAdapter que, para hosts en 'hosts', conecta a la IP resuelta por DoH.

    Mantiene el Host/SNI original (conecta por IP pero verifica el certificado
    contra el hostname real), de modo que TLS sigue siendo válido.
    """

    def __init__(self, hosts: set[str], *args, **kwargs) -> None:
        self._doh_hosts = {h.lower() for h in hosts}
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        host = requests.utils.urlparse(request.url).hostname or ""
        host_l = host.lower()
        # ¿Coincide con alguno de los dominios objetivo (exacto o subdominio)?
        target = None
        for h in self._doh_hosts:
            if host_l == h or host_l.endswith("." + h):
                target = host_l
                break
        if target:
            ip = resolve_doh(host)
            if ip:
                # Fuerza la resolución de este host a la IP DoH durante la conexión.
                _pin_host_to_ip(host, ip)
        return super().send(request, **kwargs)


# --- Pin de host->IP a nivel de urllib3 (sin tocar /etc/hosts) --------------- #
_pinned: dict[str, str] = {}
_orig_create_connection = urllib3_connection.create_connection


def _patched_create_connection(address, *args, **kwargs):
    host, port = address
    ip = _pinned.get(host.lower())
    if ip:
        return _orig_create_connection((ip, port), *args, **kwargs)
    return _orig_create_connection(address, *args, **kwargs)


def _pin_host_to_ip(host: str, ip: str) -> None:
    _pinned[host.lower()] = ip
    # Instala el parche una sola vez.
    if urllib3_connection.create_connection is not _patched_create_connection:
        urllib3_connection.create_connection = _patched_create_connection


# Dominios que AdGuard filtra intermitentemente y necesitamos resolver siempre.
DEFAULT_DOH_HOSTS = {
    "streameast.ga",
    "streamea.st",
    "streame.center",
    "edgestream1.pro", "edgestream2.pro", "edgestream3.pro", "edgestream4.pro",
    "edgestream5.pro", "edgestream6.pro", "edgestream7.pro", "edgestream8.pro",
    "espn.com",
    "api.espn.com",
}


def make_session(extra_hosts: set[str] | None = None) -> requests.Session:
    """Crea una Session de requests con fallback DoH para los dominios objetivo.

    Para hosts no incluidos, se comporta como una Session normal (DNS del sistema).
    """
    hosts = set(DEFAULT_DOH_HOSTS)
    if extra_hosts:
        hosts |= {h.lower() for h in extra_hosts}
    session = requests.Session()
    adapter = _DoHAdapter(hosts)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
