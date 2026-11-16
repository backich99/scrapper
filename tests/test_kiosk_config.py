"""Comprobación del lector de configuración del kiosk (scraper/kiosk_config.py).

El proyecto no tiene runner de tests; se escribe como script autónomo con
`assert` para poder correrlo con:
    PYTHONPATH=. python tests/test_kiosk_config.py
(También funciona bajo pytest si estuviera instalado.)

Cubre R8.1 (lectura de parámetros), R8.2 (overrides) y R8.3 (defaults seguros
+ advertencia ante archivo faltante o valores inválidos).
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scraper.kiosk_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    KioskConfig,
    load_kiosk_config,
)


def _write(contents: str) -> str:
    """Escribe un YAML temporal y devuelve su ruta."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(contents))
    return path


def test_real_config_loads_expected_defaults() -> None:
    """(R8.1) config/kiosk.yaml del repo carga los valores del diseño."""
    cfg = load_kiosk_config(DEFAULT_CONFIG_PATH)
    assert cfg.plugin.url == "http://localhost:7000", cfg.plugin.url
    assert cfg.plugin.resolve_timeout == 30, cfg.plugin.resolve_timeout
    assert cfg.plugin.retry_delay == 4, cfg.plugin.retry_delay
    assert cfg.playback.max_resolution == 1080, cfg.playback.max_resolution
    assert cfg.schedule.cache_ttl == 604800, cfg.schedule.cache_ttl
    assert cfg.schedule.timezone == "America/Mexico_City", cfg.schedule.timezone
    assert "nflverse" in cfg.schedule.csv_url, cfg.schedule.csv_url


def test_missing_file_uses_safe_defaults() -> None:
    """(R8.3) Un archivo inexistente devuelve los defaults sin lanzar."""
    cfg = load_kiosk_config("/no/such/kiosk.yaml")
    assert isinstance(cfg, KioskConfig)
    assert cfg.plugin.url == "http://localhost:7000"
    assert cfg.plugin.resolve_timeout == 30
    assert cfg.playback.max_resolution == 1080


def test_values_override_defaults() -> None:
    """(R8.2) Un valor definido en el archivo reemplaza al default."""
    path = _write(
        """
        plugin:
          url: "http://localhost:9999"
          resolve_timeout: 15
        playback:
          max_resolution: 720
        """
    )
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.plugin.url == "http://localhost:9999", cfg.plugin.url
    assert cfg.plugin.resolve_timeout == 15, cfg.plugin.resolve_timeout
    assert cfg.playback.max_resolution == 720, cfg.playback.max_resolution
    # Los no especificados conservan el default.
    assert cfg.plugin.retry_delay == 4, cfg.plugin.retry_delay
    assert cfg.schedule.timezone == "America/Mexico_City"


def test_invalid_values_fall_back_to_defaults() -> None:
    """(R8.3) Valores inválidos (vacío, negativo, no-entero, bool) caen al default."""
    path = _write(
        """
        plugin:
          url: ""
          resolve_timeout: -5
          retry_delay: "nope"
        playback:
          max_resolution: true
        """
    )
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.plugin.url == "http://localhost:7000", cfg.plugin.url
    assert cfg.plugin.resolve_timeout == 30, cfg.plugin.resolve_timeout
    assert cfg.plugin.retry_delay == 4, cfg.plugin.retry_delay
    assert cfg.playback.max_resolution == 1080, cfg.playback.max_resolution


def test_empty_and_malformed_files_use_defaults() -> None:
    """(R8.3) Archivo vacío o YAML malformado no rompen la carga."""
    empty = _write("")
    malformed = _write("key: : : [unbalanced")
    try:
        cfg_empty = load_kiosk_config(empty)
        cfg_bad = load_kiosk_config(malformed)
    finally:
        os.remove(empty)
        os.remove(malformed)
    assert cfg_empty.plugin.resolve_timeout == 30
    assert cfg_bad.plugin.url == "http://localhost:7000"


def test_non_mapping_root_uses_defaults() -> None:
    """(R8.3) Si la raíz del YAML no es un mapa, se usan los defaults."""
    path = _write("- just\n- a\n- list\n")
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.plugin.url == "http://localhost:7000"
    assert cfg.schedule.cache_ttl == 604800


def test_remote_defaults_without_section() -> None:
    """Sin sección ``remote`` se usan los defaults del control remoto."""
    path = _write("plugin:\n  retry_delay: 2\n")
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.remote.enabled is True
    assert cfg.remote.bind == "0.0.0.0"
    assert cfg.remote.port == 8080


def test_remote_valid_values() -> None:
    """Valores válidos de ``remote`` reemplazan a los defaults."""
    path = _write(
        """
        remote:
          enabled: false
          bind: "127.0.0.1"
          port: 9090
        """
    )
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.remote.enabled is False
    assert cfg.remote.bind == "127.0.0.1"
    assert cfg.remote.port == 9090


def test_remote_invalid_values_fall_back() -> None:
    """Puerto fuera de rango/bool, enabled no-bool y bind vacío caen al default."""
    for port in ("0", "70000", "true"):
        path = _write(f"remote:\n  port: {port}\n")
        try:
            cfg = load_kiosk_config(path)
        finally:
            os.remove(path)
        assert cfg.remote.port == 8080, (port, cfg.remote.port)
    path = _write('remote:\n  enabled: "si"\n  bind: ""\n')
    try:
        cfg = load_kiosk_config(path)
    finally:
        os.remove(path)
    assert cfg.remote.enabled is True
    assert cfg.remote.bind == "0.0.0.0"


class KioskConfigTests(unittest.TestCase):
    """Adaptador para que `unittest discover` recoja las funciones de arriba."""


def _make_case(fn):
    def case(self) -> None:
        fn()

    case.__doc__ = fn.__doc__
    return case


for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn):
        setattr(KioskConfigTests, _name, _make_case(_fn))


if __name__ == "__main__":
    test_real_config_loads_expected_defaults()
    test_missing_file_uses_safe_defaults()
    test_values_override_defaults()
    test_invalid_values_fall_back_to_defaults()
    test_empty_and_malformed_files_use_defaults()
    test_non_mapping_root_uses_defaults()
    test_remote_defaults_without_section()
    test_remote_valid_values()
    test_remote_invalid_values_fall_back()
    print("OK: 9 comprobaciones pasaron")
