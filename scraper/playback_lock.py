"""Lock de reproducción en /run/ — lado de escritura/limpieza (Tarea 10.1).

Mientras hay una reproducción activa, el kiosk mantiene un fichero de estado en
``/run/scrapper/playback.lock`` que contiene el **PID del proceso mpv** en curso
(Requisito 10.1). Los procesos de actualización lo consultan para no reiniciar
servicios durante un partido (Requisitos 10.3/10.4). El estado se limpia al
detener la reproducción o cuando el stream termina (Requisito 10.2).

Por qué ``/run/`` (tmpfs)
-------------------------
``/run`` es un sistema de ficheros en memoria que se vacía en cada arranque. Así,
un lock que quedara obsoleto por un corte de energía desaparece solo al reiniciar
la Raspberry, sin bloquear las actualizaciones de forma indefinida (más robusto
que guardarlo en ``data/``). El directorio ``/run/scrapper/`` se crea al vuelo si
no existe (o vía ``RuntimeDirectory=scrapper`` en la unidad systemd del kiosk).

La Tarea 10.1 cubre **escribir** (``acquire``) y **limpiar** (``release``) el
lock. La Tarea 10.2 añade la **lectura y validación de obsolescencia** que
consumen los scripts de update: ``read`` (parsea el PID), ``is_active`` (lock
presente **y** PID vivo) y ``clear_if_stale`` (borra un lock obsoleto). Todo
sobre la misma constante ``LOCK_PATH``.

Un lock cuyo PID ya no corresponde a un proceso vivo se considera **obsoleto**
(p. ej. la app se cerró de forma inesperada): ``is_active`` lo trata como "sin
reproducción" para no bloquear actualizaciones de forma indefinida (R10.4).

Marca de actualización pendiente (Tarea 12.3)
---------------------------------------------
``PENDING_PATH`` (``/run/scrapper/update.pending``) es una marca que crea
``setup/update-repo.sh`` cuando pospone una actualización por haber reproducción
activa. ``pending_exists`` solo la **consulta**: la UI la usa al liberar el lock
para disparar ``scrapper-update.service`` de inmediato (R1.9) en vez de esperar
al siguiente tick del timer. El borrado de la marca es responsabilidad de
``update-repo.sh``.

CLI para scripts de shell (Tarea 12)
------------------------------------
Como los scripts ``.sh`` no pueden importar Python cómodamente, el módulo expone
una entrada ejecutable pensada para usarse en condicionales de shell::

    python -m scraper.playback_lock --check   # (--check es opcional)

Códigos de salida (documentados para que la Tarea 12 pueda confiar en ellos):

- ``0`` → hay reproducción activa (lock presente y PID vivo). El script de
  update debe **marcar PENDING y salir** sin reiniciar servicios (R10.3).
- ``1`` → no hay reproducción activa (lock ausente, o presente pero con PID
  muerto/obsoleto → se limpia). El script puede proceder con la actualización.
- ``2`` → error de uso (argumento desconocido).

Ejemplo de uso en ``update-repo.sh``::

    if python -m scraper.playback_lock; then
        marcar_pending; exit 0        # reproducción en curso
    fi
    # ... proceder con git fetch / merge / reinicios ...

Como efecto secundario, la CLI limpia un lock obsoleto que encuentre (vía
``clear_if_stale``), de modo que el ``/run`` no acumule locks muertos.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Ruta del lock de reproducción. En producción vive en /run (tmpfs); los tests
# pasan una ruta temporal propia para no tocar /run.
LOCK_PATH = Path("/run/scrapper/playback.lock")

# Marca de "actualización pendiente" (Tarea 12). Vive junto al lock en /run
# (tmpfs): la crea ``setup/update-repo.sh`` cuando pospone una actualización por
# haber reproducción activa, y la limpia el propio script al aplicarla. La UI
# solo la **consulta** para disparar la actualización al liberar el lock
# (R1.9/R1.10); no la borra (de eso se encarga update-repo.sh). Debe coincidir
# con ``PENDING_FILE`` de update-repo.sh (/run/scrapper/update.pending).
PENDING_PATH = Path("/run/scrapper/update.pending")


def acquire(pid: int, path: Path | str = LOCK_PATH) -> None:
    """Registra el estado de reproducción activa escribiendo el PID de mpv (R10.1).

    Crea el directorio contenedor (``/run/scrapper/`` por defecto) si no existe y
    escribe ``pid`` en el fichero de lock. ``path`` es configurable para que los
    tests apunten a un directorio temporal en lugar de ``/run``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(pid)}\n", encoding="utf-8")
    log.info("Lock de reproducción adquirido (pid %s) en %s", pid, path)


def release(path: Path | str = LOCK_PATH) -> None:
    """Limpia el estado de reproducción activa borrando el lock (R10.2).

    Tolera que el fichero ya no exista (p. ej. doble ``release`` o lock nunca
    creado): en ese caso no hace nada. ``path`` es configurable para los tests.
    """
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    log.info("Lock de reproducción liberado en %s", path)


def pending_exists(path: Path | str = PENDING_PATH) -> bool:
    """Indica si existe la marca de "actualización pendiente" (R1.9/R1.10).

    ``setup/update-repo.sh`` crea esta marca cuando pospone una actualización
    por haber reproducción activa. La UI la consulta al liberar el lock para
    disparar la actualización de inmediato en lugar de esperar al siguiente
    tick del timer. Función pura y tolerante: cualquier error de E/S se trata
    como "sin marca" para no romper el flujo de la UI. ``path`` es configurable
    para los tests (evita tocar ``/run``).
    """
    try:
        return Path(path).exists()
    except OSError as exc:  # permisos u otros errores de E/S
        log.warning("No se pudo comprobar la marca PENDING %s: %s", path, exc)
        return False


def read(path: Path | str = LOCK_PATH) -> int | None:
    """Lee y parsea el PID del lock; ``None`` si no hay lock utilizable.

    Devuelve ``None`` cuando el fichero no existe o su contenido no es un entero
    válido (lock vacío o corrupto). Tolerante por diseño: un lock ilegible se
    trata como "sin reproducción" en lugar de propagar la excepción, para no
    bloquear las actualizaciones por un fichero dañado.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:  # permisos, etc.: trátalo como lock no utilizable
        log.warning("No se pudo leer el lock %s: %s", path, exc)
        return None
    try:
        return int(raw.strip())
    except ValueError:
        log.warning("Lock %s con contenido no numérico: %r", path, raw)
        return None


def _pid_alive(pid: int) -> bool:
    """Indica si un proceso con ese PID sigue vivo (POSIX).

    Usa ``os.kill(pid, 0)``, que no envía señal pero valida la existencia del
    proceso:

    - sin excepción → el proceso existe (vivo),
    - ``ProcessLookupError`` → no existe (muerto),
    - ``PermissionError`` → existe pero es de otro usuario (vivo),
    - PID no positivo → inválido, se considera muerto (``os.kill(0, ...)`` tiene
      semántica de grupo de procesos que no queremos aquí).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_active(path: Path | str = LOCK_PATH) -> bool:
    """``True`` solo si hay una reproducción realmente en curso.

    Es decir: el lock existe **y** su PID sigue vivo (R10.3). Si el lock existe
    pero el PID está muerto, el lock es **obsoleto** y se devuelve ``False``
    (R10.4), de modo que un cierre inesperado de la app no bloquee las
    actualizaciones de forma indefinida.
    """
    pid = read(path)
    if pid is None:
        return False
    return _pid_alive(pid)


def clear_if_stale(path: Path | str = LOCK_PATH) -> bool:
    """Borra el lock si es obsoleto (PID muerto) y devuelve si lo era.

    Devuelve ``True`` cuando encontró un lock con PID muerto y lo eliminó;
    ``False`` cuando no había lock o el PID seguía vivo (en cuyo caso NO toca el
    fichero). Pensado para que los scripts de update dejen limpio ``/run`` sin
    interferir con una reproducción activa.
    """
    pid = read(path)
    if pid is None:
        return False
    if _pid_alive(pid):
        return False
    release(path)
    log.info("Lock de reproducción obsoleto (pid %s) eliminado en %s", pid, path)
    return True


def _main(argv: list[str]) -> int:
    """Entrada CLI para scripts de shell. Ver docstring del módulo.

    Salida ``0`` si hay reproducción activa; ``1`` si no (limpiando un lock
    obsoleto por el camino); ``2`` ante un argumento desconocido.
    """
    args = [a for a in argv if a != "--check"]
    if args:
        print("uso: python -m scraper.playback_lock [--check]", file=sys.stderr)
        return 2
    # Resolvemos LOCK_PATH al llamar (no como default ligado en import) para que
    # la ruta efectiva sea siempre la del módulo en ese momento.
    if is_active(LOCK_PATH):
        return 0
    clear_if_stale(LOCK_PATH)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
