#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 4: Teledetección Sentinel-2 (embalse La Colada)
Copernicus Data Space Ecosystem — Sentinel Hub Process API.

Descarga la imagen más reciente (<10% nubosidad, máx. 30 días) del embalse
de La Colada y calcula tres índices espectrales:
  - NDWI  (Normalized Difference Water Index) → superficie de agua
  - NDTI  (Normalized Difference Turbidity Index) → turbidez
  - Clorofila aproximada (Band 5 / Band 4) → eutrofización

Escribe: data/sentinel2.json  (leído por index.html, capa visual futura)
         data/sentinel2_thumb.jpg  (miniatura RGB para el panel)

Fuente oficial: Copernicus Data Space Ecosystem (dataspace.copernicus.eu)
                Sentinel-2 L2A — Agencia Espacial Europea (ESA)
                Licencia: Copernicus Open Access (uso libre)

Autor: Proyecto Hídrico — AGS Norte de Córdoba
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Configuración ─────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SALIDA_DIR  = Path(os.environ.get("HIDRICO_SALIDA", SCRIPT_DIR / "data"))
LOG_PATH    = SCRIPT_DIR / "sentinel2.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [s2] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ]
)
log = logging.getLogger("sentinel2")

# Endpoints CDSE
TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# BBox por defecto (sobrescribible en config.json)
# [lon_min, lat_min, lon_max, lat_max] — La Colada, norte de Córdoba
BBOX_DEFAULT = [-5.1850, 38.3720, -5.1200, 38.4180]

# Días hacia atrás en que buscamos imagen válida
LOOKBACK_DAYS    = 30
MAX_NUBOSIDAD    = 10   # %


# ── OAuth2 — obtener token ────────────────────────────────────────────────────

def obtener_token(client_id: str, client_secret: str) -> str:
    data = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        import ssl
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
            token = resp.get("access_token")
            if not token:
                raise ValueError(f"Sin access_token: {resp}")
            log.info("Token OAuth obtenido (expira en %ds)", resp.get("expires_in", "?"))
            return token
    except Exception as e:
        log.error("Error obteniendo token: %s", e)
        raise


# ── Búsqueda de imagen en el catálogo STAC ────────────────────────────────────

def buscar_imagen(bbox: list, token: str) -> dict | None:
    """
    Busca la imagen Sentinel-2 L2A más reciente con nubosidad < MAX_NUBOSIDAD
    dentro de los últimos LOOKBACK_DAYS días sobre el bbox indicado.
    """
    fecha_fin   = datetime.now(timezone.utc)
    fecha_inicio = fecha_fin - timedelta(days=LOOKBACK_DAYS)

    payload = json.dumps({
        "collections": ["sentinel-2-l2a"],
        "bbox":        bbox,
        "datetime":    f"{fecha_inicio.strftime('%Y-%m-%dT%H:%M:%SZ')}/{fecha_fin.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "query": {
            "eo:cloud_cover": {"lt": MAX_NUBOSIDAD}
        },
        "sortby": [{"field": "datetime", "direction": "desc"}],
        "limit": 5,
    }).encode("utf-8")

    import ssl
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        CATALOG_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
            features = resp.get("features", [])
            if not features:
                log.warning("Sin imágenes disponibles (nubosidad < %d%%, %d días)", MAX_NUBOSIDAD, LOOKBACK_DAYS)
                return None
            best = features[0]
            props = best.get("properties", {})
            log.info(
                "Imagen seleccionada: %s | nubosidad: %.1f%% | fecha: %s",
                best.get("id", "?"),
                props.get("eo:cloud_cover", "?"),
                props.get("datetime", "?"),
            )
            return best
    except urllib.error.HTTPError as e:
        try:
            cuerpo = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            cuerpo = ""
        log.error("Error en búsqueda catálogo: HTTP %s %s", e.code, cuerpo)
        return None
    except Exception as e:
        log.error("Error en búsqueda catálogo: %s", e)
        return None


# ── Process API — calcular índices y miniatura ────────────────────────────────

def _evalscript_indices() -> str:
    """
    EvalScript para Sentinel Hub: devuelve imagen con 4 canales extra
    codificados en la respuesta multipart (B4, B3, B2 para RGB + índices).
    """
    return """
//VERSION=3
function setup() {
  return {
    input: ["B02","B03","B04","B05","B08","B11","SCL"],
    output: [
      { id: "rgb",    bands: 3, sampleType: "UINT8"   },
      { id: "ndwi",   bands: 1, sampleType: "FLOAT32" },
      { id: "ndti",   bands: 1, sampleType: "FLOAT32" },
      { id: "chl",    bands: 1, sampleType: "FLOAT32" },
    ]
  };
}
function evaluatePixel(s) {
  // NDWI = (B03 - B08) / (B03 + B08)
  let ndwi = (s.B03 - s.B08) / (s.B03 + s.B08 + 1e-6);
  // NDTI = (B04 - B03) / (B04 + B03)  — turbidez (Lacaux 2007)
  let ndti = (s.B04 - s.B03) / (s.B04 + s.B03 + 1e-6);
  // Aprox. clorofila: B05/B04
  let chl  = s.B05 / (s.B04 + 1e-6);
  // RGB natural (valor 0–255 aprox.)
  let gain = 3.5;
  let r = Math.min(255, s.B04 * 255 * gain);
  let g = Math.min(255, s.B03 * 255 * gain);
  let b = Math.min(255, s.B02 * 255 * gain);
  return {
    rgb:  [r, g, b],
    ndwi: [ndwi],
    ndti: [ndti],
    chl:  [chl],
  };
}
"""


def solicitar_indices(bbox: list, fecha_imagen: str, token: str) -> dict | None:
    """
    Llama a la Process API para obtener los valores medios de los índices
    sobre el polígono del embalse. Devuelve dict con medias estadísticas.
    """
    # Usamos el Statistical API para obtener estadísticas en lugar de imagen raster
    # (más eficiente: devuelve JSON con percentiles, medias, etc.)
    evalscript_stat = """
//VERSION=3
function setup() {
  return {
    input: [{ bands:["B02","B03","B04","B05","B08","B11","SCL"] }],
    output: [
      { id:"ndwi", bands:1, sampleType:"FLOAT32" },
      { id:"ndti", bands:1, sampleType:"FLOAT32" },
      { id:"chl",  bands:1, sampleType:"FLOAT32" },
    ],
    mosaicking: "SIMPLE"
  };
}
function evaluatePixel(s) {
  let ndwi = (s.B03 - s.B08) / (s.B03 + s.B08 + 1e-6);
  let ndti = (s.B04 - s.B03) / (s.B04 + s.B03 + 1e-6);
  let chl  = s.B05 / (s.B04 + 1e-6);
  // Enmascarar nubes y sombras (SCL 3=cloud_shadow, 8=cloud_medium, 9=cloud_high)
  if ([3, 8, 9].includes(s.SCL[0])) return { ndwi:[NaN], ndti:[NaN], chl:[NaN] };
  return { ndwi:[ndwi], ndti:[ndti], chl:[chl] };
}
"""

    # Parsear fecha (ej: "2026-10-21T10:30:00Z" → intervalo de 1 día)
    try:
        dt = datetime.fromisoformat(fecha_imagen.replace("Z", "+00:00"))
        d_ini = (dt - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d_fin = (dt + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        d_ini = fecha_imagen
        d_fin = fecha_imagen

    payload = json.dumps({
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": d_ini, "to": d_fin},
                    "maxCloudCoverage": MAX_NUBOSIDAD,
                }
            }]
        },
        "aggregation": {
            "timeRange": {"from": d_ini, "to": d_fin},
            "aggregationInterval": {"of": "P1D"},
            "resx": 10,
            "resy": 10,
            "evalscript": evalscript_stat,
        },
        "calculations": {
            "ndwi": {"histograms": {"default": {"nBins":10, "lowEdge":-1.0, "highEdge":1.0}}},
            "ndti": {"histograms": {"default": {"nBins":10, "lowEdge":-1.0, "highEdge":1.0}}},
            "chl":  {"histograms": {"default": {"nBins":10, "lowEdge":0.0,  "highEdge":5.0}}},
        }
    }).encode("utf-8")

    import ssl
    ctx = ssl._create_unverified_context()
    stat_url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    req = urllib.request.Request(
        stat_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Statistical API HTTP %s: %s", e.code, body[:300])
        return None
    except Exception as e:
        log.error("Error Statistical API: %s", e)
        return None


def descargar_miniatura(bbox: list, fecha_imagen: str, token: str) -> bool:
    """Descarga imagen RGB de 256x256 px como miniatura para el panel."""
    try:
        dt = datetime.fromisoformat(fecha_imagen.replace("Z", "+00:00"))
        d_ini = (dt - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d_fin = (dt + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        d_ini = d_fin = fecha_imagen

    evalscript_rgb = """
//VERSION=3
function setup() { return { input:["B02","B03","B04"], output:{bands:3, sampleType:"UINT8"} }; }
function evaluatePixel(s) {
  let gain = 3.5;
  return [
    Math.min(255, s.B04*255*gain),
    Math.min(255, s.B03*255*gain),
    Math.min(255, s.B02*255*gain),
  ];
}
"""

    payload = json.dumps({
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}
            },
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {
                    "timeRange": {"from": d_ini, "to": d_fin},
                    "maxCloudCoverage": MAX_NUBOSIDAD,
                }
            }]
        },
        "output": {
            "width": 256, "height": 256,
            "responses": [{"identifier":"default","format":{"type":"image/jpeg","quality":85}}]
        },
        "evalscript": evalscript_rgb,
    }).encode("utf-8")

    import ssl
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        PROCESS_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "image/jpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            img_bytes = r.read()
        out = SALIDA_DIR / "sentinel2_thumb.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(img_bytes)
        log.info("Miniatura RGB guardada → %s (%d KB)", out, len(img_bytes)//1024)
        return True
    except Exception as e:
        log.error("Error descargando miniatura: %s", e)
        return False


# ── Interpretar estadísticas → semáforo ──────────────────────────────────────

def interpretar_indices(stats_resp: dict) -> dict:
    """
    Extrae medias de NDWI, NDTI y Chl de la respuesta Statistical API
    y devuelve un dict con valores y semáforo de estado del embalse.
    """
    resultado = {
        "ndwi_medio":   None,
        "ndti_medio":   None,
        "chl_ratio_medio": None,
        "estado_agua":  "sin_datos",
        "estado_turb":  "sin_datos",
        "estado_chl":   "sin_datos",
        "semaforo":     "GRIS",
        "observacion":  "Sin datos espectrales disponibles",
    }

    try:
        intervalos = stats_resp.get("data", [])
        if not intervalos:
            return resultado
        # Tomar el primer intervalo con datos
        for intervalo in intervalos:
            outputs = intervalo.get("outputs", {})
            ndwi_bands = outputs.get("ndwi", {}).get("bands", {})
            ndti_bands = outputs.get("ndti", {}).get("bands", {})
            chl_bands  = outputs.get("chl",  {}).get("bands", {})

            ndwi_mean = ndwi_bands.get("B0", {}).get("stats", {}).get("mean")
            ndti_mean = ndti_bands.get("B0", {}).get("stats", {}).get("mean")
            chl_mean  = chl_bands.get("B0",  {}).get("stats", {}).get("mean")

            if ndwi_mean is None:
                continue

            resultado["ndwi_medio"]       = round(ndwi_mean, 4)
            resultado["ndti_medio"]       = round(ndti_mean, 4) if ndti_mean else None
            resultado["chl_ratio_medio"]  = round(chl_mean,  4) if chl_mean  else None

            # Estado agua (NDWI > 0.2 → agua; 0–0.2 → húmedo; < 0 → tierra)
            if ndwi_mean > 0.2:
                resultado["estado_agua"] = "agua_confirmada"
            elif ndwi_mean > 0:
                resultado["estado_agua"] = "humedo"
            else:
                resultado["estado_agua"] = "seco_o_bajo"

            # Turbidez (NDTI: > 0.05 → turbio; negativo → claro)
            if ndti_mean is not None:
                if ndti_mean > 0.10:
                    resultado["estado_turb"] = "alta"
                elif ndti_mean > 0.05:
                    resultado["estado_turb"] = "moderada"
                else:
                    resultado["estado_turb"] = "baja"

            # Clorofila aproximada (ratio B05/B04: > 1.5 → alerta)
            if chl_mean is not None:
                if chl_mean > 2.0:
                    resultado["estado_chl"] = "alta"
                elif chl_mean > 1.5:
                    resultado["estado_chl"] = "moderada"
                else:
                    resultado["estado_chl"] = "baja"

            # Semáforo global
            if resultado["estado_turb"] == "alta" or resultado["estado_chl"] == "alta":
                resultado["semaforo"]    = "NARANJA"
                resultado["observacion"] = "Turbidez o clorofila elevada detectada por satélite"
            elif resultado["estado_agua"] == "seco_o_bajo":
                resultado["semaforo"]    = "AMARILLO"
                resultado["observacion"] = "Nivel bajo detectado (NDWI < 0)"
            elif resultado["estado_agua"] == "agua_confirmada":
                resultado["semaforo"]    = "VERDE"
                resultado["observacion"] = "Lámina de agua normal, calidad espectral aceptable"
            else:
                resultado["semaforo"]    = "GRIS"
                resultado["observacion"] = "Datos espectrales insuficientes"

            break  # primer intervalo válido es suficiente

    except Exception as e:
        log.error("Error interpretando estadísticas: %s", e)

    return resultado


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Credenciales: variables de entorno (GitHub Secrets) o, en el servidor AGS, config.json.
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)

    client_id     = (os.environ.get("SENTINEL2_CLIENT_ID") or cfg.get("sentinel2_client_id", "")).strip()
    client_secret = (os.environ.get("SENTINEL2_CLIENT_SECRET") or cfg.get("sentinel2_client_secret", "")).strip()
    bbox          = cfg.get("sentinel2_bbox_la_colada", BBOX_DEFAULT)

    if not client_id or not client_secret:
        log.error("Faltan credenciales Copernicus: SENTINEL2_CLIENT_ID / SENTINEL2_CLIENT_SECRET (o config.json)")
        sys.exit(1)

    # Modo fixture
    fixture_dir = os.environ.get("HIDRICO_FIXTURE_DIR")
    if fixture_dir:
        fixture = Path(fixture_dir) / "sentinel2_fixture.json"
        if fixture.exists():
            log.info("Modo fixture — %s", fixture)
            with open(fixture, encoding="utf-8") as f:
                salida = json.load(f)
            out = SALIDA_DIR / "sentinel2.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(salida, f, ensure_ascii=False, indent=2)
            return

    ahora = datetime.now(timezone.utc)

    # 1. Token OAuth
    log.info("Autenticando en Copernicus Data Space…")
    token = obtener_token(client_id, client_secret)

    # 2. Buscar imagen más reciente
    log.info("Buscando imagen Sentinel-2 L2A sobre La Colada (últimos %d días)…", LOOKBACK_DAYS)
    imagen = buscar_imagen(bbox, token)

    if not imagen:
        salida = {
            "meta": {
                "generado": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fuente":   "Copernicus/Sentinel-2 L2A — ESA",
                "estado":   "sin_imagen",
                "motivo":   f"Sin imágenes con nubosidad <{MAX_NUBOSIDAD}% en los últimos {LOOKBACK_DAYS} días",
            },
            "indices": None,
        }
    else:
        props         = imagen.get("properties", {})
        fecha_imagen  = props.get("datetime", ahora.strftime("%Y-%m-%dT%H:%M:%SZ"))
        nubosidad     = props.get("eo:cloud_cover", "?")
        imagen_id     = imagen.get("id", "?")

        # 3. Estadísticas espectrales
        log.info("Calculando índices espectrales…")
        stats = solicitar_indices(bbox, fecha_imagen, token)
        indices = interpretar_indices(stats) if stats else {
            "semaforo": "GRIS", "observacion": "Error en Statistical API"
        }

        # 4. Miniatura RGB
        log.info("Descargando miniatura RGB…")
        descargar_miniatura(bbox, fecha_imagen, token)

        # Fecha legible
        try:
            dt_obj = datetime.fromisoformat(fecha_imagen.replace("Z", "+00:00"))
            meses = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
            fecha_legible = f"{dt_obj.day} {meses[dt_obj.month-1]} {dt_obj.year}"
        except Exception:
            fecha_legible = fecha_imagen[:10]

        salida = {
            "meta": {
                "generado":       ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fuente":         "Copernicus/Sentinel-2 L2A — ESA",
                "imagen_id":      imagen_id,
                "fecha_imagen":   fecha_imagen,
                "fecha_legible":  fecha_legible,
                "nubosidad_pct":  nubosidad,
                "bbox":           bbox,
                "estado":         "ok",
            },
            "indices": indices,
        }
        log.info(
            "Sentinel-2 OK | semáforo: %s | NDWI: %s | NDTI: %s | Chl: %s",
            indices.get("semaforo"),
            indices.get("ndwi_medio"),
            indices.get("ndti_medio"),
            indices.get("chl_ratio_medio"),
        )

    # Escritura atómica
    out = SALIDA_DIR / "sentinel2.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    tmp.replace(out)
    log.info("sentinel2.json escrito → %s", out)
    sys.exit(0)


if __name__ == "__main__":
    main()
