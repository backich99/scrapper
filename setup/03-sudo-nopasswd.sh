#!/usr/bin/env bash
#
# 03-sudo-nopasswd.sh — Permite que el usuario administrador use sudo sin contraseña.
#
# ATENCIÓN: reduce la seguridad. Úsalo solo en una red de confianza y siendo
# consciente de la implicación. Cómodo para un dispositivo dedicado en LAN.
#
# Uso (en la Raspberry):
#   sudo bash setup/03-sudo-nopasswd.sh [usuario]
# Si no se indica usuario, se usa "karlos".
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

SUDOERS_FILE="/etc/sudoers.d/010_${TARGET_USER}-nopasswd"

echo "==> Creando $SUDOERS_FILE ..."
# Escritura atómica + validación antes de instalar el fichero definitivo.
TMP_FILE="$(mktemp)"
echo "${TARGET_USER} ALL=(ALL) NOPASSWD:ALL" > "$TMP_FILE"

# Valida la sintaxis del fichero temporal antes de moverlo.
if visudo -cf "$TMP_FILE"; then
    install -m 0440 -o root -g root "$TMP_FILE" "$SUDOERS_FILE"
    rm -f "$TMP_FILE"
else
    rm -f "$TMP_FILE"
    echo "Sintaxis de sudoers inválida. Abortando." >&2
    exit 1
fi

echo "==> Verificando configuración global de sudoers..."
visudo -c

echo
echo "==> Listo: '$TARGET_USER' puede usar sudo sin contraseña."
