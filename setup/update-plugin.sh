#!/usr/bin/env bash
#
# update-plugin.sh — Actualización CONTROLADA del plugin desde upstream (R2).
#
# Trae los arreglos de providers del plugin de terceros
# (`upstream` = rajhodedara/live-sport-plugin) hacia el submódulo del fork
# (`origin` = backich99/live-sport-plugin), reconstruye el plugin (incluido el
# WASM) y reinicia su servicio para que tome el código nuevo. Deja el bump del
# submódulo registrado en el repo raíz como un cambio versionado y deliberado.
#
# ACTO MANUAL Y DELIBERADO (R2.3): este script NO se invoca desde el timer
# horario (Tarea 12.2 / scrapper-update.service). Actualizar el plugin es una
# decisión del administrador, no un pull ciego. Por eso no se instala ninguna
# unidad de timer para él: se corre a mano cuando se quiere traer upstream.
#
#   sudo bash setup/update-plugin.sh [usuario]
#
# Regla de oro (R2.5 / R10.3): NUNCA interrumpe un partido. Si hay una
# reproducción activa (lock presente y proceso mpv vivo), marca una PENDIENTE
# PROPIA del plugin y sale 0 sin tocar nada. No hay auto-aplicación al pulsar
# Detener para esta vía (la Tarea 12.3 dispara la actualización del REPO, no
# ésta): la marca sólo deja constancia de que quedó pendiente; el administrador
# vuelve a ejecutar el script cuando no haya reproducción en curso.
#
# Marca PENDING propia: usamos `/run/scrapper/update-plugin.pending`, distinta de
# la del auto-update del repo (`/run/scrapper/update.pending`), para que las dos
# vías de actualización no se pisen entre sí.
#
# Robustez: `set -uo pipefail` SIN `-e` (igual que update-repo.sh). Varios pasos
# devuelven códigos != 0 de forma esperada y se manejan explícitamente; abortar a
# medias podría dejar el plugin a medio actualizar. Nunca se usan operaciones
# destructivas de git (merge --ff-only, sin reset --hard, sin push --force).
#
# Lógica (diseño §10):
#   1. si playback.lock activo -> marcar PENDING plugin, salir 0      (R2.5, R10.3)
#   2. git -C <submodule> fetch upstream                             (R2.1)
#   3. git -C <submodule> merge --ff-only upstream/main              (R2.1)
#        (ff-only por seguridad; un rebase controlado sería la alternativa)
#   4. git -C <submodule> push origin main                          (R2.2, fork)
#   5. npm ci && npm run build  (rebuild WASM ANTES de reiniciar)    (R2.4)
#   6. en el repo raíz: git add <submodule> && commit del bump       (R2.2)
#   7. systemctl restart plugin.service
#   8. limpiar la marca PENDING del plugin
#
# Códigos de salida:
#   0  éxito, o nada que hacer, o pospuesto por reproducción activa.
#   >0 fallo (red/git/build/servicio); se conserva el build anterior en ejecución.
#
set -uo pipefail
# OJO: no usamos `-e`. Igual que update-repo.sh, controlamos los errores de forma
# explícita para no dejar el plugin a medio actualizar ni reiniciar su servicio
# sobre un build fallido.

# --- Mensajería -------------------------------------------------------------
# Formato "==>" consistente con el resto de scripts de setup/.
log()  { echo "==> $*"; }
warn() { echo "AVISO: $*" >&2; }
err()  { echo "ERROR: $*" >&2; }

# --- Parseo de argumentos ---------------------------------------------------
TARGET_USER=""
for arg in "$@"; do
    case "$arg" in
        --*) err "opción desconocida: $arg"; exit 2 ;;
        *)   TARGET_USER="$arg" ;;
    esac
done

# --- Rutas y contexto -------------------------------------------------------
# Raíz del proyecto = carpeta padre de este script (setup/). Resolvemos el
# enlace simbólico por si se invoca a través de uno, para que PROJECT_DIR sea
# siempre el repo real.
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Ruta del submódulo del plugin (relativa a la raíz del repo).
SUBMODULE_PATH="live-sport-plugin/live-sport-plugin"
SUBMODULE_DIR="$PROJECT_DIR/$SUBMODULE_PATH"

# Remotos y rama del submódulo (ver requirements §Decisiones):
#   origin   = fork backich99/live-sport-plugin
#   upstream = rajhodedara/live-sport-plugin
UPSTREAM="upstream"
ORIGIN="origin"
BRANCH="main"

# Marca de "plugin pendiente". Vive en /run (tmpfs), igual que el lock de
# reproducción; se limpia sola en cada arranque. DISTINTA de la del repo
# (update.pending) para que ambas vías no se pisen.
PENDING_DIR="/run/scrapper"
PENDING_FILE="$PENDING_DIR/update-plugin.pending"

# Dueño del repo: por defecto, el propietario del .git (así git/npm corren con
# los permisos correctos aunque el script se ejecute como root vía sudo). Un
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

# Ejecuta git en el SUBMÓDULO como el dueño del repo cuando corremos como root;
# si ya somos ese usuario (o no somos root), ejecuta directo. Evita "dubious
# ownership" y objetos propiedad de root.
run_git_sub() {
    if [[ "$(id -u)" -eq 0 && "$TARGET_USER" != "root" ]]; then
        sudo -u "$TARGET_USER" git -C "$SUBMODULE_DIR" "$@"
    else
        git -C "$SUBMODULE_DIR" "$@"
    fi
}

# Igual, pero sobre el repo raíz (para el bump del submódulo, paso 6).
run_git_root() {
    if [[ "$(id -u)" -eq 0 && "$TARGET_USER" != "root" ]]; then
        sudo -u "$TARGET_USER" git -C "$PROJECT_DIR" "$@"
    else
        git -C "$PROJECT_DIR" "$@"
    fi
}

# Ejecuta npm en el submódulo como el dueño del repo cuando corremos como root.
run_npm_sub() {
    if [[ "$(id -u)" -eq 0 && "$TARGET_USER" != "root" ]]; then
        sudo -u "$TARGET_USER" npm --prefix "$SUBMODULE_DIR" "$@"
    else
        ( cd "$SUBMODULE_DIR" && npm "$@" )
    fi
}

# Reinicio de servicios: necesita systemctl como root; si no somos root, lo
# intentamos vía sudo (el dispositivo ya tiene sudo sin contraseña, ver
# setup/03-sudo-nopasswd.sh).
systemctl_do() {
    if [[ "$(id -u)" -eq 0 ]]; then
        systemctl "$@"
    else
        sudo -n systemctl "$@"
    fi
}

# --- Marca PENDING ----------------------------------------------------------
mark_pending() {
    mkdir -p "$PENDING_DIR" 2>/dev/null || true
    : > "$PENDING_FILE" 2>/dev/null \
        && log "Actualización del plugin marcada como PENDIENTE en $PENDING_FILE." \
        || warn "no se pudo escribir la marca PENDING en $PENDING_FILE."
}

clear_pending() {
    if [[ -e "$PENDING_FILE" ]]; then
        rm -f "$PENDING_FILE" 2>/dev/null \
            && log "Marca PENDING del plugin limpiada." \
            || warn "no se pudo limpiar la marca PENDING $PENDING_FILE."
    fi
}

# --- Comprobaciones previas -------------------------------------------------
if [[ ! -d "$SUBMODULE_DIR/.git" && ! -f "$SUBMODULE_DIR/.git" ]]; then
    err "no se encontró el submódulo del plugin en $SUBMODULE_DIR."
    err "inicialízalo primero: git submodule update --init '$SUBMODULE_PATH'."
    exit 1
fi

# --- 1. ¿Hay reproducción activa? (R2.5 / R10.3) ----------------------------
# El helper de la Tarea 10.2 sale 0 si hay reproducción activa (lock + PID vivo)
# y 1 si no (limpiando de paso un lock obsoleto). Misma regla que update-repo.sh:
# si hay partido, no se reconstruye ni se reinicia nada.
if [[ -n "$PYTHON" ]]; then
    if ( cd "$PROJECT_DIR" && "$PYTHON" -m scraper.playback_lock ); then
        log "Reproducción activa en curso: se pospone la actualización del plugin."
        mark_pending
        exit 0
    fi
else
    warn "no se encontró intérprete de Python; no se pudo comprobar el lock de reproducción."
    warn "por seguridad, se pospone la actualización del plugin (se marca PENDING)."
    mark_pending
    exit 0
fi

# --- 2. git -C <submodule> fetch upstream (R2.1) ----------------------------
log "Buscando cambios del plugin en $UPSTREAM/$BRANCH ..."
if ! run_git_sub fetch "$UPSTREAM" "$BRANCH"; then
    err "falló 'git fetch $UPSTREAM $BRANCH' en el submódulo (¿sin red o remoto '$UPSTREAM' no configurado?)."
    err "se conserva el build actual del plugin; no se reinicia nada."
    exit 1
fi

# Commit del submódulo ANTES del merge: así sabemos si realmente cambió y si hace
# falta reconstruir/registrar el bump (R2.4 sólo exige rebuild si el commit cambia).
SUB_BEFORE="$(run_git_sub rev-parse HEAD 2>/dev/null || true)"
UPSTREAM_REV="$(run_git_sub rev-parse "$UPSTREAM/$BRANCH" 2>/dev/null || true)"

if [[ -z "$UPSTREAM_REV" ]]; then
    err "no se pudo resolver $UPSTREAM/$BRANCH tras el fetch. Se conserva el estado actual."
    exit 1
fi

if [[ -n "$SUB_BEFORE" && "$SUB_BEFORE" == "$UPSTREAM_REV" ]]; then
    log "El plugin ya está al día con $UPSTREAM/$BRANCH; no hay nada que integrar."
    clear_pending
    exit 0
fi

# --- 3. git merge --ff-only upstream/main (R2.1) ----------------------------
# ff-only por seguridad: si la historia del fork divergió del upstream, abortamos
# sin dejar el submódulo a medias (ni merges de conflicto a resolver a ciegas).
# La alternativa documentada sería un rebase controlado del fork sobre upstream,
# pero eso reescribe historia y exige revisión manual; preferimos fallar claro.
log "Integrando (fast-forward) $UPSTREAM/$BRANCH en el submódulo ..."
if ! run_git_sub merge --ff-only "$UPSTREAM/$BRANCH"; then
    err "'git merge --ff-only $UPSTREAM/$BRANCH' falló (historia divergente del fork)."
    err "se aborta sin reconstruir ni reiniciar; resuélvelo a mano (p. ej. rebase controlado)."
    exit 1
fi
SUB_AFTER="$(run_git_sub rev-parse HEAD 2>/dev/null || true)"
log "Plugin integrado: $SUB_BEFORE -> $SUB_AFTER."

# --- 4. git push origin main (actualizar el fork) (R2.2) --------------------
# Mantiene backich99/live-sport-plugin al día con lo integrado. Si falla (sin red
# o sin credenciales), avisamos pero CONTINUAMOS: el objetivo primario es que el
# dispositivo corra el código nuevo (build + restart locales). El push al fork se
# reintenta a mano después; no bloquea el rebuild local.
log "Empujando el fork ($ORIGIN/$BRANCH) ..."
if ! run_git_sub push "$ORIGIN" "$BRANCH"; then
    warn "no se pudo empujar a $ORIGIN/$BRANCH (¿sin red/credenciales?)."
    warn "se continúa con el rebuild/restart local; reintenta el push al fork más tarde."
fi

# --- 5. npm ci && npm run build (rebuild WASM ANTES de reiniciar) (R2.4) -----
# El build (ncc + WASM) DEBE completarse antes de reiniciar el servicio. Si falla,
# abortamos SIN reiniciar plugin.service: el servicio sigue corriendo el build
# anterior, que es sano (R3.5: nunca activar un build a medias).
log "Reinstalando dependencias del plugin (npm ci) ..."
if ! run_npm_sub ci; then
    err "'npm ci' falló en el submódulo. Se aborta sin reiniciar; se conserva el build anterior."
    exit 1
fi
log "Reconstruyendo el plugin (npm run build, incluye WASM) ..."
if ! run_npm_sub run build; then
    err "'npm run build' falló en el submódulo. Se aborta sin reiniciar; se conserva el build anterior."
    exit 1
fi

# --- 6. Repo raíz: git add <submodule> + commit del bump (R2.2) -------------
# Registrar el nuevo commit del submódulo como un cambio versionado y deliberado.
# Sólo si el puntero del submódulo cambió realmente respecto a lo que el repo
# tiene registrado (evita commits vacíos).
log "Registrando el bump del submódulo en el repo raíz ..."
if ! run_git_root add -- "$SUBMODULE_PATH"; then
    warn "no se pudo 'git add $SUBMODULE_PATH' en el repo raíz; el plugin ya está actualizado localmente."
    warn "registra el bump a mano para versionarlo."
else
    # ¿Hay algo staged para el submódulo? --cached + --quiet sale 1 si hay cambios.
    if run_git_root diff --cached --quiet -- "$SUBMODULE_PATH"; then
        log "El puntero del submódulo en el repo raíz no cambió; no hay bump que commitear."
    else
        if run_git_root commit -m "chore: bump plugin submodule" -- "$SUBMODULE_PATH"; then
            log "Bump del submódulo commiteado en el repo raíz."
        else
            warn "no se pudo commitear el bump del submódulo; el plugin ya está actualizado localmente."
            warn "revísalo y commitea a mano para versionar el cambio."
        fi
    fi
fi

# --- 7. systemctl restart plugin.service ------------------------------------
# Sólo tras un build exitoso. Si la unidad no está instalada, avisamos (el nuevo
# build quedará activo al próximo arranque del servicio).
if ! systemctl_do list-unit-files "plugin.service" >/dev/null 2>&1; then
    warn "plugin.service no está instalado; se omite el reinicio."
    warn "el nuevo build se usará cuando se instale/arranque el servicio (Tarea 11)."
else
    log "Reiniciando plugin.service ..."
    if ! systemctl_do restart "plugin.service"; then
        err "no se pudo reiniciar plugin.service. El build nuevo está listo pero el servicio no se reinició."
        err "revisa 'systemctl status plugin.service' y reinícialo a mano."
        exit 1
    fi
fi

# --- 8. Limpiar la marca PENDING del plugin ---------------------------------
clear_pending

log "Actualización del plugin completada."
exit 0
