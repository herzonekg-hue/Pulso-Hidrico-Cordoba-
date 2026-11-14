#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 1b: SIRA Guadiana
Scraper del Sistema de Información de Redes Automáticas de la cuenca del Guadiana.

Integra datos que el SAIH del Guadalquivir no cubre:
  - Embalse La Colada (E2-40): nivel, volumen, % capacidad, caudal de salida
  - Estación de calidad EAA-421 (El Viso, río Guadarramilla): fosfatos, amonio, pH
  - Estación de aforo CR2-08 (Zújar en Guadamatilla, El Viso): caudal

Endpoints descubiertos en siraguadiana.com:
  GET /backend/Visor/estaciones  → catálogo de estaciones
  GET /backend/Visor/resourceByID → datos en tiempo real por ID

Escribe: sira.json (leído por index.html y alertas.py)

Autor: Proyecto Hídrico — AGS Norte de Córdoba
"""

import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error
import urllib.parse
import ssl
from datetime import datetime, timezone
from pathlib import Path

# ── Configuración ─────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent
SALIDA_DIR  = Path(os.environ.get("HIDRICO_SALIDA", SCRIPT_DIR))

BASE_URL    = "https://siraguadiana.com/backend/Visor"
URL_ESTACIONES  = f"{BASE_URL}/estaciones"
URL_RESOURCE    = f"{BASE_URL}/resourceByID"

# Headers que imitan al navegador — imprescindible para que el backend responda
HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept":      "application/json, text/plain, */*",
    "Referer":     "https://siraguadiana.com/E/DATOS",
    "Origin":      "https://siraguadiana.com",
    "Accept-Language": "es-ES,es;q=0.9",
}

# Estaciones del norte de Córdoba que nos interesan
# cod_estacion tal como aparece en /estaciones
ESTACIONES_OBJETIVO = {
    # Embalses (tipo E)
    "E2-40": {
        "nombre":  "La Colada",
        "tipo":    "embalse",
        "rio":     "Guadamatilla",
        "nota":    "Embalse de reserva comarca Los Pedroches. Cuenca CHG.",
    },
    # Calidad del agua (tipo EAA — SAICA)
    "EAA-421": {
        "nombre":  "El Viso — Guadarramilla",
        "tipo":    "calidad",
        "rio":     "Guadarramilla",
        "nota":    "Sonda SAICA. Mide fosfatos, amonio, pH antes de desembocar en La Colada. "
                   "Datos usados en la denuncia ante Fiscalía (sep 2026).",
    },
    # Aforo (tipo CR)
    "CR2-08": {
        "nombre":  "Zújar en Guadamatilla",
        "tipo":    "aforo",
        "rio":     "Zújar / Guadamatilla",
        "nota":    "Aforo en El Viso. Caudal que entra a La Colada por el Guadamatilla.",
    },
}

# Umbrales de alerta calidad (EAA-421)
UMBRAL_FOSFATOS_ALERTA   = 0.4   # mg/L  (límite legal vertido = 1 mg/L pero alarma antes)
UMBRAL_FOSFATOS_CRITICO  = 1.0   # mg/L  (límite legal)
UMBRAL_AMONIO_ALERTA     = 0.5   # mg/L
UMBRAL_AMONIO_CRITICO    = 1.0   # mg/L

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sira] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("sira")

# ── Cliente HTTP ───────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, timeout: int = 20):
    ctx = ssl._create_unverified_context()
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.warning("Error GET %s — %s", url, e)
        return None


# ── Funciones de consulta ──────────────────────────────────────────────────────

def obtener_catalogo_estaciones() -> dict:
    """Descarga el catálogo completo y extrae los IDs internos de nuestras estaciones."""
    log.info("Descargando catálogo de estaciones SIRA…")
    data = _get(URL_ESTACIONES)
    if not data:
        return {}

    # Mapear cod_estacion → id interno (necesario para resourceByID)
    mapa = {}

    # El catálogo tiene varios arrays: estaciones (EAA), embalses, aforos…
    # Exploramos todos los que haya
    if isinstance(data, dict):
        for clave, lista in data.items():
            if not isinstance(lista, list):
                continue
            for item in lista:
                cod = item.get("cod_estacion") or item.get("cod_embalse") or ""
                if cod in ESTACIONES_OBJETIVO:
                    mapa[cod] = {
                        "id_interno": item.get("id") or item.get("id_estacion") or cod,
                        "nombre_sira": item.get("nombre", ""),
                        "cod": cod,
                    }
                    log.info("Encontrada: %s — %s (id=%s)",
                             cod, item.get("nombre",""), mapa[cod]["id_interno"])
    elif isinstance(data, list):
        for item in data:
            cod = item.get("cod_estacion") or item.get("cod_embalse") or ""
            if cod in ESTACIONES_OBJETIVO:
                mapa[cod] = {
                    "id_interno": item.get("id") or item.get("id_estacion") or cod,
                    "nombre_sira": item.get("nombre", ""),
                    "cod": cod,
                }

    log.info("Estaciones objetivo localizadas: %d/%d", len(mapa), len(ESTACIONES_OBJETIVO))
    return mapa


def obtener_datos_estacion(cod: str, id_interno: str) -> dict | None:
    """Consulta los datos en tiempo real de una estación por su ID interno."""
    log.info("Consultando datos %s (id=%s)…", cod, id_interno)
    # Probamos los parámetros más comunes que usan estos backends
    for params in [
        {"id": id_interno},
        {"cod": cod},
        {"cod_estacion": cod},
        {"id": id_interno, "tipo": ESTACIONES_OBJETIVO[cod]["tipo"]},
    ]:
        data = _get(URL_RESOURCE, params=params)
        if data:
            log.info("Datos obtenidos para %s con params %s", cod, params)
            return data
        time.sleep(0.3)
    log.warning("Sin datos para %s", cod)
    return None


def normalizar_embalse(cod: str, raw: dict) -> dict:
    """Extrae campos relevantes de la respuesta de un embalse."""
    # Intentamos varias rutas posibles según lo que vimos en la Preview
    embalses = raw.get("embalses_volcap") or raw.get("est_res") or raw.get("ult_res") or raw
    if isinstance(embalses, dict):
        volumen    = embalses.get("volumen")
        capacidad  = embalses.get("capacidad")
        porcentaje = embalses.get("porcentajeVol")
        nivel      = embalses.get("nivel") or embalses.get("cota")
        q_salida   = embalses.get("caudal_salida") or embalses.get("Qsalida")
    else:
        volumen = capacidad = porcentaje = nivel = q_salida = None

    return {
        "cod":        cod,
        **ESTACIONES_OBJETIVO[cod],
        "nivel_msnm":    _float(nivel),
        "volumen_hm3":   _float(volumen),
        "capacidad_hm3": _float(capacidad),
        "pct_capacidad": _float(porcentaje),
        "q_salida_m3s":  _float(q_salida),
        "ok":            volumen is not None,
    }


def normalizar_calidad(cod: str, raw: dict) -> dict:
    """Extrae parámetros de calidad de la respuesta de una EAA."""
    ult = raw.get("ult_res") or raw.get("est_res") or raw
    if isinstance(ult, dict):
        fosfatos = ult.get("fosfatos") or ult.get("PO4") or ult.get("P")
        amonio   = ult.get("amonio")   or ult.get("NH4") or ult.get("N")
        ph       = ult.get("pH")       or ult.get("ph")
        turbidez = ult.get("turbidez") or ult.get("SST")
        cot      = ult.get("COT")      or ult.get("TOC")
        hora     = ult.get("fecha")    or ult.get("hora") or ""
    else:
        fosfatos = amonio = ph = turbidez = cot = None
        hora = ""

    fosfatos_v = _float(fosfatos)
    amonio_v   = _float(amonio)

    # Nivel de alerta
    if fosfatos_v and fosfatos_v >= UMBRAL_FOSFATOS_CRITICO:
        nivel_alerta = "critico"
    elif fosfatos_v and fosfatos_v >= UMBRAL_FOSFATOS_ALERTA:
        nivel_alerta = "alerta"
    elif amonio_v and amonio_v >= UMBRAL_AMONIO_CRITICO:
        nivel_alerta = "critico"
    elif amonio_v and amonio_v >= UMBRAL_AMONIO_ALERTA:
        nivel_alerta = "alerta"
    else:
        nivel_alerta = "normal"

    return {
        "cod":          cod,
        **ESTACIONES_OBJETIVO[cod],
        "hora_obs":     hora,
        "fosfatos_mgl": fosfatos_v,
        "amonio_mgl":   amonio_v,
        "ph":           _float(ph),
        "turbidez_ntu": _float(turbidez),
        "cot_mgl":      _float(cot),
        "nivel_alerta": nivel_alerta,
        "umbral_fosfatos_alerta_mgl":  UMBRAL_FOSFATOS_ALERTA,
        "umbral_fosfatos_critico_mgl": UMBRAL_FOSFATOS_CRITICO,
        "ok":           fosfatos is not None or amonio is not None,
    }


def normalizar_aforo(cod: str, raw: dict) -> dict:
    """Extrae caudal de la respuesta de un aforo."""
    ult = raw.get("ult_res") or raw.get("est_res") or raw
    if isinstance(ult, dict):
        caudal = ult.get("caudal") or ult.get("Q") or ult.get("valor")
        nivel  = ult.get("nivel")  or ult.get("N")
        hora   = ult.get("fecha")  or ult.get("hora") or ""
    else:
        caudal = nivel = None
        hora = ""

    return {
        "cod":       cod,
        **ESTACIONES_OBJETIVO[cod],
        "hora_obs":  hora,
        "caudal_m3s": _float(caudal),
        "nivel_mla":  _float(nivel),
        "ok":        caudal is not None,
    }


def _float(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return default


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Modo fixture
    fixture_dir = os.environ.get("HIDRICO_FIXTURE_DIR")
    if fixture_dir:
        fixture = Path(fixture_dir) / "sira_fixture.json"
        if fixture.exists():
            log.info("Modo fixture — %s", fixture)
            with open(fixture, encoding="utf-8") as f:
                salida = json.load(f)
            out = SALIDA_DIR / "sira.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(salida, f, ensure_ascii=False, indent=2)
            log.info("sira.json escrito desde fixture → %s", out)
            return

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Obtener catálogo para resolver IDs internos
    mapa = obtener_catalogo_estaciones()

    embalses = []
    calidad  = []
    aforos   = []

    # 2. Consultar cada estación objetivo
    for cod, meta in ESTACIONES_OBJETIVO.items():
        id_interno = mapa.get(cod, {}).get("id_interno", cod)
        raw = obtener_datos_estacion(cod, id_interno)
        time.sleep(0.5)

        if raw is None:
            # Entrada vacía para mantener estructura
            entrada = {"cod": cod, **meta, "ok": False}
            if meta["tipo"] == "embalse":
                embalses.append(entrada)
            elif meta["tipo"] == "calidad":
                calidad.append(entrada)
            else:
                aforos.append(entrada)
            continue

        if meta["tipo"] == "embalse":
            embalses.append(normalizar_embalse(cod, raw))
        elif meta["tipo"] == "calidad":
            calidad.append(normalizar_calidad(cod, raw))
        else:
            aforos.append(normalizar_aforo(cod, raw))

    # 3. Resumen de alerta calidad
    alerta_maxima = "normal"
    for c in calidad:
        if c.get("nivel_alerta") == "critico":
            alerta_maxima = "critico"
        elif c.get("nivel_alerta") == "alerta" and alerta_maxima != "critico":
            alerta_maxima = "alerta"

    salida = {
        "generado":      ahora,
        "fuente":        "SIRA Guadiana — siraguadiana.com (CHG)",
        "aviso":         "Datos automáticos sujetos a validación. No sustituyen avisos oficiales.",
        "alerta_calidad": alerta_maxima,
        "embalses":      embalses,
        "calidad":       calidad,
        "aforos":        aforos,
    }

    out = SALIDA_DIR / "sira.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info("sira.json escrito → %s | alerta_calidad=%s", out, alerta_maxima)

    if alerta_maxima == "critico":
        sys.exit(2)
    elif alerta_maxima == "alerta":
        sys.exit(1)


if __name__ == "__main__":
    main()
