"""Carga y validación de la configuración de fuentes (config/sources.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Settings:
    """Ajustes globales del scraper."""

    output: str = "data/events.json"
    request_timeout: int = 20
    max_depth: int = 4
    max_events: int = 60


@dataclass
class Source:
    """Una fuente a rastrear."""

    name: str
    url: str
    strategy: str = "static"          # "static" | "dynamic" | "hybrid"
    enabled: bool = True
    sports: list[str] = field(default_factory=list)
    link_selector: str = "a"
    follow_pattern: str = r".*"
    stream_pattern: str = r"\.m3u8"
    wait_for: str | None = None
    wait_ms: int = 0                   # Espera extra (ms) para que el reproductor pida el stream
    match_pattern: str | None = None   # (hybrid) regex de los enlaces a páginas de partido
    max_matches: int = 20              # (hybrid) tope de partidos a resolver por ejecución

    def validate(self) -> None:
        """Valida los campos; lanza ValueError con un mensaje claro si algo falla."""
        if not self.name:
            raise ValueError("Una fuente no tiene 'name'.")
        if self.strategy not in ("static", "dynamic", "hybrid"):
            raise ValueError(
                f"[{self.name}] strategy debe ser 'static', 'dynamic' o 'hybrid', no {self.strategy!r}."
            )
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"[{self.name}] url inválida: {self.url!r}.")
        # Compila los patrones para detectar regex inválidas cuanto antes.
        patterns = ["follow_pattern", "stream_pattern"]
        if self.match_pattern:
            patterns.append("match_pattern")
        for attr in patterns:
            try:
                re.compile(getattr(self, attr))
            except re.error as exc:
                raise ValueError(f"[{self.name}] {attr} no es una regex válida: {exc}") from exc


@dataclass
class Config:
    """Configuración completa: ajustes + fuentes."""

    settings: Settings
    sources: list[Source]

    def enabled_sources(self) -> list[Source]:
        return [s for s in self.sources if s.enabled]


def _coerce_source(raw: dict[str, Any]) -> Source:
    """Convierte un dict del YAML en un Source, ignorando claves desconocidas."""
    known = {
        "name", "url", "strategy", "enabled", "sports",
        "link_selector", "follow_pattern", "stream_pattern", "wait_for",
        "wait_ms", "match_pattern", "max_matches",
    }
    filtered = {k: v for k, v in raw.items() if k in known}
    return Source(**filtered)


def load_config(path: str | Path) -> Config:
    """Carga y valida config/sources.yaml.

    Lanza FileNotFoundError si no existe y ValueError si el contenido es inválido.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el fichero de configuración: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("El YAML de configuración debe ser un mapa en la raíz.")

    settings = Settings(**(data.get("settings") or {}))

    raw_sources = data.get("sources") or []
    if not isinstance(raw_sources, list):
        raise ValueError("'sources' debe ser una lista.")

    sources = [_coerce_source(item) for item in raw_sources]
    for src in sources:
        src.validate()

    return Config(settings=settings, sources=sources)
