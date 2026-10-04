#!/usr/bin/env bash
#
# 04-static-ip.sh — Configura un fallback de IP estática en la Raspberry Pi
# (Raspberry Pi OS Bookworm usa NetworkManager).
#
# La IP 10.10.0.246 ya está reservada por DHCP en el router; esto añade un
# fallback estático en el propio dispositivo por si el DHCP fallara, apuntando
# a la MISMA IP para no tener que buscarla nunca.
#
# Uso (en la Raspberry):
#   sudo bash setup/04-static-ip.sh [interfaz]
# Si no se indica interfaz, se detecta la conexión activa (normalmente eth0).
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

# --- Parámetros de red (ajusta si tu red cambia) ---
STATIC_IP="10.10.0.246/24"
GATEWAY="10.10.0.1"
DNS="10.10.0.210"

if ! command -v nmcli &>/dev/null; then
    echo "nmcli no está disponible. Este script asume NetworkManager (Bookworm)." >&2
    exit 1
fi

IFACE="${1:-}"
if [[ -z "$IFACE" ]]; then
    # Detecta la interfaz de la conexión activa por defecto.
    IFACE="$(nmcli -t -f DEVICE,STATE device | awk -F: '$2=="connected"{print $1; exit}')"
fi

if [[ -z "$IFACE" ]]; then
    echo "No se pudo detectar una interfaz conectada. Indícala como argumento." >&2
    exit 1
fi

# Nombre de la conexión de NetworkManager asociada a esa interfaz.
CON_NAME="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v d="$IFACE" '$2==d{print $1; exit}')"
if [[ -z "$CON_NAME" ]]; then
    echo "No se encontró una conexión activa para la interfaz '$IFACE'." >&2
    exit 1
fi

echo "==> Interfaz:  $IFACE"
echo "==> Conexión:  $CON_NAME"
echo "==> IP fija:   $STATIC_IP  (gw $GATEWAY, dns $DNS)"

# ipv4.method manual = IP estática. Mantiene la MISMA IP que la reserva DHCP.
nmcli connection modify "$CON_NAME" \
    ipv4.addresses "$STATIC_IP" \
    ipv4.gateway "$GATEWAY" \
    ipv4.dns "$DNS" \
    ipv4.method manual

echo "==> Reaplicando la conexión (solo 'up' para no cortar sesiones SSH remotas)..."
# Nota: evitamos 'connection down' a propósito. Si administras por SSH sobre esta
# misma interfaz (p. ej. Wi-Fi sin Ethernet de respaldo), un 'down' te dejaría fuera.
# 'up' reaplica la config reconectando en unos segundos sin cortar de forma permanente.
nmcli connection up "$CON_NAME"

echo
echo "==> IP estática configurada. Verifica con:  ip addr show $IFACE"
echo "    Nota: si prefieres volver a DHCP:  sudo nmcli connection modify \"$CON_NAME\" ipv4.method auto && sudo nmcli connection up \"$CON_NAME\""
