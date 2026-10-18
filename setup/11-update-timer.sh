#!/usr/bin/env bash
#
# 11-update-timer.sh — Instala y activa la auto-update horaria del repo propio.
#
# El kiosk se mantiene al día tirando (pull) de `backich99/scrapper` cada hora.
# Este script deja operativo ese mecanismo (Tarea 12.2):
#   1. Hace ejecutables update-repo.sh y update-now (el flujo y su envoltorio).
#   2. Instala la unidad oneshot scrapper-update.service (sustituyendo el
#      placeholder __PROJECT_DIR__) y el timer scrapper-update.timer.
#   3. Enlaza `update-now` en el PATH (/usr/local/bin) para forzar la
#      actualización a mano (R1.5): `update-now` / `update-now --force`.
#   4. Recarga systemd y habilita+arranca el timer (enable --now).
#
# La actualización NUNCA interrumpe un partido: si hay reproducción activa queda
# pendiente y se aplica al pulsar Detener o en el siguiente tick (ver
# update-repo.sh). El servicio corre como root para poder reiniciar las unidades
# del kiosk y del plugin tras aplicar cambios (R1.4).
#
# Uso (en la Raspberry):
#   sudo bash setup/11-update-timer.sh [usuario]
# Si no se indica usuario, se usa "karlos".
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

TARGET_USER="${1:-karlos}"
USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

if [[ -z "$USER_HOME" || ! -d "$USER_HOME" ]]; then
    echo "No se encontró el home del usuario '$TARGET_USER'." >&2
    exit 1
fi

# Raíz del proyecto = carpeta padre de este script (setup/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

UPDATE_SCRIPT="$SCRIPT_DIR/update-repo.sh"
UPDATE_NOW="$SCRIPT_DIR/update-now"

# --- 1. Comprobar que el script de actualización (Tarea 12.1) está presente --
if [[ ! -f "$UPDATE_SCRIPT" ]]; then
    echo "No se encontró el script de actualización: $UPDATE_SCRIPT" >&2
    echo "Debe existir (Tarea 12.1) antes de instalar el timer." >&2
    exit 1
fi

# --- 2. Hacer ejecutables el flujo y su envoltorio manual --------------------
echo "==> Marcando como ejecutables update-repo.sh y update-now ..."
chmod +x "$UPDATE_SCRIPT"
[[ -f "$UPDATE_NOW" ]] && chmod +x "$UPDATE_NOW"

# --- 3. Instalar la unidad del servicio (sustituyendo el placeholder) --------
echo "==> Instalando scrapper-update.service (systemd)..."
sed -e "s|__USER__|$TARGET_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$SCRIPT_DIR/scrapper-update.service" > /etc/systemd/system/scrapper-update.service

echo "==> Instalando scrapper-update.timer (systemd)..."
cp "$SCRIPT_DIR/scrapper-update.timer" /etc/systemd/system/scrapper-update.timer

# --- 4. Enlazar `update-now` en el PATH para la invocación manual (R1.5) -----
if [[ -f "$UPDATE_NOW" ]]; then
    echo "==> Enlazando 'update-now' en /usr/local/bin ..."
    ln -sf "$UPDATE_NOW" /usr/local/bin/update-now
else
    echo "    (Aviso: no existe $UPDATE_NOW; se omite el enlace 'update-now')"
fi

# --- 5. Recargar systemd y activar el timer ---------------------------------
echo "==> Recargando systemd y activando el timer ..."
systemctl daemon-reload
systemctl enable --now scrapper-update.timer

echo
echo "==> Auto-update instalada: el repo se actualizará cada hora (Persistent=true)."
echo "    Próximos disparos:  systemctl list-timers scrapper-update.timer"
echo "    Logs del update:    journalctl -u scrapper-update.service -f"
echo "    Forzar a mano:      update-now   (o: update-now --force)"
