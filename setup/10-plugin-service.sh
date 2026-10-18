#!/usr/bin/env bash
#
# 10-plugin-service.sh — Instala y activa live-sport-plugin como servicio Node
# (systemd) para el kiosk NFL.
#
# El tablero NFL obtiene la jornada y resuelve streams bajo demanda a través del
# addon `live-sport-plugin` corriendo como servicio Node local en :7000. Este
# script:
#   1. Instala Node.js >= 22 si falta o es más antiguo (NodeSource setup_22.x).
#   2. Compila el plugin (npm ci + npm run build: ncc + WASM) ANTES de activar el
#      servicio, para no dejar una unidad apuntando a un dist/ inexistente.
#   3. Instala, recarga y habilita la unidad plugin.service.
#
# Uso (en la Raspberry):
#   sudo bash setup/10-plugin-service.sh [usuario]
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
PLUGIN_DIR="$PROJECT_DIR/live-sport-plugin/live-sport-plugin"

# --- 1. Comprobar que el submódulo del plugin está inicializado --------------
if [[ ! -f "$PLUGIN_DIR/package.json" ]]; then
    echo "No se encontró el plugin en: $PLUGIN_DIR" >&2
    echo "El submódulo no parece estar inicializado. Ejecuta primero:" >&2
    echo "    git -C \"$PROJECT_DIR\" submodule update --init --recursive" >&2
    echo "y vuelve a correr este script." >&2
    exit 1
fi

# --- 2. Instalar Node.js >= 22 si falta o es más antiguo ---------------------
NODE_REQUIRED_MAJOR=22
need_node_install=1

if command -v node >/dev/null 2>&1; then
    # node --version imprime algo como "v22.9.0"; extraer el major.
    node_version="$(node --version 2>/dev/null || echo "")"
    node_major="$(echo "$node_version" | sed -E 's/^v?([0-9]+).*/\1/')"
    if [[ "$node_major" =~ ^[0-9]+$ ]] && [[ "$node_major" -ge "$NODE_REQUIRED_MAJOR" ]]; then
        echo "==> Node.js $node_version ya instalado (>= $NODE_REQUIRED_MAJOR); se omite la instalación."
        need_node_install=0
    else
        echo "==> Node.js $node_version es anterior a $NODE_REQUIRED_MAJOR; se actualizará."
    fi
else
    echo "==> Node.js no está instalado; se instalará la versión $NODE_REQUIRED_MAJOR."
fi

if [[ "$need_node_install" -eq 1 ]]; then
    echo "==> Instalando Node.js $NODE_REQUIRED_MAJOR vía NodeSource (setup_22.x)..."
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_REQUIRED_MAJOR}.x" | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
    echo "==> Node.js instalado: $(node --version)"
fi

# --- 3. Compilar el plugin ANTES de activar el servicio (R3.5) ---------------
# Se corre como el usuario objetivo (no root), igual que 05-kiosk.sh con pip.
echo "==> Compilando el plugin en $PLUGIN_DIR (npm ci + npm run build)..."
if ! sudo -u "$TARGET_USER" bash -c "cd '$PLUGIN_DIR' && npm ci && npm run build"; then
    echo "ERROR: la compilación del plugin falló (npm ci / npm run build)." >&2
    echo "No se activará plugin.service para no dejar un servicio roto." >&2
    echo "Revisa el error anterior, corrígelo y vuelve a ejecutar este script." >&2
    exit 1
fi

if [[ ! -f "$PLUGIN_DIR/dist/index.js" ]]; then
    echo "ERROR: la compilación terminó pero no existe $PLUGIN_DIR/dist/index.js." >&2
    echo "No se activará plugin.service." >&2
    exit 1
fi

echo "==> Compilación del plugin completada."

# --- 4. Instalar y activar la unidad systemd --------------------------------
echo "==> Instalando el servicio del plugin (systemd)..."
sed -e "s|__USER__|$TARGET_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$SCRIPT_DIR/plugin.service" > /etc/systemd/system/plugin.service

systemctl daemon-reload
systemctl enable plugin.service
systemctl restart plugin.service

echo
echo "==> plugin.service instalado y activado (escucha en http://localhost:7000)."
echo "    Estado:   sudo systemctl status plugin.service"
echo "    Logs:     journalctl -u plugin.service -f"
