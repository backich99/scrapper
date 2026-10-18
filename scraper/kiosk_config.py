"""Carga de la configuración del kiosk (config/kiosk.yaml).

A diferencia de ``scraper/sources.py`` (que lanza excepciones ante errores),
este lector está pensado para arranque del kiosk: nunca falla de forma dura.
Si el archivo falta o un valor es inválido, usa defaults seguros y registra una
advertencia en el log (Requisito 8.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# Ruta por defecto del archivo, relativa a la raíz del repositorio
# (scraper/kiosk_config.py -> ../config/kiosk.yaml).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "kiosk.yaml"


@dataclass
class PluginConfig:
    """Parámetros del servicio Node del plugin."""

    url: str = "http://localhost:7000"
    resolve_timeout: int = 30   # segundos máx. de resolución antes de abortar (R6.6)
    retry_delay: int = 4        # segundos antes de reintentar /stream una vez


@dataclass
class PlaybackConfig:
    """Parámetros de reproducción."""

    max_resolution: int = 1080  # tope de altura; mejor stream <= este valor (R6.3)


@dataclass
class ScheduleConfig:
    """Parámetros de los fixtures de nflverse (CSV)."""

    csv_url: str = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
    cache_ttl: int = 604800     # validez de la caché del CSV: 1 semana (R11.5)
    timezone: str = "America/Mexico_City"


@dataclass
class KioskConfig:
    """Configuración completa del kiosk."""

    plugin: PluginConfig = field(default_factory=PluginConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)


def _as_str(section: dict[str, Any], key: str, default: str) -> str:
    """Lee una cadena no vacía; si falta o es inválida, devuelve el default y avisa."""
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, str) and value.strip():
        return value
    log.warning(
        "kiosk.yaml: valor inválido para %r (%r); se usa el default %r.",
        key, value, default,
    )
    return default


def _as_positive_int(section: dict[str, Any], key: str, default: int) -> int:
    """Lee un entero positivo; si falta o es inválido, devuelve el default y avisa."""
    if key not in section:
        return default
    value = section[key]
    # bool es subclase de int; lo rechazamos explícitamente para no aceptar True/False.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    log.warning(
        "kiosk.yaml: valor inválido para %r (%r); se usa el default %r.",
        key, value, default,
    )
    return default


def _as_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Devuelve una subsección como dict; si falta o no es un mapa, devuelve {}."""
    value = data.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    log.warning(
        "kiosk.yaml: la sección %r debe ser un mapa (%r); se usan los defaults.",
        key, value,
    )
    return {}


def load_kiosk_config(path: str | Path | None = None) -> KioskConfig:
    """Carga config/kiosk.yaml con defaults seguros.

    Nunca lanza: si el archivo falta, no es legible, no es YAML válido, o algún
    valor es inválido, usa los defaults de los dataclasses y registra una
    advertencia (Requisito 8.3).
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH

    if not path.exists():
        log.warning(
            "kiosk.yaml no encontrado en %s; se usan valores por defecto.", path
        )
        return KioskConfig()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning(
            "No se pudo leer kiosk.yaml (%s); se usan valores por defecto.", exc
        )
        return KioskConfig()

    if raw is None:
        # Archivo vacío: válido, equivale a usar todos los defaults.
        return KioskConfig()
    if not isinstance(raw, dict):
        log.warning(
            "kiosk.yaml debe ser un mapa en la raíz; se usan valores por defecto."
        )
        return KioskConfig()

    plugin_raw = _as_section(raw, "plugin")
    playback_raw = _as_section(raw, "playback")
    schedule_raw = _as_section(raw, "schedule")

    plugin = PluginConfig(
        url=_as_str(plugin_raw, "url", PluginConfig.url),
        resolve_timeout=_as_positive_int(
            plugin_raw, "resolve_timeout", PluginConfig.resolve_timeout
        ),
        retry_delay=_as_positive_int(
            plugin_raw, "retry_delay", PluginConfig.retry_delay
        ),
    )
    playback = PlaybackConfig(
        max_resolution=_as_positive_int(
            playback_raw, "max_resolution", PlaybackConfig.max_resolution
        ),
    )
    schedule = ScheduleConfig(
        csv_url=_as_str(schedule_raw, "csv_url", ScheduleConfig.csv_url),
        cache_ttl=_as_positive_int(
            schedule_raw, "cache_ttl", ScheduleConfig.cache_ttl
        ),
        timezone=_as_str(schedule_raw, "timezone", ScheduleConfig.timezone),
    )

    return KioskConfig(plugin=plugin, playback=playback, schedule=schedule)
