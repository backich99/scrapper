#!/usr/bin/env bash
#
# update-repo.sh — Auto-update del repositorio propio (backich99/scrapper).
#
# Trae y aplica (fast-forward) los commits nuevos de `origin/main`, actualiza el
# submódulo del plugin si cambió, y reinicia los servicios afectados (UI del
# kiosk + plugin.service) para que tomen el código nuevo. Pensado para correr
# tanto desde el timer horario (Tarea 12.2, vía scrapper-update.service) como a
# mano a través del enlace `update-now`.
#
# Regla de oro (R1.8 / R10.3): NUNCA interrumpe un partido. Si hay una
# reproducción activa (lock presente y proceso mpv vivo), marca la actualización
# como PENDIENTE y sale 0 sin tocar nada; la UI la aplicará al pulsar Detener
# (Tarea 12.3) o el siguiente tick del timer la reintentará (R1.10).
#
# Robustez (R1.6 / R1.7): si el working tree impide el fast-forward, o si falla
# cualquier operación de git/servicio, se registra el motivo y se conserva el
# estado anterior en ejecución (no se reinician servicios ante un fallo, no se
# deja el repo a medias; nada de resets destructivos).
#
# Lógica (diseño §9):
#   1. si playback.lock activo (mpv vivo) -> marcar PENDING, salir 0  (R1.8, R10.3)
#   2. git fetch origin main
#   3. si no hay commits nuevos y no hay PENDING -> salir 0           (R1.3)
#   4. si el working tree impide el fast-forward -> log + abortar     (R1.6)
#   5. git merge --ff-only origin/main                                (R1.2)
#   6. si el submódulo cambió -> git submodule update --init
#   7. reiniciar servicios afectados: UI del kiosk + plugin.service   (R1.4)
#   8. limpiar la marca PENDING
#   9. cualquier fallo -> log y conservar el estado anterior          (R1.7)
#
# Uso:
#   sudo bash setup/update-repo.sh [--force] [usuario]
#   update-now [--force]            # enlace de invocación manual (R1.5)
#
# Opción --force (R1.5): fuerza el intento de aplicar aunque no haya cambios
# detectados / exista PENDING, pero IGUALMENTE respeta el lock de reproducción:
# nunca interrumpe un partido (para eso está el botón Detener de la UI).
#
# Códigos de salida:
#   0  éxito, o nada que hacer, o pospuesto por reproducción activa.
#   >0 fallo (red/git/servicio); el estado anterior se conserva en ejecución.
#
set -uo pipefail
# OJO: no usamos `-e`. Varios pasos (lock CLI, git fetch, merge) devuelven
# códigos != 0 de forma esperada y los manejamos explícitamente; `set -e`
# abortaría el script a medias y podría dejar el kiosk sin reiniciar servicios
# tras una actualización ya aplicada. Preferimos control de errores explícito.

# --- Mensajería -------------------------------------------------------------
# Todo va al journal de systemd (stdout/stderr). Formato "==>" consistente con
# el resto de scripts de setup/.
log()  { echo "==> $*"; }
warn() { echo "AVISO: $*" >&2; }
err()  { echo "ERROR: $*" >&2; }

# --- Parseo de argumentos ---------------------------------------------------
FORCE=0
TARGET_USER=""
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --*) err "opción desconocida: $arg"; exit 2 ;;
        *)   TARGET_USER="$arg" ;;
    esac
done

# --- Rutas y contexto -------------------------------------------------------
# Raíz del proyecto = carpeta padre de este script (setup/). Resolvemos el
# enlace simbólico de `update-now` para que PROJECT_DIR sea siempre el repo real.
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Marca de actualización pendiente. Vive en /run (tmpfs), igual que el lock de
# reproducción: se limpia sola en cada arranque y comparte ubicación con él.
PENDING_DIR="/run/scrapper"
PENDING_FILE="$PENDING_DIR/update.pending"

REMOTE="origin"
BRANCH="main"

# Dueño del repo: por defecto, el propietario del .git (así `git` corre con los
# permisos correctos aunque el script se ejecute como root vía systemd). Un
# argumento posicional lo sobreescribe.
if [[ -z "$TARGET_USER" ]]; then
    TARGET_USER="$(stat -c '%U' "$PROJECT_DIR/.git" 2>/dev/null || true)"
    [[ -z "$TARGET_USER" || "$TARGET_USER" == "UNKNOWN" ]] && TARGET_USER="karlos"
fi

# Intérprete de Python para el helper del lock. Preferimos el venv del kiosk
# (coincide con 05-kiosk.sh); si no está, python3 del sistema sirve porque
# playback_lock.py no tiene dependencias de terceros.
USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 2>/dev/null || true)"
PYTHON=""
if [[ -n "$USER_HOME" && -x "$USER_HOME/scrapper-venv/bin/python" ]]; then
    PYTHON="$USER_HOME/scrapper-venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
fi

# Ejecuta git como el dueño del repo cuando corremos como root; si ya somos ese
# usuario (o no somos root), ejecuta directo. Evita que git marque el repo como
# "dubious ownership" y que se creen objetos propiedad de root.
run_git() {
    if [[ "$(id -u)" -eq 0 && "$TARGET_USER" != "root" ]]; then
        sudo -u "$TARGET_USER" git -C "$PROJECT_DIR" "$@"
    else
        git -C "$PROJECT_DIR" "$@"
    fi
}

# --- Marca PENDING ----------------------------------------------------------
mark_pending() {
    mkdir -p "$PENDING_DIR" 2>/dev/null || true
    : > "$PENDING_FILE" 2>/dev/null \
        && log "Actualización marcada como PENDIENTE en $PENDING_FILE." \
        || warn "no se pudo escribir la marca PENDING en $PENDING_FILE."
}

clear_pending() {
    if [[ -e "$PENDING_FILE" ]]; then
        rm -f "$PENDING_FILE" 2>/dev/null \
            && log "Marca PENDING limpiada." \
            || warn "no se pudo limpiar la marca PENDING $PENDING_FILE."
    fi
}

has_pending() { [[ -e "$PENDING_FILE" ]]; }

# --- 1. ¿Hay reproducción activa? (R1.8 / R10.3) ----------------------------
# El helper de la Tarea 10.2 sale 0 si hay reproducción activa (lock + PID vivo)
# y 1 si no (limpiando de paso un lock obsoleto). --force NO cambia esta regla.
if [[ -n "$PYTHON" ]]; then
    if ( cd "$PROJECT_DIR" && "$PYTHON" -m scraper.playback_lock ); then
        log "Reproducción activa en curso: se pospone la actualización."
        mark_pending
        exit 0
    fi
else
    warn "no se encontró intérprete de Python; no se pudo comprobar el lock de reproducción."
    warn "por seguridad, se pospone la actualización (se marca PENDING)."
    mark_pending
    exit 0
fi

# --- 2. git fetch origin main (R1.1) ----------------------------------------
log "Buscando cambios en $REMOTE/$BRANCH ..."
if ! run_git fetch "$REMOTE" "$BRANCH"; then
    err "falló 'git fetch $REMOTE $BRANCH' (¿sin red?). Se conserva el estado actual."
    exit 1
fi

# --- 3. ¿Hay commits nuevos? (R1.3) -----------------------------------------
LOCAL_REV="$(run_git rev-parse HEAD 2>/dev/null || true)"
REMOTE_REV="$(run_git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || true)"

if [[ -z "$REMOTE_REV" ]]; then
    err "no se pudo resolver $REMOTE/$BRANCH tras el fetch. Se conserva el estado actual."
    exit 1
fi

if [[ "$LOCAL_REV" == "$REMOTE_REV" ]]; then
    if has_pending && [[ "$FORCE" -eq 0 ]]; then
        # No hay commits nuevos pero quedó una marca pendiente de un intento
        # anterior pospuesto; ya estamos al día, así que basta con limpiarla.
        log "Sin commits nuevos; se limpia la marca PENDIENTE previa."
        clear_pending
        exit 0
    fi
    if [[ "$FORCE" -eq 0 ]]; then
        log "Sin commits nuevos en $REMOTE/$BRANCH; no se reinician servicios (R1.3)."
        exit 0
    fi
    log "Sin commits nuevos, pero se continúa por --force."
fi

# --- 4. ¿El working tree impide el fast-forward? (R1.6) ---------------------
# Cambios sin confirmar en archivos versionados pueden bloquear el merge --ff-only
# y dejar un estado inconsistente. Si los hay, abortamos sin tocar el repo.
DIRTY="$(run_git status --porcelain --untracked-files=no 2>/dev/null || true)"
if [[ -n "$DIRTY" ]]; then
    err "el working tree tiene cambios sin confirmar que impiden el fast-forward."
    err "se aborta la actualización sin tocar el repo (R1.6). Cambios pendientes:"
    echo "$DIRTY" >&2
    exit 1
fi

# Guardamos el commit del submódulo ANTES del merge para saber si cambió (paso 6).
SUBMODULE_PATH="live-sport-plugin/live-sport-plugin"
SUB_BEFORE="$(run_git rev-parse "HEAD:$SUBMODULE_PATH" 2>/dev/null || true)"

# --- 5. git merge --ff-only origin/main (R1.2) ------------------------------
log "Aplicando cambios (fast-forward) de $REMOTE/$BRANCH ..."
if ! run_git merge --ff-only "$REMOTE/$BRANCH"; then
    err "'git merge --ff-only' falló (historia divergente o working tree sucio)."
    err "se conserva el estado anterior; no se reinician servicios (R1.6/R1.7)."
    exit 1
fi
log "Fast-forward aplicado: $LOCAL_REV -> $REMOTE_REV."

# --- 6. Actualizar el submódulo si cambió -----------------------------------
SUB_AFTER="$(run_git rev-parse "HEAD:$SUBMODULE_PATH" 2>/dev/null || true)"
if [[ -n "$SUB_AFTER" && "$SUB_BEFORE" != "$SUB_AFTER" ]]; then
    log "El submódulo del plugin cambió; sincronizando (git submodule update --init)..."
    if ! run_git submodule update --init "$SUBMODULE_PATH"; then
        err "falló 'git submodule update --init'. El código del repo YA está aplicado,"
        err "pero el submódulo del plugin puede estar desincronizado. Revísalo a mano."
        # No salimos: seguimos reiniciando servicios para tomar el código ya aplicado.
    fi
else
    log "El submódulo del plugin no cambió."
fi

# --- 7. Reiniciar servicios afectados (R1.4) --------------------------------
# Solo tras un fast-forward exitoso. Reiniciar servicios necesita systemctl como
# root; si no somos root, lo intentamos vía sudo (el dispositivo ya tiene sudo
# sin contraseña, ver setup/03-sudo-nopasswd.sh).
systemctl_do() {
    if [[ "$(id -u)" -eq 0 ]]; then
        systemctl "$@"
    else
        sudo -n systemctl "$@"
    fi
}

restart_unit() {
    local unit="$1"
    # Solo reiniciamos unidades instaladas (evita ruido si una no aplica a este
    # equipo, p. ej. kiosk.service vs kiosk-wayland.service). `list-unit-files`
    # con el nombre exacto sale != 0 si la unidad no existe.
    if ! systemctl_do list-unit-files "$unit" >/dev/null 2>&1; then
        log "$unit no está instalado; se omite."
        return 0
    fi
    log "Reiniciando $unit ..."
    if ! systemctl_do restart "$unit"; then
        err "no se pudo reiniciar $unit (continúo con el resto)."
        return 1
    fi
    return 0
}

restart_failed=0
# UI del kiosk: puede ser la unidad Xorg (kiosk.service) o la Wayland
# (kiosk-wayland.service) según cómo se haya provisionado el equipo. Reiniciamos
# la que esté instalada.
restart_unit "kiosk.service"         || restart_failed=1
restart_unit "kiosk-wayland.service" || restart_failed=1
# Servicio Node del plugin (Tarea 11).
restart_unit "plugin.service"        || restart_failed=1

# --- 8. Limpiar la marca PENDING --------------------------------------------
clear_pending

# --- 9. Resultado -----------------------------------------------------------
if [[ "$restart_failed" -eq 1 ]]; then
    err "la actualización se aplicó pero al menos un servicio no pudo reiniciarse."
    err "revisa 'systemctl status' de las unidades del kiosk/plugin."
    exit 1
fi

log "Actualización completada y servicios reiniciados."
exit 0
