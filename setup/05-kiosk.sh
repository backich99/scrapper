#!/usr/bin/env bash
#
# 05-kiosk.sh — Instala el entorno mínimo (Xorg reducido), el reproductor mpv,
# las dependencias de Python y deja la UI Kivy arrancando en modo kiosk al encender.
#
# Pensado para Raspberry Pi OS Lite (Bookworm): instala solo lo imprescindible
# para lanzar la interfaz a pantalla completa, sin escritorio.
#
# Uso (en la Raspberry):
#   sudo bash setup/05-kiosk.sh [usuario]
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
VENV_DIR="$USER_HOME/scrapper-venv"

echo "==> Instalando entorno mínimo (Xorg), mpv y Python..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xserver-xorg xinit x11-xserver-utils \
    mpv \
    python3 python3-pip python3-venv

echo "==> Creando entorno virtual de Python en $VENV_DIR ..."
sudo -u "$TARGET_USER" python3 -m venv "$VENV_DIR"

if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
    echo "==> Instalando dependencias de requirements.txt ..."
    sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install --upgrade pip
    sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
    echo "==> Instalando navegador de Playwright (chromium) ..."
    sudo -u "$TARGET_USER" "$VENV_DIR/bin/python" -m playwright install chromium || \
        echo "    (Aviso: 'playwright install' falló o no está en requirements; continúo)"
else
    echo "    (Aviso: no existe requirements.txt todavía; se omite la instalación de dependencias)"
fi

# Script de arranque de la UI bajo xinit (sin gestor de ventanas).
XINITRC="$USER_HOME/.xinitrc"
echo "==> Escribiendo $XINITRC ..."
cat > "$XINITRC" <<EOF
#!/bin/sh
# Arranque de la UI Kivy en modo kiosk (generado por setup/05-kiosk.sh).
xset s off        # sin salvapantallas
xset -dpms        # sin apagado de energía de pantalla
xset s noblank    # sin blanking
exec "$VENV_DIR/bin/python" -m ui.app
EOF
chown "$TARGET_USER":"$TARGET_USER" "$XINITRC"
chmod 0644 "$XINITRC"

echo "==> Instalando el servicio de kiosk (systemd)..."
# Se genera un service que ejecuta startx como el usuario en la consola tty1.
sed -e "s|__USER__|$TARGET_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$SCRIPT_DIR/kiosk.service" > /etc/systemd/system/kiosk.service

systemctl daemon-reload
systemctl enable kiosk.service

echo
echo "==> Kiosk instalado. Al reiniciar, la Raspberry arrancará directa en la UI (HDMI0)."
echo "    Iniciar ahora sin reiniciar:  sudo systemctl start kiosk.service"
