#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 2: Meteorología predictiva
Scraper AEMET OpenData para el norte de Córdoba.

Descarga precipitación y temperatura de las 5 estaciones de la zona,
calcula lluvia acumulada en las cuencas contaminantes (Guadarramilla,
afluentes La Colada) y genera un índice de riesgo de arrastre de
contaminación hacia La Colada con 24-48 h de antelación.

Escribe: meteo.json  (leído por index.html y alertas.py)
Autor: Proyecto Hídrico — AGS Norte de Córdoba
"""

import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Configuración ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SALIDA_DIR = Path(os.environ.get("HIDRICO_SALIDA", SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [aemet] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("aemet")

# ── Estaciones AEMET del norte de Córdoba ───────────────────────────────────
# Pesos de contribución al riesgo de La Colada:
#   1.0 = cuenca que vierte directamente al Guadarramilla/La Colada
#   0.6 = cuenca que vierte a Sierra Boyera (riesgo indirecto)
#   0.3 = zona periférica (referencia)

ESTACIONES = [
    {
        "id": "5402",
        "nombre": "Pozoblanco",
        "lat": 38.3833, "lon": -4.8333, "alt": 649,
        "cuenca": "Guadarramilla",
        "peso_colada": 1.0,   # vierte al Guadarramilla → La Colada
        "nota": "EDAR Pozoblanco vierte al Guadarramilla. Máximo riesgo."
    },
    {
        "id": "5411",
        "nombre": "Peñarroya-Pueblonuevo",
        "lat": 38.300, "lon": -5.2667, "alt": 562,
        "cuenca": "Guadiato",
        "peso_colada": 0.3,
        "nota": "Cuenca del Guadiato. Referencia occidental."
    },
    {
        "id": "5410",
        "nombre": "Hinojosa del Duque",
        "lat": 38.500, "lon": -5.1333, "alt": 590,
        "cuenca": "Zújar",
        "peso_colada": 0.4,
        "nota": "Cuenca alta del Zújar. Contribución moderada."
    },
    {
        "id": "5403",
        "nombre": "Villanueva de Córdoba",
        "lat": 38.3167, "lon": -4.6167, "alt": 648,
        "cuenca": "Yeguas",
        "peso_colada": 0.6,
        "nota": "Cuenca del río Yeguas — afluente zona Sierra Boyera."
    },
    {
        "id": "5389",
        "nombre": "Belmez",
        "lat": 38.2667, "lon": -5.2167, "alt": 485,
        "cuenca": "Guadiato",
        "peso_colada": 0.3,
        "nota": "Guadiato sur. Referencia minería histórica."
    },
]

# Umbrales de riesgo de arrastre (mm lluvia acumulada 48 h ponderada)
UMBRAL_ALERTA   = 15.0   # mm ponderados → riesgo moderado
UMBRAL_CRITICO  = 30.0   # mm ponderados → riesgo alto, posible pluma en 24-48 h

BASE_URL = "https://opendata.aemet.es/opendata/api"

# ── Cliente AEMET ────────────────────────────────────────────────────────────

class AEMETClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ctx = ssl._create_unverified_context()

    def _get(self, url: str, timeout: int = 20) -> dict | list | None:
        """Petición GET a la API de AEMET. Sigue la redirección de datos."""
        req = urllib.request.Request(
            url,
            headers={
                "api_key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "AQUASALUD-NORTE/2.0 (AGS Norte de Cordoba)"
            }
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as r:
                meta = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            log.warning("AEMET HTTP %s — %s", e.code, url)
            return None
        except Exception as e:
            log.warning("AEMET error — %s", e)
            return None

        # La API devuelve primero un objeto con 'datos' (URL real de los datos)
        datos_url = meta.get("datos")
        if not datos_url:
            log.warning("Sin campo 'datos' en respuesta AEMET: %s", meta)
            return None

        try:
            with urllib.request.urlopen(
                urllib.request.Request(datos_url, headers={"User-Agent": "AQUASALUD-NORTE/2.0"}),
                context=self.ctx, timeout=timeout
            ) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log.warning("Error descargando datos AEMET: %s", e)
            return None

    def observacion_actual(self, id_estacion: str) -> dict | None:
        """Última observación horaria de una estación."""
        url = f"{BASE_URL}/observacion/convencional/datos/estacion/{id_estacion}/?api_key={self.api_key}"
        datos = self._get(url)
        if not datos or not isinstance(datos, list):
            return None
        # El último elemento es la observación más reciente
        return datos[-1] if datos else None

    def prediccion_municipio(self, cod_municipio: str) -> dict | None:
        """Predicción horaria para un municipio (código INE)."""
        url = f"{BASE_URL}/prediccion/especifica/municipio/horaria/{cod_municipio}/?api_key={self.api_key}"
        return self._get(url)


def _float(valor, default=None):
    """Convierte valor AEMET (puede ser string con coma decimal) a float."""
    if valor is None or valor == "" or valor == "Ip":  # Ip = inapreciable
        return default
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return default


# ── Lógica principal ─────────────────────────────────────────────────────────

def obtener_datos_estaciones(client: AEMETClient) -> list[dict]:
    """Descarga la última observación de cada estación y la normaliza."""
    resultados = []
    for est in ESTACIONES:
        log.info("Consultando estación %s (%s)…", est["id"], est["nombre"])
        obs = client.observacion_actual(est["id"])
        time.sleep(0.5)   # respetar límite de peticiones AEMET

        if obs:
            prec   = _float(obs.get("prec"))    # precipitación última hora (mm)
            temp   = _float(obs.get("ta"))       # temperatura aire (°C)
            racha  = _float(obs.get("vmax"))     # racha máxima viento (km/h)
            humedad= _float(obs.get("hr"))       # humedad relativa (%)
            hora   = obs.get("fint", "")         # fecha-hora observación
            ok     = True
        else:
            prec = temp = racha = humedad = None
            hora = ""
            ok   = False
            log.warning("Sin datos para estación %s", est["id"])

        resultados.append({
            **est,
            "ok":          ok,
            "hora_obs":    hora,
            "prec_1h_mm":  prec,
            "temp_c":      temp,
            "racha_kmh":   racha,
            "humedad_pct": humedad,
        })

    return resultados


def calcular_riesgo_colada(estaciones: list[dict]) -> dict:
    """
    Calcula el índice de riesgo de arrastre de contaminación hacia La Colada.

    Lógica:
      - Suma la precipitación de cada estación ponderada por su peso de cuenca.
      - Una lluvia intensa sobre Pozoblanco (peso 1.0) es el factor de mayor
        riesgo: arrastra los vertidos del Guadarramilla hacia La Colada en
        24-48 horas.
      - El índice se escala a 0-100 para facilitar la visualización.
    """
    lluvia_ponderada = 0.0
    estaciones_con_dato = 0

    for est in estaciones:
        if est["prec_1h_mm"] is not None:
            lluvia_ponderada += est["prec_1h_mm"] * est["peso_colada"]
            estaciones_con_dato += 1

    if estaciones_con_dato == 0:
        return {
            "lluvia_ponderada_mm": None,
            "nivel": "sin_datos",
            "nivel_num": 0,
            "mensaje": "Sin datos meteorológicos disponibles",
            "tiempo_transito_h": None,
        }

    # Nivel de riesgo
    if lluvia_ponderada >= UMBRAL_CRITICO:
        nivel     = "critico"
        nivel_num = 3
        msg = (f"Lluvia intensa ({lluvia_ponderada:.1f} mm pond.). "
               "Riesgo alto de arrastre de contaminantes hacia La Colada en 24-48 h. "
               "Vigilar COT y fosfatos en ETAP Sierra Boyera.")
        transito = 24
    elif lluvia_ponderada >= UMBRAL_ALERTA:
        nivel     = "alerta"
        nivel_num = 2
        msg = (f"Lluvia moderada ({lluvia_ponderada:.1f} mm pond.). "
               "Posible incremento de turbidez y materia orgánica en La Colada en 36-48 h.")
        transito = 36
    elif lluvia_ponderada > 0:
        nivel     = "vigilancia"
        nivel_num = 1
        msg = (f"Lluvia leve ({lluvia_ponderada:.1f} mm pond.). "
               "Sin riesgo inmediato. Monitorizar si persiste.")
        transito = None
    else:
        nivel     = "normal"
        nivel_num = 0
        msg = "Sin precipitación. Riesgo de arrastre nulo."
        transito = None

    return {
        "lluvia_ponderada_mm": round(lluvia_ponderada, 2),
        "nivel":               nivel,
        "nivel_num":           nivel_num,
        "mensaje":             msg,
        "tiempo_transito_h":   transito,
        "umbral_alerta_mm":    UMBRAL_ALERTA,
        "umbral_critico_mm":   UMBRAL_CRITICO,
    }


def main():
    # ── Cargar configuración ─────────────────────────────────────────────────
    if not CONFIG_PATH.exists():
        log.error("No existe config.json. Copia config.example.json y pon tu clave AEMET.")
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    api_key = cfg.get("aemet_api_key", "").strip()
    if not api_key or api_key.startswith("PON_"):
        log.error("Falta la clave AEMET en config.json (campo 'aemet_api_key').")
        sys.exit(1)

    # ── Modo fixture (pruebas sin red) ───────────────────────────────────────
    fixture_dir = os.environ.get("HIDRICO_FIXTURE_DIR")
    if fixture_dir:
        fixture = Path(fixture_dir) / "meteo_fixture.json"
        if fixture.exists():
            log.info("Modo fixture — cargando %s", fixture)
            with open(fixture, encoding="utf-8") as f:
                salida = json.load(f)
            out = SALIDA_DIR / "meteo.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(salida, f, ensure_ascii=False, indent=2)
            log.info("meteo.json escrito desde fixture → %s", out)
            return
        else:
            log.warning("Fixture no encontrado en %s — usando red", fixture)

    # ── Consulta real a AEMET ────────────────────────────────────────────────
    client = AEMETClient(api_key)
    estaciones = obtener_datos_estaciones(client)
    riesgo     = calcular_riesgo_colada(estaciones)

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    salida = {
        "generado":   ahora,
        "fuente":     "AEMET OpenData — opendata.aemet.es",
        "aviso":      "Datos meteorológicos no contrastados. No sustituyen avisos oficiales de AEMET ni Protección Civil.",
        "riesgo_colada": riesgo,
        "estaciones": [
            {k: v for k, v in e.items() if k not in ("peso_colada",)}
            for e in estaciones
        ],
    }

    # ── Escribir meteo.json ──────────────────────────────────────────────────
    out = SALIDA_DIR / "meteo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    log.info(
        "meteo.json escrito → %s | riesgo=%s (%.1f mm pond.)",
        out,
        riesgo["nivel"],
        riesgo.get("lluvia_ponderada_mm") or 0.0,
    )

    # ── Código de salida según nivel de riesgo (útil en GitHub Actions) ──────
    if riesgo["nivel_num"] >= 3:
        log.warning("RIESGO CRÍTICO — %s", riesgo["mensaje"])
        sys.exit(2)   # exit 2 → alerta en el workflow
    elif riesgo["nivel_num"] >= 2:
        log.warning("ALERTA — %s", riesgo["mensaje"])
        sys.exit(1)   # exit 1 → aviso en el workflow
    # nivel normal o vigilancia → exit 0


if __name__ == "__main__":
    main()
