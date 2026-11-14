#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 3: Calidad agua de consumo (SINAC)
Scraper del Sistema de Información Nacional de Aguas de Consumo.

Consulta el portal ciudadano de SINAC (sinac.es) para los 27 municipios
del área norte de Córdoba y genera sinac_calidad.json con el semáforo
de calidad de cada uno.

Regla de seguridad (obligatoria):
  - El estado NO APTA (ROJO) NUNCA aparece en la vista ciudadana del panel.
  - Si se detecta un municipio NO APTA, se genera una alerta interna
    inmediata a Antonio González Caballero (vía correo + ntfy).
  - La vista ciudadana muestra "En verificación" (ámbar) hasta que el
    técnico de AGS confirme o desestime la resolución.

Fuente oficial: sinac.sanidad.gob.es (Ministerio de Sanidad)
Aviso: los datos de SINAC no pueden publicarse sin autorización de la
       Dirección del AGS y Salud Ambiental (ver config.json).

Escribe: data/sinac_calidad.json (leído por index.html)
         sinac_alertas.log       (registro de alertas internas)

Autor: Proyecto Hídrico — AGS Norte de Córdoba
"""

import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

# ── Configuración ─────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SALIDA_DIR  = Path(os.environ.get("HIDRICO_SALIDA", SCRIPT_DIR / "data"))
LOG_PATH    = SCRIPT_DIR / "sinac_alertas.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sinac] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ]
)
log = logging.getLogger("sinac")

# Base URL del portal ciudadano de SINAC
# El portal devuelve JSON con las resoluciones de cada municipio
SINAC_BASE = "https://sinac.sanidad.gob.es/SINAC/rest"
# Código de provincia de Córdoba en SINAC
COD_PROVINCIA = "14"

# ── 27 municipios del norte de Córdoba ────────────────────────────────────────
# (código INE, nombre, red de abastecimiento principal)
MUNICIPIOS = [
    {"cod_ine": "14002", "municipio": "Adamuz",                "red": "Adamuz"},
    {"cod_ine": "14004", "municipio": "Alcaracejos",           "red": "Alcaracejos"},
    {"cod_ine": "14012", "municipio": "Belalcázar",            "red": "Belalcázar"},
    {"cod_ine": "14013", "municipio": "Belmez",                "red": "Belmez"},
    {"cod_ine": "14021", "municipio": "Cardeña",               "red": "Cardeña"},
    {"cod_ine": "14022", "municipio": "Conquista",             "red": "Conquista"},
    {"cod_ine": "14026", "municipio": "Dos Torres",            "red": "Dos Torres"},
    {"cod_ine": "14028", "municipio": "El Viso",               "red": "El Viso"},
    {"cod_ine": "14030", "municipio": "Espiel",                "red": "Espiel"},
    {"cod_ine": "14035", "municipio": "Fuente la Lancha",      "red": "Fuente la Lancha"},
    {"cod_ine": "14036", "municipio": "Fuente Obejuna",        "red": "F. Obejuna"},
    {"cod_ine": "14037", "municipio": "Guadalcázar",           "red": "Guadalcázar"},
    {"cod_ine": "14039", "municipio": "Guijo",                 "red": "—"},
    {"cod_ine": "14041", "municipio": "Hinojosa del Duque",    "red": "Hinojosa"},
    {"cod_ine": "14052", "municipio": "Montoro",               "red": "Montoro"},
    {"cod_ine": "14054", "municipio": "Obejo",                 "red": "Obejo"},
    {"cod_ine": "14060", "municipio": "Pedroche",              "red": "Pedroche"},
    {"cod_ine": "14063", "municipio": "Peñarroya-Pueblonuevo", "red": "Peñarroya"},
    {"cod_ine": "14066", "municipio": "Pozoblanco",            "red": "Pozoblanco"},
    {"cod_ine": "14074", "municipio": "Santa Eufemia",         "red": "Sta. Eufemia"},
    {"cod_ine": "14084", "municipio": "Torrecampo",            "red": "Torrecampo"},
    {"cod_ine": "14085", "municipio": "Villaharta",            "red": "Villaharta"},
    {"cod_ine": "14087", "municipio": "Villanueva de Córdoba", "red": "V. de Córdoba"},
    {"cod_ine": "14088", "municipio": "Villanueva del Duque",  "red": "—"},
    {"cod_ine": "14090", "municipio": "Villaralto",            "red": "Villaralto"},
    {"cod_ine": "14092", "municipio": "Villaviciosa de Córdoba","red": "Villaviciosa"},
    {"cod_ine": "14052", "municipio": "Añora",                 "red": "Añora"},
]
# Nota: Añora tiene cod_ine 14001, corregido abajo
MUNICIPIOS[-1]["cod_ine"] = "14001"

# ── Semáforo ──────────────────────────────────────────────────────────────────

def calcular_semaforo(calificacion: str, resolucion_no_apta: bool) -> dict:
    """
    Convierte la calificación SINAC en semáforo.
    ROJO solo se devuelve si hay resolución de no aptitud vigente.
    La regla de ocultación al ciudadano se aplica en index.html (aplicarReglaRojo).
    """
    if resolucion_no_apta:
        return {"color": "ROJO", "icono": "🔴", "texto": "No apta"}
    cal = (calificacion or "").lower()
    if "apta" in cal and "no" not in cal:
        return {"color": "VERDE", "icono": "🟢", "texto": "Apta"}
    if cal in ("", "sin datos", "no disponible", "nd"):
        return {"color": "GRIS",  "icono": "⚫", "texto": "Sin datos"}
    # Calificación presente pero no "apta" definitiva → gris cautelar
    return {"color": "GRIS", "icono": "⚫", "texto": "Sin datos"}


# ── Cliente SINAC ─────────────────────────────────────────────────────────────

def _get_sinac(url: str, timeout: int = 25):
    ctx = ssl._create_unverified_context()
    headers = {
        "User-Agent": "AQUASALUD-NORTE/1.0 (AGS Norte de Córdoba, uso sanitario interno)",
        "Accept":     "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        log.warning("SINAC HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.warning("SINAC error — %s — %s", url, e)
        return None


def consultar_municipio(cod_ine: str) -> dict:
    """
    Consulta el estado de calidad de un municipio en SINAC.
    Devuelve dict con calificacion y resolucion_no_apta.
    """
    # Endpoint principal: municipios con resoluciones vigentes
    url = f"{SINAC_BASE}/municipio/{COD_PROVINCIA}/{cod_ine}"
    data = _get_sinac(url)
    time.sleep(0.4)   # respetar el servidor del Ministerio

    if not data:
        # Fallback: búsqueda por código INE en el endpoint general
        url2 = f"{SINAC_BASE}/municipioCalidad/{cod_ine}"
        data = _get_sinac(url2)
        time.sleep(0.4)

    if not data:
        return {"calificacion": None, "resolucion_no_apta": False, "fecha_analisis": None, "fuente": "sin_datos"}

    # La respuesta puede variar según la versión de la API de SINAC
    # Intentamos extraer los campos más comunes
    calificacion      = None
    resolucion        = False
    fecha_analisis    = None

    # Formato 1: lista de zonas de abastecimiento
    if isinstance(data, list) and len(data) > 0:
        zona = data[0]
        calificacion   = zona.get("calificacion") or zona.get("resultadoCalificacion")
        resolucion     = bool(zona.get("resolucionNoAptitud") or zona.get("noApta") or False)
        fecha_analisis = zona.get("fechaUltimoAnalisis") or zona.get("fechaAnalisis")
    # Formato 2: objeto único
    elif isinstance(data, dict):
        calificacion   = data.get("calificacion") or data.get("resultadoCalificacion")
        resolucion     = bool(data.get("resolucionNoAptitud") or data.get("noApta") or False)
        fecha_analisis = data.get("fechaUltimoAnalisis") or data.get("fechaAnalisis")

    # Normalizar fecha
    if fecha_analisis:
        # Convertir "2026-10-15T00:00:00" → "oct 2026"
        try:
            dt_obj = datetime.fromisoformat(str(fecha_analisis).replace("Z", ""))
            meses = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
            fecha_analisis = f"{meses[dt_obj.month-1]} {dt_obj.year}"
        except Exception:
            fecha_analisis = str(fecha_analisis)[:7]   # "2026-10"

    return {
        "calificacion":      calificacion or "Sin datos",
        "resolucion_no_apta": resolucion,
        "fecha_analisis":    fecha_analisis or "—",
        "fuente":            "SINAC ciudadano (sinac.sanidad.gob.es)",
    }


# ── Alerta interna NO APTA ────────────────────────────────────────────────────

def enviar_alerta_interna(municipios_rojo: list, cfg: dict):
    """
    Envía alerta interna a Antonio cuando hay municipios con resolución NO APTA.
    NUNCA se publica al exterior — es comunicación interna AGS.
    """
    if not municipios_rojo:
        return

    nombres = ", ".join(m["municipio"] for m in municipios_rojo)
    titulo  = f"🔒 SINAC INTERNO — {len(municipios_rojo)} resolución(es) NO APTA: {nombres}"
    cuerpo  = (
        f"AVISO INTERNO — AQUASALUD NORTE — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Se han detectado {len(municipios_rojo)} municipio(s) con resolución de no aptitud "
        f"vigente en SINAC:\n\n"
    )
    for m in municipios_rojo:
        cuerpo += f"  • {m['municipio']} (red: {m.get('red','—')}, análisis: {m.get('fecha_analisis','—')})\n"
    cuerpo += (
        "\nACCIÓN REQUERIDA:\n"
        "  1. Verificar resolución en SINAC ciudadano (sinac.sanidad.gob.es)\n"
        "  2. Contactar con Salud Ambiental AGS Norte de Córdoba\n"
        "  3. Coordinar comunicación oficial con el Ayuntamiento\n\n"
        "NOTA: La vista ciudadana del panel muestra 'En verificación' (ámbar)\n"
        "hasta que el técnico actualice el estado.\n\n"
        "Este mensaje es CONFIDENCIAL — uso exclusivo AGS Norte de Córdoba."
    )

    log.warning("ALERTA INTERNA NO APTA: %s", nombres)

    # Canal 1: ntfy (si está configurado)
    ntfy_topic = cfg.get("ntfy_topic", "").strip()
    ntfy_server = cfg.get("ntfy_server", "https://ntfy.sh").strip()
    if ntfy_topic:
        try:
            req = urllib.request.Request(
                f"{ntfy_server.rstrip('/')}/{ntfy_topic}",
                data=cuerpo.encode("utf-8"),
                headers={
                    "Title":    titulo.encode("utf-8"),
                    "Priority": "urgent",
                    "Tags":     "warning,droplet",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
                log.info("Alerta ntfy enviada: %s", r.status)
        except Exception as e:
            log.error("Error enviando alerta ntfy: %s", e)

    # Canal 2: correo (si está configurado)
    smtp_server  = cfg.get("correo_smtp", "").strip()
    smtp_usuario = cfg.get("correo_usuario", "").strip()
    smtp_clave   = cfg.get("correo_password", "").strip()
    destino      = cfg.get("correo_destino", "").strip()
    if smtp_server and destino:
        try:
            msg = EmailMessage()
            msg["Subject"] = titulo
            msg["From"]    = smtp_usuario or destino
            msg["To"]      = destino
            msg.set_content(cuerpo)
            with smtplib.SMTP(smtp_server, 587, timeout=20) as s:
                s.starttls()
                if smtp_usuario and smtp_clave:
                    s.login(smtp_usuario, smtp_clave)
                s.send_message(msg)
            log.info("Alerta correo enviada a %s", destino)
        except Exception as e:
            log.error("Error enviando alerta correo: %s", e)
    else:
        log.info("Canal correo no configurado (correo_smtp/correo_destino en config.json)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Cargar config
    if not CONFIG_PATH.exists():
        log.error("No existe config.json")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    # Modo fixture (pruebas sin red)
    fixture_dir = os.environ.get("HIDRICO_FIXTURE_DIR")
    if fixture_dir:
        fixture = Path(fixture_dir) / "sinac_fixture.json"
        if fixture.exists():
            log.info("Modo fixture — %s", fixture)
            with open(fixture, encoding="utf-8") as f:
                salida = json.load(f)
            out = SALIDA_DIR / "sinac_calidad.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(salida, f, ensure_ascii=False, indent=2)
            log.info("sinac_calidad.json escrito desde fixture → %s", out)
            return

    ahora = datetime.now(timezone.utc)
    meses = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible = f"{ahora.day} {meses[ahora.month-1]} {ahora.year} {ahora.strftime('%H:%M')}"

    log.info("Consultando SINAC para %d municipios del norte de Córdoba…", len(MUNICIPIOS))

    municipios_out   = []
    municipios_rojo  = []
    resumen = {"VERDE": 0, "ROJO": 0, "GRIS": 0}

    for muni in MUNICIPIOS:
        log.info("  → %s (%s)", muni["municipio"], muni["cod_ine"])
        resultado = consultar_municipio(muni["cod_ine"])

        semaforo = calcular_semaforo(
            resultado["calificacion"],
            resultado["resolucion_no_apta"]
        )
        resumen[semaforo["color"]] = resumen.get(semaforo["color"], 0) + 1

        entrada = {
            "municipio":        muni["municipio"],
            "cod_ine":          muni["cod_ine"],
            "red":              muni["red"],
            "calificacion":     resultado["calificacion"],
            "resolucion_no_apta": resultado["resolucion_no_apta"],
            "fecha_analisis":   resultado["fecha_analisis"],
            "semaforo":         semaforo,
        }
        municipios_out.append(entrada)

        if semaforo["color"] == "ROJO":
            municipios_rojo.append({**muni, "fecha_analisis": resultado["fecha_analisis"]})
            log.warning("⚠️  NO APTA detectada: %s", muni["municipio"])

    # Alerta interna si hay municipios ROJO
    if municipios_rojo:
        enviar_alerta_interna(municipios_rojo, cfg)

    salida = {
        "meta": {
            "generado":               ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fecha_consulta_legible": fecha_legible,
            "fuente":                 "SINAC ciudadano (sinac.sanidad.gob.es) — Ministerio de Sanidad",
            "nota_legal":             (
                "Datos de calidad del agua de consumo. "
                "Publicación sujeta a autorización de AGS Norte de Córdoba y Salud Ambiental."
            ),
            "logica_semaforo":        (
                "VERDE=apta, ROJO=resolución no aptitud vigente (solo vista técnica), "
                "GRIS=sin datos o sin resolución definitiva."
            ),
            "municipios_total":       len(municipios_out),
        },
        "resumen":    resumen,
        "municipios": municipios_out,
    }

    out = SALIDA_DIR / "sinac_calidad.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica (evitar que el panel lea un fichero a medias)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    tmp.replace(out)

    log.info(
        "sinac_calidad.json escrito → %s | VERDE:%d ROJO:%d GRIS:%d",
        out, resumen["VERDE"], resumen["ROJO"], resumen["GRIS"]
    )

    # Exit code para GitHub Actions / cron
    if resumen["ROJO"] > 0:
        log.warning("HAY RESOLUCIONES NO APTA — alerta interna enviada")
        sys.exit(2)   # señal para el workflow

    sys.exit(0)


if __name__ == "__main__":
    main()
