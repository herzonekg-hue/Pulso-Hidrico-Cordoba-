#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 1b: SIRA Guadiana
Scraper del Sistema de Información de Redes Automáticas de la cuenca del Guadiana.

Integra datos que el SAIH del Guadalquivir no cubre:
  - Embalse La Colada (E2-40): nivel, volumen, % capacidad, caudal de salida
  - Estación de calidad EAA-421 (El Viso, río Guadarramilla): fosfatos, amonio, pH
  - Estación de aforo CR2-08 (Zújar en Guadamatilla, El Viso): caudal

Endpoints descubiertos en siraguadiana.com:
  GET  /backend/Visor/estaciones     → catálogo de estaciones
  POST /backend/Visor/resourceByID   → datos en tiempo real
       body: {"id":"<prefijo>","idEstacion":"<cod>","type":"ficha_redes_control_detalle"}
  POST /backend/security/getProfile  → token sesión anónima (~12h)
       body: {"COD_USUARIO":"public","ORIGIN":"...","APP":"VISOR","CASTICKET":null}

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

BASE_URL        = "https://siraguadiana.com/backend/Visor"
URL_ESTACIONES  = f"{BASE_URL}/estaciones"
URL_RESOURCE    = f"{BASE_URL}/resourceByID"
URL_CALIDAD     = f"{BASE_URL}/calidad"     # POST — datos por estación EAA (body: consulta/estacion/tiempo)
# Embalses: tabla pública vía resourceByID/ficha_redes_control. Aforos (CR2-08): pendiente confirmar con DevTools
URL_GETPROFILE  = "https://siraguadiana.com/backend/security/getProfile"

# Headers base que imitan al navegador — imprescindible para que el backend responda
HEADERS = {
    "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept":        "application/json, text/plain, */*",
    "Referer":       "https://www.siraguadiana.com/",
    "Origin":        "https://www.siraguadiana.com",
    "Accept-Language": "es-ES,es;q=0.9",
    "Content-Type":  "application/json",
}


def obtener_token_sira() -> str | None:
    """
    Solicita un token de sesión anónima a SIRA Guadiana.
    Endpoint: POST /backend/security/getProfile
    Body: {"COD_USUARIO":"public","ORIGIN":"https://www.siraguadiana.com","APP":"VISOR","CASTICKET":null}
    Respuesta: {"sesionId": "eyJ...", ...}
    El token tiene ~12 h de validez (43200 s).
    No contiene credenciales privadas — es una sesión pública de solo lectura.
    """
    ctx = ssl._create_unverified_context()
    payload = json.dumps({
        "COD_USUARIO": "public",
        "ORIGIN":      "https://www.siraguadiana.com",
        "APP":         "VISOR",
        "CASTICKET":   None,
    }).encode("utf-8")
    req = urllib.request.Request(
        URL_GETPROFILE,
        data=payload,
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        token = data.get("sesionId") or data.get("token") or data.get("jwt")
        if token:
            log.info("Token SIRA obtenido (exp ~12h)")
            return token
        log.warning("getProfile respondió pero sin sesionId: %s", list(data.keys()))
        return None
    except Exception as e:
        log.warning("No se pudo obtener token SIRA: %s", e)
        return None

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


def _post(url: str, body: dict, timeout: int = 20):
    ctx = ssl._create_unverified_context()
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log.warning("HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.warning("Error POST %s — %s", url, e)
        return None


# ── Funciones de consulta ──────────────────────────────────────────────────────

def obtener_calidad_eaa(cod: str) -> dict | None:
    """
    Consulta datos de calidad de una EAA via POST /calidad.
    Body confirmado en DevTools (20-11-2026):
      {"consulta": "EAA_DAT", "estacion": "<cod>", "tiempo": 7, "fInicial": null, "fFinal": null}

    La respuesta es una lista de registros ordenada cronológicamente:
      [{"Timestamp": "2026-11-19 02:00:00", "TA": 15.92, "PO": 9.76, "NH": 20.0,
        "PH": 8.76, "OX": 5.87, "CD": 1650.75, "TB": 4.36, "NO": 0.0,
        "Código Estación": "EAA-421"}, ...]
    Devuelve el último registro envuelto en {"ult_res": {"valores": [<último>]}}
    para que sea compatible con _extraer_valores_ult_res() y normalizar_calidad().
    El campo timestamp se mapea desde "Timestamp".
    """
    log.info("POST /calidad para %s…", cod)
    body = {
        "consulta": "EAA_DAT",
        "estacion": cod,
        "tiempo":   7,
        "fInicial": None,
        "fFinal":   None,
    }
    data = _post(URL_CALIDAD, body)
    if not data:
        log.warning("Sin datos /calidad para %s", cod)
        return None

    # La respuesta es una lista; cogemos el último registro no vacío
    registros = data if isinstance(data, list) else []
    registro = None
    for r in reversed(registros):
        if isinstance(r, dict) and r.get("PO") is not None:
            registro = r
            break
    if registro is None and registros:
        registro = registros[-1]

    if not registro:
        log.warning("Lista /calidad vacía para %s", cod)
        return None

    # Normalizar campo timestamp (clave con mayúscula en /calidad)
    if "Timestamp" in registro and "timestamp" not in registro:
        registro = dict(registro)
        registro["timestamp"] = registro["Timestamp"]

    log.info("Datos /calidad obtenidos para %s — timestamp=%s PO=%s NH=%s",
             cod, registro.get("timestamp"), registro.get("PO"), registro.get("NH"))
    # Envolver para compatibilidad con _extraer_valores_ult_res
    return {"ult_res": {"valores": [registro]}, "fuente": "calidad"}


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
                        "id_interno": cod,
                        "nombre_sira": item.get("nombre", ""),
                        "cod": cod,
                    }
                    log.info("Encontrada: %s — %s", cod, item.get("nombre",""))
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


def _prefijo_estacion(cod: str) -> str:
    """Extrae el prefijo de tipo a partir del código de estación.
    EAA-421 → "EAA", E2-40 → "E", CR2-08 → "CR"
    """
    return cod.split("-")[0] if "-" in cod else cod


def obtener_datos_estacion(cod: str, id_interno: str) -> dict | None:
    """Consulta los datos en tiempo real de una estación.
    Endpoint: POST /backend/Visor/resourceByID
    Body: {"id": "<prefijo>", "idEstacion": "<cod>", "type": "ficha_redes_control_detalle"}
    """
    log.info("Consultando datos %s…", cod)
    prefijo = _prefijo_estacion(cod)
    body = {
        "id":         prefijo,
        "idEstacion": cod,
        "type":       "ficha_redes_control_detalle",
    }
    data = _post(URL_RESOURCE, body)
    if data:
        log.info("Datos obtenidos para %s", cod)
        return data
    log.warning("Sin datos para %s", cod)
    return None


def obtener_tabla_embalses() -> dict:
    """Tabla pública de todos los embalses del Guadiana (la misma que muestra la web de SIRA).
    Endpoint: POST /backend/Visor/resourceByID
    Body confirmado (sondeo del 24-11-2026, sin 401):
      {"id": "E", "idEstacion": "E", "type": "ficha_redes_control"}
    Devuelve {cod_estacion: fila}; cada fila trae NE1 (nivel, msnm), VE1 (volumen, hm3),
    PV1 (% de llenado), QSR (caudal de salida, m3/s) y timestamp.
    """
    log.info("Descargando tabla de embalses SIRA (ficha_redes_control)…")
    data = _post(URL_RESOURCE, {"id": "E", "idEstacion": "E", "type": "ficha_redes_control"})
    if not data:
        return {}
    valores = (data.get("ult_res") or {}).get("valores") or []
    return {f["cod_estacion"]: f for f in valores if isinstance(f, dict) and f.get("cod_estacion")}


def normalizar_embalse(cod: str, raw: dict) -> dict:
    """
    Extrae campos relevantes de la respuesta de un embalse (tipo E).
    Usamos _extraer_valores_ult_res como primer intento (misma estructura que EAA).
    Si los campos no coinciden, probamos rutas alternativas del bloque raíz.
    Pendiente: confirmar claves exactas con DevTools en E2-40 (aún no capturado).
    """
    v = _extraer_valores_ult_res(raw)

    # Claves posibles en embalses SIRA (a confirmar con DevTools E2-40)
    # Claves reales de la tabla de embalses (confirmadas 24-11-2026): VE1, PV1, NE1, QSR
    volumen    = v.get("VE1") if v.get("VE1") is not None else (v.get("VACT") or v.get("volumen") or v.get("VOL"))
    capacidad  = v.get("VCAP") or v.get("capacidad") or v.get("CAP")
    porcentaje = v.get("PV1") if v.get("PV1") is not None else (v.get("PVOL") or v.get("porcentajeVol") or v.get("PCT"))
    nivel      = v.get("NE1") if v.get("NE1") is not None else (v.get("COTA") or v.get("nivel") or v.get("NMN"))
    q_salida   = v.get("QSR") if v.get("QSR") is not None else (v.get("QSAL") or v.get("caudal_salida") or v.get("QS"))
    if capacidad is None and _float(volumen) and _float(porcentaje):
        capacidad = round(_float(volumen) / _float(porcentaje) * 100, 2)  # SIRA no publica la capacidad: se deduce
    hora       = v.get("timestamp") or ""

    # Fallback a bloques raíz si ult_res no tiene los campos de embalse
    if volumen is None:
        alt = raw.get("embalses_volcap") or raw.get("est_res") or {}
        if isinstance(alt, dict):
            volumen    = alt.get("volumen") or alt.get("VACT")
            capacidad  = alt.get("capacidad") or alt.get("VCAP")
            porcentaje = alt.get("porcentajeVol") or alt.get("PVOL")
            nivel      = alt.get("nivel") or alt.get("COTA")
            q_salida   = alt.get("caudal_salida") or alt.get("QSAL")

    return {
        "cod":        cod,
        **ESTACIONES_OBJETIVO[cod],
        "hora_obs":      hora,
        "nivel_msnm":    _float(nivel),
        "volumen_hm3":   _float(volumen),
        "capacidad_hm3": _float(capacidad),
        "pct_capacidad": _float(porcentaje),
        "q_salida_m3s":  _float(q_salida),
        "ok":            volumen is not None,
    }


def _extraer_valores_ult_res(raw: dict) -> dict:
    """
    Extrae el primer objeto de valores del bloque ult_res.
    Estructura real de SIRA:
      {"ult_res": {"encabezado": [...], "valores": [{...}]}, "umbrales": [...]}
    Los valores están en ult_res.valores[0] (dict con claves cortas: PO, NH, PH, OX, CD, TB, TA, NO…)
    """
    ult = raw.get("ult_res") or {}
    if isinstance(ult, dict):
        valores = ult.get("valores")
        if isinstance(valores, list) and valores:
            return valores[0]
        # Fallback: a veces el dict de valores está directamente en ult_res
        return ult
    return {}


def normalizar_calidad(cod: str, raw: dict) -> dict:
    """
    Extrae parámetros de calidad de la respuesta de una EAA (SAICA).
    Claves reales confirmadas en DevTools (EAA-421, 2026-11-20):
      PO  → Fosfatos (mg/l)
      NH  → Amonio (mg/l)
      PH  → pH (udph)
      OX  → Oxígeno disuelto (mg/l)
      CD  → Conductividad (µS/cm)
      TB  → Turbidez (NTU)
      TA  → Temperatura agua (°C)
      NO  → Nitratos (mg/l)
      SAC → SAC (Abs/m)
      CLF → Clorofila (µg/l)
      FIC → Ficocianina (Cell/l)
      timestamp → hora de la observación
    """
    v = _extraer_valores_ult_res(raw)

    fosfatos = v.get("PO")
    amonio   = v.get("NH")
    ph       = v.get("PH")
    ox       = v.get("OX")
    cd       = v.get("CD")
    turbidez = v.get("TB")
    temp     = v.get("TA")
    nitratos = v.get("NO")
    sac      = v.get("SAC")
    clf      = v.get("CLF")
    fic      = v.get("FIC")
    hora     = v.get("timestamp") or ""

    fosfatos_v = _float(fosfatos)
    amonio_v   = _float(amonio)

    # Nivel de alerta (interno — NO APTA nunca es público, solo alerta interna a Antonio)
    if fosfatos_v is not None and fosfatos_v >= UMBRAL_FOSFATOS_CRITICO:
        nivel_alerta = "critico"
    elif fosfatos_v is not None and fosfatos_v >= UMBRAL_FOSFATOS_ALERTA:
        nivel_alerta = "alerta"
    elif amonio_v is not None and amonio_v >= UMBRAL_AMONIO_CRITICO:
        nivel_alerta = "critico"
    elif amonio_v is not None and amonio_v >= UMBRAL_AMONIO_ALERTA:
        nivel_alerta = "alerta"
    else:
        nivel_alerta = "normal"

    return {
        "cod":          cod,
        **ESTACIONES_OBJETIVO[cod],
        "hora_obs":          hora,
        "fosfatos_mgl":      fosfatos_v,
        "amonio_mgl":        amonio_v,
        "ph":                _float(ph),
        "oxigeno_mgl":       _float(ox),
        "conductividad_uscm":_float(cd),
        "turbidez_ntu":      _float(turbidez),
        "temp_agua_c":       _float(temp),
        "nitratos_mgl":      _float(nitratos),
        "sac_absm":          _float(sac) if sac not in (None, "") else None,
        "clorofila_ugl":     _float(clf) if clf not in (None, "") else None,
        "ficocianina_celll": _float(fic) if fic not in (None, "") else None,
        "nivel_alerta":      nivel_alerta,
        "umbral_fosfatos_alerta_mgl":  UMBRAL_FOSFATOS_ALERTA,
        "umbral_fosfatos_critico_mgl": UMBRAL_FOSFATOS_CRITICO,
        "ok": fosfatos_v is not None or amonio_v is not None,
    }


def normalizar_aforo(cod: str, raw: dict) -> dict:
    """
    Extrae caudal de la respuesta de un aforo (tipo CR).
    Usamos _extraer_valores_ult_res como primer intento (misma estructura que EAA).
    Pendiente: confirmar claves exactas con DevTools en CR2-08 (aún no capturado).
    """
    v = _extraer_valores_ult_res(raw)

    # Claves posibles en aforos SIRA (a confirmar con DevTools CR2-08)
    caudal = v.get("Q") or v.get("CAUD") or v.get("caudal") or v.get("valor")
    nivel  = v.get("N") or v.get("NIVEL") or v.get("nivel")
    hora   = v.get("timestamp") or ""

    # Fallback
    if caudal is None:
        alt = raw.get("est_res") or {}
        if isinstance(alt, dict):
            caudal = alt.get("caudal") or alt.get("Q")
            nivel  = alt.get("nivel")  or alt.get("N")

    return {
        "cod":        cod,
        **ESTACIONES_OBJETIVO[cod],
        "hora_obs":   hora,
        "caudal_m3s": _float(caudal),
        "nivel_mla":  _float(nivel),
        "ok":         caudal is not None,
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

    # 0. Obtener token de sesión SIRA (sesión anónima pública, ~12h validez)
    #    Prioridad: variable de entorno SIRA_AUTHJWT (GitHub Actions secret) →
    #              llamada automática a getProfile (token público, sin credenciales)
    token = os.environ.get("SIRA_AUTHJWT") or obtener_token_sira()
    if token:
        HEADERS["Authjwt"] = token
        log.info("Authjwt configurado en headers")
    else:
        log.warning("Sin token SIRA — las peticiones podrían ser rechazadas (401/403)")

    # 1. Consultar cada estación con el endpoint correcto según tipo
    #    EAA  → POST /calidad  (body confirmado DevTools 20-11-2026)
    #    E/CR → POST /resourceByID (fallback; E2-40 y CR2-08 dan 401 con token público)
    todos_datos: dict[str, dict] = {}
    tabla_embalses = obtener_tabla_embalses()

    for cod, meta in ESTACIONES_OBJETIVO.items():
        if meta["tipo"] == "embalse":
            fila = tabla_embalses.get(cod)
            raw = {"ult_res": {"valores": [fila]}} if fila else None
        elif meta["tipo"] == "calidad":
            # POST /calidad — estructura confirmada (lista de registros con PO/NH/TA…)
            raw = obtener_calidad_eaa(cod)
            if raw is None:
                # Fallback a resourceByID si /calidad falla
                log.info("Fallback resourceByID para %s", cod)
                raw = obtener_datos_estacion(cod, cod)
        else:
            # Aforos: resourceByID (CR2-08 da 401 con token público)
            raw = obtener_datos_estacion(cod, cod)
        if raw:
            todos_datos[cod] = raw
        time.sleep(0.5)

    embalses = []
    calidad  = []
    aforos   = []

    # 2. Normalizar cada estación con sus datos
    for cod, meta in ESTACIONES_OBJETIVO.items():
        raw = todos_datos.get(cod)

        if raw is None:
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
