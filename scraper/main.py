"""Punto de entrada del scraper.

Recorre las fuentes configuradas, resuelve los streams limpios, normaliza los
resultados al esquema de events.json y los escribe de forma atómica.

Uso:
    python -m scraper.main --config config/sources.yaml [--verbose]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .resolvers import StreamCandidate, resolve_source
from .sources import Config, Source, load_config

log = logging.getLogger("scraper")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "evento"


def _event_id(source_name: str, candidate: StreamCandidate) -> str:
    """ID estable derivado de la fuente + URL, con un fragmento legible del título."""
    base = _slugify(candidate.title) if candidate.title else "stream"
    digest = hashlib.sha1(f"{source_name}|{candidate.url}".encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def normalize(source: Source, candidates: list[StreamCandidate]) -> list[dict]:
    """Convierte candidatos en eventos con el esquema de events.json."""
    sport = source.sports[0] if source.sports else "unknown"
    league = sport.upper()
    # Referer para el reproductor: origen del sitio de la fuente (muchos CDN lo exigen).
    parsed = urlparse(source.url)
    referrer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.netloc else ""
    events: list[dict] = []
    for c in candidates:
        events.append(
            {
                "id": _event_id(source.name, c),
                "title": c.title or "Stream en directo",
                "league": league,
                "sport": sport,
                "start_time": None,          # Desconocido en esta versión; la UI lo tolera.
                "stream_url": c.url,
                "referrer": referrer,
                "source": source.name,
                "status": "live",
            }
        )
    return events


def dedupe(events: list[dict]) -> list[dict]:
    """Elimina eventos con el mismo id, conservando el primero."""
    seen: set[str] = set()
    unique: list[dict] = []
    for ev in events:
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        unique.append(ev)
    return unique


def write_atomic(output_path: Path, payload: dict) -> None:
    """Escribe el JSON a un temporal y lo renombra (atómico) sobre el destino.

    Así la UI nunca lee un fichero a medio escribir.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, output_path)  # atómico en el mismo sistema de ficheros
    except BaseException:
        # Limpia el temporal si algo falla.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def run(config: Config) -> dict:
    """Ejecuta el scraping completo y devuelve el payload de events.json."""
    all_events: list[dict] = []
    for source in config.enabled_sources():
        log.info("Procesando fuente: %s (%s)", source.name, source.strategy)
        try:
            candidates = resolve_source(
                source,
                timeout=config.settings.request_timeout,
                max_depth=config.settings.max_depth,
            )
        except Exception as exc:  # noqa: BLE001 - una fuente no debe tumbar el resto
            log.warning("Fuente %s falló y se omite: %s", source.name, exc)
            continue
        all_events.extend(normalize(source, candidates))

    events = dedupe(all_events)[: config.settings.max_events]
    return {"generated_at": _now_iso(), "events": events}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper de streams deportivos.")
    parser.add_argument("--config", default="config/sources.yaml", help="Ruta a sources.yaml")
    parser.add_argument("--verbose", action="store_true", help="Log detallado (DEBUG)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        log.error("Configuración inválida: %s", exc)
        return 2

    payload = run(config)

    output = Path(config.settings.output)
    if not payload["events"]:
        # No sobrescribir con lista vacía por un fallo puntual: conservar la última salida válida.
        if output.exists():
            log.warning("No se obtuvieron eventos; se conserva %s anterior.", output)
            return 0
        log.warning("No se obtuvieron eventos y no hay salida previa; escribiendo lista vacía.")

    write_atomic(output, payload)
    log.info("Escritos %d eventos en %s", len(payload["events"]), output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
