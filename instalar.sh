#!/usr/bin/env bash
# Instala el panel hídrico de Córdoba. Uso:  sudo ./instalar.sh [carpeta_web] [carpeta_app]
# Por defecto: web en /var/www/html/hidrico y aplicación en /opt/hidrico-cordoba
set -euo pipefail

WEB_DIR="${1:-/var/www/html/hidrico}"
APP_DIR="${2:-/opt/hidrico-cordoba}"
USUARIO="${SUDO_USER:-$(whoami)}"
ORIGEN="$(cd "$(dirname "$0")" && pwd)"

command -v python3 >/dev/null || { echo "Falta python3"; exit 1; }

mkdir -p "$APP_DIR" "$WEB_DIR"
cp "$ORIGEN/scraper_saih.py" "$ORIGEN/alertas.py" "$APP_DIR/"
# No se pisa un config.json ya editado (contiene tokens de avisos)
[ -f "$APP_DIR/config.json" ] || cp "$ORIGEN/config.example.json" "$APP_DIR/config.json"
cp "$ORIGEN/index.html" "$WEB_DIR/index.html"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet requests beautifulsoup4 lxml

chown -R "$USUARIO" "$APP_DIR" "$WEB_DIR"
chmod 600 "$APP_DIR/config.json"

CRON=/etc/cron.d/hidrico-cordoba
cat > "$CRON" <<CRONEOF
# Panel hídrico de Córdoba: lectura cada 15 minutos y evaluación de avisos
*/15 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR $APP_DIR/venv/bin/python $APP_DIR/scraper_saih.py && HIDRICO_SALIDA=$WEB_DIR $APP_DIR/venv/bin/python $APP_DIR/alertas.py
CRONEOF
chmod 644 "$CRON"

echo "Primera lectura..."
sudo -u "$USUARIO" env HIDRICO_SALIDA="$WEB_DIR" "$APP_DIR/venv/bin/python" "$APP_DIR/scraper_saih.py" \
  && echo "Correcto. Datos en $WEB_DIR/datos.json" \
  || echo "La primera lectura falló. Revisa $APP_DIR/scraper.log"
echo "Panel publicado en $WEB_DIR. Abre index.html desde el servidor web."
