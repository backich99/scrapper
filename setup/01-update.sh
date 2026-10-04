#!/usr/bin/env bash
#
# 01-update.sh — Actualiza el sistema de la Raspberry Pi (Raspberry Pi OS Bookworm).
#
# Uso (en la Raspberry, o vía SSH):
#   sudo bash setup/01-update.sh
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

echo "==> Actualizando índices de paquetes..."
apt-get update

echo "==> Aplicando actualizaciones (full-upgrade)..."
DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y

echo "==> Eliminando paquetes innecesarios..."
apt-get autoremove -y
apt-get clean

echo
echo "==> Sistema actualizado."
echo "    Se recomienda reiniciar:  sudo reboot"
