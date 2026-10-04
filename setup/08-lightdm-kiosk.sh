#!/usr/bin/env bash
#
# 08-lightdm-kiosk.sh — Configura el kiosk vía autologin de LightDM con sesión labwc.
#
# En Raspberry Pi OS (Bookworm/trixie) el arranque gráfico lo gestiona LightDM
# sobre graphical.target. La forma robusta de un kiosk Wayland es autologin del
# usuario en la sesión labwc (obtiene seat0 vía logind), y que el autostart de
# labwc lance la UI. Evita pelear por el "seat" con un servicio systemd manual.
#
# Uso (en la Raspberry):
#   sudo bash setup/08-lightdm-kiosk.sh [usuario]
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

TARGET_USER="${1:-karlos}"
if ! id "$TARGET_USER" &>/dev/null; then
    echo "El usuario '$TARGET_USER' no existe." >&2
    exit 1
fi

# 1) Desactivar el servicio kiosk manual (X11 o Wayland) si quedó de intentos previos.
systemctl disable --now kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/kiosk.service
systemctl daemon-reload

# 2) Asegurar arranque en modo gráfico (LightDM).
systemctl set-default graphical.target

# 3) Configurar autologin de LightDM con la sesión labwc.
CONF_DIR="/etc/lightdm/lightdm.conf.d"
install -d "$CONF_DIR"
cat > "$CONF_DIR/60-scrapper-kiosk.conf" <<EOF
[Seat:*]
autologin-user=$TARGET_USER
autologin-user-timeout=0
autologin-session=labwc
user-session=labwc
EOF
chmod 0644 "$CONF_DIR/60-scrapper-kiosk.conf"

echo
echo "==> LightDM configurado para autologin de '$TARGET_USER' en sesión labwc."
echo "    El autostart de labwc (~/.config/labwc/autostart) lanzará la UI."
echo "    Aplica reiniciando:  sudo systemctl restart lightdm   (o reboot)"
