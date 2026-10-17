#!/usr/bin/env bash
#
# 09-time-sync.sh — Configura la hora de la Raspberry: zona horaria, NTP y
# fake-hwclock.
#
# ¿Por qué? La Raspberry Pi NO tiene reloj de hardware con batería (RTC). Al
# apagarse pierde la hora, y al arrancar retiene la ÚLTIMA fecha conocida hasta
# que sincroniza por red (NTP). Si arranca sin internet, puede mostrar una fecha
# vieja (p. ej. el lunes pasado un jueves), lo que rompe la agenda del día.
#
# Este script:
#   1) Fija la zona horaria (por defecto America/Mexico_City).
#   2) Habilita la sincronización NTP (systemd-timesyncd).
#   3) Instala fake-hwclock, que guarda la hora en disco periódicamente y la
#      restaura al arrancar, de modo que un arranque sin red parte de una fecha
#      razonable en vez de una antigua.
#
# Uso (en la Raspberry, o vía SSH):
#   sudo bash setup/09-time-sync.sh                      # usa America/Mexico_City
#   sudo bash setup/09-time-sync.sh Europe/Madrid        # otra zona horaria
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

TIMEZONE="${1:-America/Mexico_City}"

echo "==> Fijando zona horaria: $TIMEZONE"
timedatectl set-timezone "$TIMEZONE"

echo "==> Habilitando sincronización NTP..."
timedatectl set-ntp true

# systemd-timesyncd es el cliente NTP por defecto en Raspberry Pi OS Bookworm.
if systemctl list-unit-files systemd-timesyncd.service >/dev/null 2>&1; then
    echo "==> Habilitando y arrancando systemd-timesyncd..."
    systemctl enable --now systemd-timesyncd
fi

echo "==> Instalando fake-hwclock (respaldo de hora sin RTC)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y fake-hwclock

echo "==> Guardando la hora actual en fake-hwclock..."
fake-hwclock save

echo
echo "==> Estado del reloj:"
timedatectl

echo
echo "==> Hora configurada."
echo "    Verifica arriba que 'System clock synchronized: yes' y la zona horaria sean correctas."
echo "    Nota: la primera sincronización NTP tras arrancar requiere acceso a internet."
