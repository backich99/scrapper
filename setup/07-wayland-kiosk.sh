#!/usr/bin/env bash
#
# 07-wayland-kiosk.sh — Configura el kiosk sobre Wayland (labwc) para dual-HDMI
# independiente en la Raspberry Pi 4 (Raspberry Pi OS Bookworm/trixie).
#
# Sustituye el enfoque X11 (startx) que clonaba las salidas. Con labwc/wlroots,
# HDMI0 (7") y HDMI1 (TV) son pantallas independientes.
#
# Uso (en la Raspberry):
#   sudo bash setup/07-wayland-kiosk.sh [usuario]
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

TARGET_USER="${1:-karlos}"
USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
USER_UID="$(id -u "$TARGET_USER")"
if [[ -z "$USER_HOME" || ! -d "$USER_HOME" ]]; then
    echo "No se encontró el home del usuario '$TARGET_USER'." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$USER_HOME/scrapper-venv"

echo "==> Instalando componentes Wayland (labwc, wlr-randr, kanshi)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    labwc wlr-randr kanshi

echo "==> Escribiendo configuración de labwc y kanshi..."
install -d -o "$TARGET_USER" -g "$TARGET_USER" \
    "$USER_HOME/.config/labwc" "$USER_HOME/.config/kanshi"

# autostart con placeholders sustituidos
sed -e "s|__VENV__|$VENV_DIR|g" -e "s|__PROJECT__|$PROJECT_DIR|g" \
    "$SCRIPT_DIR/wayland/labwc/autostart" > "$USER_HOME/.config/labwc/autostart"
chmod +x "$USER_HOME/.config/labwc/autostart"
install -m 0644 "$SCRIPT_DIR/wayland/labwc/environment" "$USER_HOME/.config/labwc/environment"
install -m 0644 "$SCRIPT_DIR/wayland/labwc/rc.xml" "$USER_HOME/.config/labwc/rc.xml"
install -m 0644 "$SCRIPT_DIR/wayland/kanshi/config" "$USER_HOME/.config/kanshi/config"
chown -R "$TARGET_USER":"$TARGET_USER" "$USER_HOME/.config/labwc" "$USER_HOME/.config/kanshi"

echo "==> Instalando servicio kiosk (Wayland) y deshabilitando el X11 anterior..."
# Desactivar el kiosk X11 antiguo si existe.
systemctl disable --now kiosk.service 2>/dev/null || true

sed -e "s|__USER__|$TARGET_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__UID__|$USER_UID|g" \
    "$SCRIPT_DIR/kiosk-wayland.service" > /etc/systemd/system/kiosk.service

systemctl daemon-reload
systemctl enable kiosk.service

echo
echo "==> Kiosk Wayland instalado."
echo "    Arrancar ahora:  sudo systemctl start kiosk.service"
echo "    Ver outputs:     (en la sesión) wlr-randr"
