# AQUASALUD NORTE — Guía de instalación para el informático

**AGS Norte de Córdoba · Versión 15 de noviembre de 2026**

Panel web de inteligencia hídrico-sanitaria para los 27 municipios del norte de Córdoba. Muestra en tiempo real calidad del agua de consumo (SINAC), estado de embalses y ríos (SAIH), meteorología (AEMET), teledetección del embalse de La Colada (Sentinel-2) y calidad fisicoquímica (SIRA Guadiana).

---

## 1. Arquitectura

```
Servidor AGS (cron)
  ├── scraper_saih.py       → data/datos.json  + data/serie.json
  ├── scraper_sinac.py      → data/sinac_calidad.json
  ├── scraper_aemet.py      → data/aemet.json
  ├── scraper_sira.py       → data/sira.json
  ├── scraper_sentinel2.py  → data/sentinel2.json + data/sentinel2_thumb.jpg
  ├── alertas.py            → (lee datos.json, envía avisos si umbral cruzado)
  └── config.json           ← NUNCA en la carpeta web ni en GitHub

Carpeta web (Apache/Nginx)
  ├── index.html            ← panel estático, sin backend
  └── data/                 ← los scrapers escriben aquí cada X minutos
```

**El panel es 100 % estático.** El servidor web solo necesita servir archivos. No hace falta PHP, Node ni ningún backend. Los scrapers corren como cron en el servidor AGS y escriben JSON en la carpeta web.

---

## 2. Requisitos del servidor

| Componente | Versión mínima | Notas |
|---|---|---|
| Linux | cualquiera con systemd | Probado en Ubuntu 22.04 y Debian 12 |
| Python | 3.9 | Se instala un venv propio, no afecta al sistema |
| Servidor web | Apache 2 / Nginx | Solo ficheros estáticos. Puerto 80 o 443 |
| Acceso HTTPS saliente | — | Ver dominios en sección 4 |

**Windows:** el código Python es válido pero `instalar.sh` no funciona. Hay que crear el entorno virtual a mano y usar el Programador de tareas en lugar de cron.

---

## 3. Instalación

```bash
sudo bash instalar.sh /var/www/html/aquasalud /opt/aquasalud-norte
```

El script hace todo automáticamente:
- Copia los 6 scrapers y alertas.py a `/opt/aquasalud-norte/`
- Publica `index.html` y el esqueleto de datos en `/var/www/html/aquasalud/`
- Crea un entorno virtual Python con `requests`, `beautifulsoup4`, `lxml`
- Instala el cron en `/etc/cron.d/aquasalud-norte`
- Lanza una primera lectura del SAIH
- Crea `config.json` (si no existe ya) con plantilla vacía

**Después de instalar, editar config.json:**

```bash
nano /opt/aquasalud-norte/config.json
```

---

## 4. Dominios que el servidor debe poder alcanzar (HTTPS saliente)

| Dominio | Para qué | Scraper |
|---|---|---|
| `www.chguadalquivir.es` | Embalses y ríos | scraper_saih.py |
| `www.embalses.net` | Respaldo La Colada | scraper_saih.py |
| `sinac.sanidad.gob.es` | Calidad agua consumo | scraper_sinac.py |
| `opendata.aemet.es` | Meteorología | scraper_aemet.py |
| `www.siraguadiana.com` | Calidad fisicoquímica | scraper_sira.py |
| `identity.dataspace.copernicus.eu` | OAuth Sentinel-2 | scraper_sentinel2.py |
| `catalogue.dataspace.copernicus.eu` | Catálogo imágenes | scraper_sentinel2.py |
| `sh.dataspace.copernicus.eu` | Estadísticas + imágenes | scraper_sentinel2.py |
| El servidor ntfy o SMTP configurado | Alertas | alertas.py |

Si hay cortafuegos, hay que abrir estos dominios en salida HTTPS (puerto 443).

---

## 5. Configuración — config.json

Este archivo **nunca debe publicarse en la web ni subirse a GitHub**. Contiene claves privadas.

```jsonc
{
  "aemet_api_key": "...",              // Clave AEMET API (Junta de Andalucía)

  // Alertas por ntfy (push al móvil sin cuenta)
  "ntfy_topic": "...",                 // Tema largo y privado, ej: aquasalud-norte-xk7m9p
  "ntfy_server": "https://ntfy.sh",   // O servidor ntfy propio

  // Alertas por correo (alternativa o complemento a ntfy)
  "correo_smtp": "smtp.juntadeandalucia.es",
  "correo_puerto": 587,
  "correo_usuario": "...",
  "correo_password": "...",
  "correo_destino": "antonio.gonzalez.caballero.sspa@juntadeandalucia.es",

  // Sentinel-2 — Copernicus Data Space Ecosystem
  // Cliente OAuth: "aquasalud-norte" — creado en cdse.eu con la cuenta Junta
  "sentinel2_client_id": "sh-2ca6f517-c07c-437f-80b4-09f912ef1e68",
  "sentinel2_client_secret": "84O1H4uk7aNB2fDOmD0UwVEnBuBzWlqhbfx6ZDs7w4qiITQgoqJ.",
  "sentinel2_bbox_la_colada": [-5.1850, 38.3720, -5.1200, 38.4180],
  "sentinel2_instance_id": "aquasalud-norte"
}
```

> **Las claves Sentinel-2 ya están rellenas.** Solo hay que añadir `aemet_api_key` y los datos de notificación.

---

## 6. Cron instalado

```
*/15 * * * *   SAIH + alertas  (embalses, ríos, avisos de umbral)
*/15 * * * *   AEMET           (meteorología)
0 6,18 * * *   SINAC           (calidad agua consumo — dos veces al día)
0 8   * * *    Sentinel-2      (teledetección La Colada — máx. una imagen cada 5 días)
0 *   * * *    SIRA Guadiana   (calidad fisicoquímica — cada hora)
```

---

## 7. Regla de seguridad inamovible — NO APTA

**Esta regla nunca debe modificarse.** Si un análisis SINAC devuelve "no apta", el panel **no muestra ningún color rojo al ciudadano**. En su lugar:

- Vista ciudadana: muestra ÁMBAR con el texto "En verificación"
- El scraper envía una alerta interna (ntfy + correo) a Antonio González Caballero
- Nada se publica hasta que el técnico lo revise

Esta lógica está implementada en `scraper_sinac.py` (función `enviar_alerta_interna`) y en `index.html` (función `aplicarReglaRojo`). No tocar.

---

## 8. Actualizar el código (cuando llegue una nueva versión)

Como el informático tiene acceso al servidor pero Antonio no tiene acceso remoto después del despliegue, el proceso de actualización es:

```bash
# 1. En el servidor, descargar la nueva versión del repositorio
cd /tmp
git clone https://github.com/herzonekg-hue/Pulso-Hidrico-Cordoba- aquasalud-nueva
# (o git pull si ya lo tienes clonado)

# 2. Copiar los scripts actualizados (nunca tocar config.json)
cp aquasalud-nueva/scraper_*.py /opt/aquasalud-norte/
cp aquasalud-nueva/alertas.py   /opt/aquasalud-norte/
cp aquasalud-nueva/index.html   /var/www/html/aquasalud/

# 3. Verificar sintaxis
/opt/aquasalud-norte/venv/bin/python -m py_compile /opt/aquasalud-norte/scraper_saih.py
# (repetir para cada scraper)

# 4. El cron retoma automáticamente en la siguiente pasada. No hace falta reiniciar nada.
```

---

## 9. Logs y diagnóstico

| Log | Ruta | Qué buscar |
|---|---|---|
| SAIH | `/opt/aquasalud-norte/saih.log` | "datos.json actualizado: 15 embalses" |
| SINAC | `/opt/aquasalud-norte/sinac.log` | "sinac_calidad.json actualizado: 27 municipios" |
| AEMET | `/opt/aquasalud-norte/aemet.log` | "aemet.json actualizado" |
| Sentinel-2 | `/opt/aquasalud-norte/sentinel2.log` | "sentinel2.json actualizado" o "nube > 10 %, sin imagen" |
| SIRA | `/opt/aquasalud-norte/sira.log` | "sira.json actualizado" |
| Alertas | `/opt/aquasalud-norte/alertas.log` | "Alerta enviada" o "Sin cambios" |

**Problemas frecuentes:**

1. **El panel muestra datos de ejemplo** → `datos.json` no está en la carpeta web. Comprobar variable `HIDRICO_SALIDA` en el cron.
2. **SINAC muestra todo en gris** → El scraper no ha corrido o falló. Ver `sinac.log`.
3. **Sentinel-2 no actualiza** → Normal si hay nubes (nube > 10 % descarta la imagen). El último valor se conserva.
4. **El panel muestra horas de antigüedad** → El cron no se ejecuta. `systemctl status cron`.
5. **"EmbalNiv sin filas reconocibles"** → El SAIH cambió el diseño de su página. Contactar con Antonio.

---

## 10. Qué se ha probado y qué no

**Probado (en entorno de desarrollo):**
- Sintaxis de los 6 scrapers y alertas.py
- Scraper SAIH con páginas de ejemplo (fixture)
- Lógica de semáforos y alertas con valores forzados
- Visualización del panel en navegador (móvil y escritorio)
- Workflow CI en GitHub Actions verifica que nada se rompe en cada commit

**Pendiente de probar en servidor real:**
- Descarga real desde chguadalquivir.es, sinac.sanidad.gob.es, opendata.aemet.es, siraguadiana.com
- Autenticación OAuth Sentinel-2 con las claves de producción
- Envío real de alertas por ntfy y correo
- Integración del thumbnail Sentinel-2 en el panel (el panel ya tiene el punto de integración; falta validar con imagen real)

---

## 11. Repositorio

`https://github.com/herzonekg-hue/Pulso-Hidrico-Cordoba-` (privado)

El repositorio contiene el código fuente pero **nunca** `config.json`, `datos.json`, `serie.json` ni ningún dato de explotación. Solo el informático con acceso al repositorio puede ver el código.

**Importante:** `config.example.json` en el repositorio tiene las claves Sentinel-2 reales (ya configuradas). No es necesario tocarlas.

---

*AQUASALUD NORTE — AGS Norte de Córdoba — Antonio González Caballero*

---

## Actualización 24/11/2026: página de embalses y cómo se mantiene

**Qué se instala:** `instalar.sh` copia los scrapers (incluido `scraper_embalses.py`) y las páginas `index.html` y `embalses_cordoba.html` a la web del área, y crea un cron cada 10 minutos para `scraper_sira.py` y `scraper_embalses.py`. Solo Python 3 y servidor web estático; no hay base de datos ni procesos permanentes.

**Qué necesita de Antonio:** un `config.json` con sus claves (nunca a GitHub). Sin claves, los embalses y SIRA funcionan igual; solo Sentinel-2 y AEMET las requieren.

**Cómo se actualiza después:** no hay que rehacer nada a mano. Para recibir cambios, ejecutar `git pull` en la carpeta del repositorio y volver a lanzar `sudo ./instalar.sh` (no pisa `config.json`). Los datos se renuevan solos por cron. Alternativa sin servidor propio: la página ya está publicada en GitHub Pages y se actualiza sola.

**Comprobación de zonas de embalses:** `python pruebas/test_zonas.py` (debe imprimir «Zonas correctas»).

**Actualización automática (sin intervención):** `instalar.sh` deja un cron diario (03:30) que ejecuta `actualizar.sh`: descarga la rama `produccion` y, si hay cambios, los aplica sin tocar `config.json`. Solo se publica en `produccion` lo ya probado, así que el servidor nunca recibe cambios a medias. Registro en `actualizar.log`. Si no se quiere automático, borrar esa línea de `/etc/cron.d/aquasalud-norte`.
