#!/usr/bin/env bash
#
# 02-ssh-key.sh — Habilita el servidor SSH en la Raspberry y (opcionalmente)
# endurece la configuración para exigir acceso por clave.
#
# El COPIADO de la clave se hace DESDE TU EQUIPO, no aquí. Ver instrucciones abajo.
#
# Uso (en la Raspberry):
#   sudo bash setup/02-ssh-key.sh              # habilita SSH
#   sudo bash setup/02-ssh-key.sh --harden     # además, deshabilita login por contraseña
#
# IMPORTANTE: usa --harden SOLO cuando ya hayas confirmado que entras por clave,
# de lo contrario podrías quedarte fuera.
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

HARDEN="no"
if [[ "${1:-}" == "--harden" ]]; then
    HARDEN="yes"
fi

echo "==> Habilitando y arrancando el servicio SSH..."
systemctl enable --now ssh

if [[ "$HARDEN" == "yes" ]]; then
    SSHD_CONFIG="/etc/ssh/sshd_config.d/10-scrapper-hardening.conf"
    echo "==> Endureciendo SSH en $SSHD_CONFIG (solo clave pública)..."
    cat > "$SSHD_CONFIG" <<'EOF'
# Gestionado por setup/02-ssh-key.sh — acceso solo por clave.
PasswordAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
EOF
    chmod 0644 "$SSHD_CONFIG"

    echo "==> Validando configuración de sshd..."
    sshd -t

    echo "==> Reiniciando SSH..."
    systemctl restart ssh
    echo "    Login por contraseña DESHABILITADO. Asegúrate de tener tu clave instalada."
else
    echo "==> SSH habilitado (login por contraseña aún permitido)."
fi

cat <<'INSTR'

------------------------------------------------------------------
PASOS A REALIZAR DESDE TU EQUIPO (no en la Raspberry):

  # 1) Genera una clave si aún no tienes una
  ssh-keygen -t ed25519 -C "scrapper-admin"

  # 2) Copia tu clave pública a la Raspberry (pedirá la contraseña una vez)
  ssh-copy-id karlos@10.10.0.246

  # 3) Añade un alias cómodo en ~/.ssh/config:
  #   Host tvbox
  #       HostName 10.10.0.246
  #       User karlos
  #       IdentityFile ~/.ssh/id_ed25519

  # 4) Prueba:
  ssh tvbox

Cuando confirmes que entras por clave, vuelve a ejecutar en la Raspberry:
  sudo bash setup/02-ssh-key.sh --harden
------------------------------------------------------------------
INSTR
