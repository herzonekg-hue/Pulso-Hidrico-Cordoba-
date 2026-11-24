#!/usr/bin/env bash
# AQUASALUD NORTE — instalación en servidor Linux.
# Uso:  sudo ./instalar.sh [carpeta_web] [carpeta_app]
# Por defecto: web en /var/www/html/aquasalud  y  app en /opt/aquasalud-norte
set -euo pipefail

WEB_DIR="${1:-/var/www/html/aquasalud}"
APP_DIR="${2:-/opt/aquasalud-norte}"
USUARIO="${SUDO_USER:-$(whoami)}"
ORIGEN="$(cd "$(dirname "$0")" && pwd)"

echo "=== AQUASALUD NORTE — instalación ==="
echo "  Carpeta web: $WEB_DIR"
echo "  Carpeta app: $APP_DIR"
echo "  Usuario:     $USUARIO"
echo ""

# ── Prerequisitos ──────────────────────────────────────────────────────────────
command -v python3 >/dev/null || { echo "ERROR: falta python3"; exit 1; }
python3 -c "import sys; assert sys.version_info >= (3,9), 'Python 3.9+ requerido'" \
  || { echo "ERROR: Python 3.9 o superior requerido"; exit 1; }

# ── Directorios ────────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR" "$WEB_DIR/data"

# ── Copiar scripts ─────────────────────────────────────────────────────────────
cp "$ORIGEN/scraper_saih.py"      "$APP_DIR/"
cp "$ORIGEN/scraper_sinac.py"     "$APP_DIR/"
cp "$ORIGEN/scraper_aemet.py"     "$APP_DIR/"
cp "$ORIGEN/scraper_sira.py"      "$APP_DIR/"
cp "$ORIGEN/scraper_sentinel2.py" "$APP_DIR/"
cp "$ORIGEN/scraper_embalses.py"  "$APP_DIR/"
cp "$ORIGEN/alertas.py"           "$APP_DIR/"

# ── Copiar panel web ───────────────────────────────────────────────────────────
cp "$ORIGEN/index.html" "$WEB_DIR/index.html"
cp "$ORIGEN/embalses_cordoba.html" "$WEB_DIR/embalses_cordoba.html"
mkdir -p "$WEB_DIR/data"
cp -n "$ORIGEN"/data/embalses_serie_prov.json "$ORIGEN"/data/eaa_EAA421_serie.json "$WEB_DIR/data/" 2>/dev/null || true

# Esqueleto vacío de SINAC (27 municipios en GRIS) para que el panel arranque
if [ -f "$ORIGEN/data/sinac_calidad.json" ] && [ ! -f "$WEB_DIR/data/sinac_calidad.json" ]; then
  cp "$ORIGEN/data/sinac_calidad.json" "$WEB_DIR/data/sinac_calidad.json"
fi

# ── Configuración ──────────────────────────────────────────────────────────────
# Nunca se pisa un config.json ya editado (contiene claves y tokens)
if [ ! -f "$APP_DIR/config.json" ]; then
  cp "$ORIGEN/config.example.json" "$APP_DIR/config.json"
  echo "⚠  Creado config.json — EDÍTALO antes de que los scrapers funcionen."
  echo "   Claves necesarias: aemet_api_key, ntfy_topic/correo, sentinel2_client_id/secret"
else
  echo "   config.json ya existe — no se sobreescribe."
fi
chmod 600 "$APP_DIR/config.json"

# ── Entorno virtual Python ─────────────────────────────────────────────────────
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet requests beautifulsoup4 lxml

# ── Cron ───────────────────────────────────────────────────────────────────────
CRON=/etc/cron.d/aquasalud-norte
PYTHON="$APP_DIR/venv/bin/python"
CONF="$APP_DIR/config.json"

cat > "$CRON" <<CRONEOF
# AQUASALUD NORTE — actualización automática de datos
# Cada scraper escribe en $WEB_DIR/data/ y los datos los sirve el servidor web.

# SAIH + AEMET + alertas: cada 15 minutos
*/15 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_saih.py >> $APP_DIR/saih.log 2>&1
*/15 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_aemet.py >> $APP_DIR/aemet.log 2>&1
*/15 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/alertas.py >> $APP_DIR/alertas.log 2>&1

# SINAC (calidad agua consumo): dos veces al día — 06:00 y 18:00
0 6,18 * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_sinac.py >> $APP_DIR/sinac.log 2>&1

# Sentinel-2 (teledetección La Colada): a las 08:00 — máximo una imagen cada 5 días
0 8 * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_sentinel2.py >> $APP_DIR/sentinel2.log 2>&1

# SIRA Guadiana (La Colada E2-40 y sonda EAA-421): cada 10 minutos
*/10 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_sira.py >> $APP_DIR/sira.log 2>&1

# Embalses de Córdoba (SAIH Guadalquivir + La Colada): cada 10 minutos
*/10 * * * * $USUARIO HIDRICO_SALIDA=$WEB_DIR/data HIDRICO_CONFIG=$CONF $PYTHON $APP_DIR/scraper_embalses.py >> $APP_DIR/embalses.log 2>&1

# Actualización automática diaria (03:30) desde la rama produccion
30 3 * * * $USUARIO cd $ORIGEN && ./actualizar.sh
CRONEOF
chmod 644 "$CRON"

# ── Permisos ───────────────────────────────────────────────────────────────────
chown -R "$USUARIO" "$APP_DIR" "$WEB_DIR"

# ── Primera lectura ────────────────────────────────────────────────────────────
echo ""
echo "Lanzando primera lectura SAIH..."
if sudo -u "$USUARIO" env \
     HIDRICO_SALIDA="$WEB_DIR/data" \
     HIDRICO_CONFIG="$APP_DIR/config.json" \
     "$PYTHON" "$APP_DIR/scraper_saih.py"; then
  echo "✓ SAIH OK — datos en $WEB_DIR/data/datos.json"
else
  echo "✗ La primera lectura SAIH falló. Revisa $APP_DIR/saih.log"
fi

echo ""
echo "=== Instalación completada ==="
echo ""
echo "Pasos siguientes:"
echo "  1. Edita $APP_DIR/config.json con las claves reales"
echo "  2. Reinicia el cron:  sudo systemctl restart cron"
echo "  3. Comprueba los logs en $APP_DIR/*.log"
echo "  4. Abre $WEB_DIR/index.html desde el servidor web"
echo ""
echo "Logs de cada scraper:"
echo "  SAIH:      $APP_DIR/saih.log"
echo "  SINAC:     $APP_DIR/sinac.log"
echo "  AEMET:     $APP_DIR/aemet.log"
echo "  Sentinel2: $APP_DIR/sentinel2.log"
echo "  SIRA:      $APP_DIR/sira.log"
echo "  Embalses:  $APP_DIR/embalses.log"
echo "  Alertas:   $APP_DIR/alertas.log"
