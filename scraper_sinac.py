#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 3: Calidad agua de consumo (SINAC)
Scraper del Sistema de Información Nacional de Aguas de Consumo.

Consulta el portal ciudadano SINAC v2 (sinacv2.sanidad.gob.es) para los 27
municipios del área norte de Córdoba y genera sinac_calidad.json con el
semáforo de calidad de cada uno.

Flujo de scraping:
  1. GET /informacionRedes.do?codMunicipio={cod_ine}
     → Lista de redes de distribución del municipio
  2. GET /informacionCalidad.do?codRed={cod_red}  (primera red)
     → Calificación sanitaria y datos analíticos

Regla de seguridad (obligatoria):
  - El estado NO APTA (ROJO) NUNCA aparece en la vista ciudadana del panel.
  - Si se detecta un municipio NO APTA, se genera una alerta interna
    inmediata a Antonio González Caballero (vía correo + ntfy).
  - La vista ciudadana muestra "En verificación" (ámbar) hasta que el
    técnico de AGS confirme o desestime la resolución.

Fuente oficial: sinacv2.sanidad.gob.es (Ministerio de Sanidad)

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
from html.parser import HTMLParser
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

# Portal ciudadano SINAC v2
SINAC_BASE = "https://sinacv2.sanidad.gob.es/CiudadanoWeb/ciudadano"

# ── 27 municipios del norte de Córdoba ────────────────────────────────────────
MUNICIPIOS = [
    {"cod_ine": "14001", "municipio": "Añora",                  "red": "Añora"},
    {"cod_ine": "14002", "municipio": "Adamuz",                 "red": "Adamuz"},
    {"cod_ine": "14004", "municipio": "Alcaracejos",            "red": "Alcaracejos"},
    {"cod_ine": "14012", "municipio": "Belalcázar",             "red": "Belalcázar"},
    {"cod_ine": "14013", "municipio": "Belmez",                 "red": "Belmez"},
    {"cod_ine": "14021", "municipio": "Cardeña",                "red": "Cardeña"},
    {"cod_ine": "14022", "municipio": "Conquista",              "red": "Conquista"},
    {"cod_ine": "14026", "municipio": "Dos Torres",             "red": "Dos Torres"},
    {"cod_ine": "14028", "municipio": "El Viso",                "red": "El Viso"},
    {"cod_ine": "14030", "municipio": "Espiel",                 "red": "Espiel"},
    {"cod_ine": "14035", "municipio": "Fuente la Lancha",       "red": "Fuente la Lancha"},
    {"cod_ine": "14036", "municipio": "Fuente Obejuna",         "red": "F. Obejuna"},
    {"cod_ine": "14037", "municipio": "Guadalcázar",            "red": "Guadalcázar"},
    {"cod_ine": "14039", "municipio": "Guijo",                  "red": "—"},
    {"cod_ine": "14041", "municipio": "Hinojosa del Duque",     "red": "Hinojosa"},
    {"cod_ine": "14052", "municipio": "Montoro",                "red": "Montoro"},
    {"cod_ine": "14054", "municipio": "Obejo",                  "red": "Obejo"},
    {"cod_ine": "14060", "municipio": "Pedroche",               "red": "Pedroche"},
    {"cod_ine": "14063", "municipio": "Peñarroya-Pueblonuevo",  "red": "Peñarroya"},
    {"cod_ine": "14066", "municipio": "Pozoblanco",             "red": "Pozoblanco"},
    {"cod_ine": "14074", "municipio": "Santa Eufemia",          "red": "Sta. Eufemia"},
    {"cod_ine": "14084", "municipio": "Torrecampo",             "red": "Torrecampo"},
    {"cod_ine": "14085", "municipio": "Villaharta",             "red": "Villaharta"},
    {"cod_ine": "14087", "municipio": "Villanueva de Córdoba",  "red": "V. de Córdoba"},
    {"cod_ine": "14088", "municipio": "Villanueva del Duque",   "red": "—"},
    {"cod_ine": "14090", "municipio": "Villaralto",             "red": "Villaralto"},
    {"cod_ine": "14092", "municipio": "Villaviciosa de Córdoba","red": "Villaviciosa"},
]


# ── Cliente HTTP ───────────────────────────────────────────────────────────────

def _get_html(url: str, timeout: int = 25) -> str | None:
    """Descarga HTML del portal SINAC v2."""
    ctx = ssl._create_unverified_context()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; AQUASALUD-NORTE/2.0; "
            "+https://github.com/herzonekg-hue/Pulso-Hidrico-Cordoba-)"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            charset = "utf-8"
            ct = r.headers.get("Content-Type", "")
            m = re.search(r"charset=([\w-]+)", ct, re.I)
            if m:
                charset = m.group(1)
            return r.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        log.warning("HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.warning("Error — %s — %s", url, e)
        return None


# ── Parsers HTML ───────────────────────────────────────────────────────────────

class LinkExtractor(HTMLParser):
    """Extrae href de <a> que contengan un patrón dado."""
    def __init__(self, pattern: str):
        super().__init__()
        self.pattern = pattern
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href and self.pattern in href:
                self.links.append(href)


class TextExtractor(HTMLParser):
    """Extrae todo el texto visible de un HTML."""
    def __init__(self):
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self._text.append(t)

    @property
    def text(self) -> str:
        return " ".join(self._text)


def extraer_links(html: str, patron: str) -> list[str]:
    p = LinkExtractor(patron)
    p.feed(html)
    return p.links


def extraer_texto(html: str) -> str:
    p = TextExtractor()
    p.feed(html)
    return p.text


# ── Lógica SINAC ──────────────────────────────────────────────────────────────

def obtener_redes_municipio(cod_ine: str) -> list[str]:
    """
    Paso 1: obtiene las URLs de las redes de distribución de un municipio.
    Devuelve lista de URLs absolutas de cada red.
    """
    url = f"{SINAC_BASE}/informacionRedes.do?codMunicipio={cod_ine}"
    html = _get_html(url)
    if not html:
        return []

    # Buscar enlaces a la calidad de cada red
    # El portal usa URLs como: informacionCalidad.do?codRed=XXXX
    # o bien informacionZona.do?codZona=XXXX
    links_red = extraer_links(html, "informacionCalidad.do")
    links_zona = extraer_links(html, "informacionZona.do")
    links = links_red + links_zona

    # También puede haber enlaces directos a la zona via informacionRedes
    if not links:
        # Fallback: buscar cualquier enlace con codRed o codZona
        links = extraer_links(html, "codRed=") + extraer_links(html, "codZona=")

    # Convertir a URLs absolutas
    result = []
    for lnk in links:
        if lnk.startswith("http"):
            result.append(lnk)
        elif lnk.startswith("/"):
            result.append("https://sinacv2.sanidad.gob.es" + lnk)
        else:
            result.append(f"{SINAC_BASE}/{lnk}")

    return result


def obtener_calificacion_red(url_red: str) -> dict:
    """
    Paso 2: obtiene la calificación sanitaria de una red/zona de abastecimiento.
    Devuelve dict con calificacion, resolucion_no_apta, fecha_analisis.
    """
    html = _get_html(url_red)
    if not html:
        return {"calificacion": None, "resolucion_no_apta": False, "fecha_analisis": None}

    texto = extraer_texto(html).lower()

    # Detectar resolución de no aptitud (prioridad máxima)
    no_apta = any(p in texto for p in [
        "no apta", "no es apta", "resolución de no aptitud",
        "resolucion de no aptitud", "agua no apta",
        "incumplimiento", "suspensión del suministro",
    ])

    # Detectar calificación positiva
    apta = any(p in texto for p in [
        "apta para el consumo", "agua apta", "calificación: apta",
        "calificacion: apta", "cumple", "conforme",
    ]) and not no_apta

    # Extraer fecha del último análisis
    fecha_analisis = None
    # Buscar patrones de fecha: "dd/mm/yyyy", "dd-mm-yyyy", "mes año"
    m_fecha = re.search(
        r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', extraer_texto(html)
    )
    if m_fecha:
        try:
            dia, mes_n, anio = int(m_fecha.group(1)), int(m_fecha.group(2)), int(m_fecha.group(3))
            meses = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
            fecha_analisis = f"{meses[mes_n-1]} {anio}"
        except Exception:
            fecha_analisis = m_fecha.group(0)

    if no_apta:
        calificacion = "No apta"
    elif apta:
        calificacion = "Apta"
    else:
        calificacion = "Sin datos"

    return {
        "calificacion": calificacion,
        "resolucion_no_apta": no_apta,
        "fecha_analisis": fecha_analisis or "—",
    }


def consultar_municipio(cod_ine: str) -> dict:
    """
    Consulta SINAC v2 para un municipio: obtiene redes y calificación sanitaria.
    """
    # Paso 1: obtener redes del municipio
    redes = obtener_redes_municipio(cod_ine)
    time.sleep(0.6)   # respetar el servidor del Ministerio

    if not redes:
        log.debug("    Sin redes para %s", cod_ine)
        return {
            "calificacion": "Sin datos",
            "resolucion_no_apta": False,
            "fecha_analisis": "—",
            "fuente": "SINAC v2 — sin redes registradas",
        }

    # Paso 2: consultar la primera red (la principal)
    resultado = obtener_calificacion_red(redes[0])
    time.sleep(0.6)

    resultado["fuente"] = "SINAC v2 (sinacv2.sanidad.gob.es)"
    resultado["n_redes"] = len(redes)
    return resultado


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
    if cal == "apta":
        return {"color": "VERDE", "icono": "🟢", "texto": "Apta"}
    if cal in ("", "sin datos", "no disponible", "nd", "none"):
        return {"color": "GRIS",  "icono": "⚫", "texto": "Sin datos"}
    return {"color": "GRIS", "icono": "⚫", "texto": "Sin datos"}


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
        "  1. Verificar resolución en SINAC v2 (sinacv2.sanidad.gob.es)\n"
        "  2. Contactar con Salud Ambiental AGS Norte de Córdoba\n"
        "  3. Coordinar comunicación oficial con el Ayuntamiento\n\n"
        "NOTA: La vista ciudadana del panel muestra 'En verificación' (ámbar)\n"
        "hasta que el técnico actualice el estado.\n\n"
        "Este mensaje es CONFIDENCIAL — uso exclusivo AGS Norte de Córdoba."
    )

    log.warning("ALERTA INTERNA NO APTA: %s", nombres)

    # Canal 1: ntfy (si está configurado)
    ntfy_topic  = cfg.get("ntfy_topic", "").strip()
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
    # Cargar config (opcional — solo se necesita para alertas por correo/ntfy)
    # En GitHub Actions no existe config.json; el scraper funciona igual
    # pero sin canal de alerta (el log queda en el workflow)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        log.info("config.json cargado — alertas activadas")
    else:
        cfg = {}
        log.info("config.json no encontrado — funcionando sin alertas externas (modo CI)")

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

    log.info("Consultando SINAC v2 para %d municipios del norte de Córdoba…", len(MUNICIPIOS))

    municipios_out  = []
    municipios_rojo = []
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
            "municipio":          muni["municipio"],
            "cod_ine":            muni["cod_ine"],
            "red":                muni["red"],
            "calificacion":       resultado["calificacion"],
            "resolucion_no_apta": resultado["resolucion_no_apta"],
            "fecha_analisis":     resultado["fecha_analisis"],
            "semaforo":           semaforo,
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
            "fuente":                 "SINAC v2 — Portal ciudadano Ministerio de Sanidad (sinacv2.sanidad.gob.es)",
            "nota_legal": (
                "Datos de calidad del agua de consumo. "
                "Publicación sujeta a autorización de AGS Norte de Córdoba y Salud Ambiental."
            ),
            "logica_semaforo": (
                "VERDE=apta, ROJO=resolución no aptitud vigente (solo vista técnica), "
                "GRIS=sin datos o sin resolución definitiva."
            ),
            "municipios_total": len(municipios_out),
        },
        "resumen":    resumen,
        "municipios": municipios_out,
    }

    out = SALIDA_DIR / "sinac_calidad.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    tmp.replace(out)

    log.info(
        "sinac_calidad.json escrito → %s | VERDE:%d ROJO:%d GRIS:%d",
        out, resumen["VERDE"], resumen["ROJO"], resumen["GRIS"]
    )

    if resumen["ROJO"] > 0:
        log.warning("HAY RESOLUCIONES NO APTA — alerta interna enviada")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
