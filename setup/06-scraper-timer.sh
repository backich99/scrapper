#!/usr/bin/env bash
#
# 06-scraper-timer.sh — Desactiva el scraper horario (modelo antiguo).
#
# NUEVO MODELO: el tablero obtiene la agenda de partidos de la API de ESPN y
# scrapea StreamEast SOLO cuando el usuario pulsa una tarjeta (on-demand). Ya no
# se necesita el scraper de fondo cada hora, así que este script ahora DESACTIVA
# y elimina scrapper.timer/scrapper.service si estaban instalados.
#
# Uso (en la Raspberry):
#   sudo bash setup/06-scraper-timer.sh
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (usa: sudo bash $0)" >&2
    exit 1
fi

echo "==> Desactivando el scraper horario (modelo antiguo)..."
systemctl disable --now scrapper.timer 2>/dev/null || true
systemctl stop scrapper.service 2>/dev/null || true

removed=0
for unit in /etc/systemd/system/scrapper.timer /etc/systemd/system/scrapper.service; do
    if [[ -f "$unit" ]]; then
        rm -f "$unit"
        removed=1
        echo "    eliminado: $unit"
    fi
done

systemctl daemon-reload

if [[ "$removed" -eq 1 ]]; then
    echo "==> Scraper horario eliminado. El tablero ahora resuelve el stream al pulsar."
else
    echo "==> No había timer/servicio instalados; nada que quitar."
fi
