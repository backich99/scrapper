"""Tests del lock de reproducción (scraper/playback_lock.py, Tarea 10.1).

Usa unittest (stdlib) para no añadir dependencias. Las pruebas NUNCA tocan
``/run``: pasan una ruta dentro de un directorio temporal propio.

Ejecución:
    PYTHONPATH=. python scraper/test_playback_lock.py
(También funciona bajo pytest si estuviera instalado.)

Cubre:
- ``acquire`` crea el directorio contenedor y escribe el PID (R10.1),
- ``acquire`` sobre un lock existente lo sobrescribe,
- ``release`` borra el lock (R10.2),
- ``release`` sin lock presente no lanza (tolerante a fichero ausente),
- un ciclo acquire→release deja el estado limpio.

Tarea 10.2 (lectura/validación de obsolescencia):
- ``read`` con lock válido / ausente / corrupto,
- ``is_active`` con PID vivo (el proceso actual), con PID muerto/obsoleto
  (``_pid_alive`` mockeado) y sin lock (R10.3/R10.4),
- ``clear_if_stale`` elimina locks obsoletos y respeta los vivos,
- la CLI ``_main`` y sus códigos de salida.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scraper import playback_lock  # noqa: E402


class PlaybackLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Ruta anidada para verificar que acquire crea los directorios padre
        # (equivalente a /run/scrapper/ en producción), sin tocar /run.
        self.lock_path = Path(self._tmp.name) / "scrapper" / "playback.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_acquire_crea_directorio_y_escribe_pid(self) -> None:
        self.assertFalse(self.lock_path.parent.exists())
        playback_lock.acquire(4321, path=self.lock_path)
        self.assertTrue(self.lock_path.exists())
        self.assertEqual(self.lock_path.read_text(encoding="utf-8").strip(), "4321")

    def test_acquire_sobrescribe_lock_existente(self) -> None:
        playback_lock.acquire(111, path=self.lock_path)
        playback_lock.acquire(222, path=self.lock_path)
        self.assertEqual(self.lock_path.read_text(encoding="utf-8").strip(), "222")

    def test_acquire_acepta_ruta_como_str(self) -> None:
        playback_lock.acquire(999, path=str(self.lock_path))
        self.assertEqual(self.lock_path.read_text(encoding="utf-8").strip(), "999")

    def test_release_borra_el_lock(self) -> None:
        playback_lock.acquire(555, path=self.lock_path)
        self.assertTrue(self.lock_path.exists())
        playback_lock.release(path=self.lock_path)
        self.assertFalse(self.lock_path.exists())

    def test_release_sin_lock_no_lanza(self) -> None:
        # Fichero ausente: release debe ser tolerante (doble stop, lock nunca
        # creado al correr headless, etc.).
        self.assertFalse(self.lock_path.exists())
        playback_lock.release(path=self.lock_path)  # no debe lanzar
        self.assertFalse(self.lock_path.exists())

    def test_ciclo_acquire_release_queda_limpio(self) -> None:
        playback_lock.acquire(777, path=self.lock_path)
        playback_lock.release(path=self.lock_path)
        self.assertFalse(self.lock_path.exists())
        # Un segundo release tras limpiar sigue siendo seguro.
        playback_lock.release(path=self.lock_path)


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "scrapper" / "playback.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_read_lock_valido_devuelve_pid(self) -> None:
        playback_lock.acquire(1234, path=self.lock_path)
        self.assertEqual(playback_lock.read(path=self.lock_path), 1234)

    def test_read_lock_ausente_devuelve_none(self) -> None:
        self.assertFalse(self.lock_path.exists())
        self.assertIsNone(playback_lock.read(path=self.lock_path))

    def test_read_lock_corrupto_devuelve_none(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("no-soy-un-pid\n", encoding="utf-8")
        self.assertIsNone(playback_lock.read(path=self.lock_path))

    def test_read_lock_vacio_devuelve_none(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("", encoding="utf-8")
        self.assertIsNone(playback_lock.read(path=self.lock_path))

    def test_read_acepta_ruta_como_str(self) -> None:
        playback_lock.acquire(42, path=self.lock_path)
        self.assertEqual(playback_lock.read(path=str(self.lock_path)), 42)


class IsActiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "scrapper" / "playback.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_is_active_true_con_pid_vivo(self) -> None:
        # El propio proceso de test está, por definición, vivo.
        playback_lock.acquire(os.getpid(), path=self.lock_path)
        self.assertTrue(playback_lock.is_active(path=self.lock_path))

    def test_is_active_false_sin_lock(self) -> None:
        self.assertFalse(self.lock_path.exists())
        self.assertFalse(playback_lock.is_active(path=self.lock_path))

    def test_is_active_false_con_pid_muerto(self) -> None:
        # Lock obsoleto: existe el fichero pero el PID no está vivo (mock
        # determinista para no depender de un PID real concreto).
        playback_lock.acquire(999999, path=self.lock_path)
        with mock.patch.object(playback_lock, "_pid_alive", return_value=False):
            self.assertFalse(playback_lock.is_active(path=self.lock_path))

    def test_is_active_false_con_lock_corrupto(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text("basura", encoding="utf-8")
        self.assertFalse(playback_lock.is_active(path=self.lock_path))


class PidAliveTests(unittest.TestCase):
    def test_proceso_actual_esta_vivo(self) -> None:
        self.assertTrue(playback_lock._pid_alive(os.getpid()))

    def test_pid_no_positivo_se_considera_muerto(self) -> None:
        self.assertFalse(playback_lock._pid_alive(0))
        self.assertFalse(playback_lock._pid_alive(-1))

    def test_process_lookup_error_es_muerto(self) -> None:
        with mock.patch.object(playback_lock.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(playback_lock._pid_alive(123))

    def test_permission_error_es_vivo(self) -> None:
        # El proceso existe pero pertenece a otro usuario → sigue vivo.
        with mock.patch.object(playback_lock.os, "kill", side_effect=PermissionError):
            self.assertTrue(playback_lock._pid_alive(123))


class ClearIfStaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "scrapper" / "playback.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sin_lock_devuelve_false(self) -> None:
        self.assertFalse(playback_lock.clear_if_stale(path=self.lock_path))

    def test_lock_vivo_no_se_toca(self) -> None:
        playback_lock.acquire(os.getpid(), path=self.lock_path)
        self.assertFalse(playback_lock.clear_if_stale(path=self.lock_path))
        self.assertTrue(self.lock_path.exists())

    def test_lock_obsoleto_se_elimina(self) -> None:
        playback_lock.acquire(999999, path=self.lock_path)
        with mock.patch.object(playback_lock, "_pid_alive", return_value=False):
            self.assertTrue(playback_lock.clear_if_stale(path=self.lock_path))
        self.assertFalse(self.lock_path.exists())


class PendingExistsTests(unittest.TestCase):
    """Marca de "actualización pendiente" (Tarea 12.3).

    ``pending_exists`` solo consulta la existencia del fichero PENDING; la crea
    ``update-repo.sh`` y la limpia él mismo. Las pruebas usan una ruta temporal
    para no tocar ``/run``.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pending_path = Path(self._tmp.name) / "scrapper" / "update.pending"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pending_ausente_devuelve_false(self) -> None:
        self.assertFalse(self.pending_path.exists())
        self.assertFalse(playback_lock.pending_exists(path=self.pending_path))

    def test_pending_presente_devuelve_true(self) -> None:
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_path.write_text("", encoding="utf-8")  # marca vacía, como el script
        self.assertTrue(playback_lock.pending_exists(path=self.pending_path))

    def test_pending_acepta_ruta_como_str(self) -> None:
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_path.touch()
        self.assertTrue(playback_lock.pending_exists(path=str(self.pending_path)))

    def test_pending_error_de_io_se_trata_como_ausente(self) -> None:
        # Un error de E/S al comprobar (p. ej. permisos) no debe propagarse:
        # la UI lo trata como "sin marca" para no romperse.
        with mock.patch.object(
            playback_lock.Path, "exists", side_effect=OSError("boom")
        ):
            self.assertFalse(playback_lock.pending_exists(path=self.pending_path))

    def test_pending_default_path_es_run_scrapper(self) -> None:
        # Parity con update-repo.sh: /run/scrapper/update.pending.
        self.assertEqual(
            str(playback_lock.PENDING_PATH), "/run/scrapper/update.pending"
        )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "scrapper" / "playback.lock"
        # Redirigir LOCK_PATH por defecto a una ruta temporal: la CLI usa los
        # defaults del módulo, así que no tocamos /run.
        self._patcher = mock.patch.object(playback_lock, "LOCK_PATH", self.lock_path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmp.cleanup()

    def test_cli_exit_0_con_reproduccion_activa(self) -> None:
        playback_lock.acquire(os.getpid(), path=self.lock_path)
        self.assertEqual(playback_lock._main([]), 0)
        self.assertEqual(playback_lock._main(["--check"]), 0)

    def test_cli_exit_1_sin_lock(self) -> None:
        self.assertEqual(playback_lock._main([]), 1)

    def test_cli_exit_1_y_limpia_lock_obsoleto(self) -> None:
        playback_lock.acquire(999999, path=self.lock_path)
        with mock.patch.object(playback_lock, "_pid_alive", return_value=False):
            self.assertEqual(playback_lock._main([]), 1)
        # Efecto secundario documentado: el lock obsoleto queda limpio.
        self.assertFalse(self.lock_path.exists())

    def test_cli_exit_2_con_argumento_desconocido(self) -> None:
        self.assertEqual(playback_lock._main(["--loquesea"]), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
