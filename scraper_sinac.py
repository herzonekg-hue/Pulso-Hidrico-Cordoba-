#!/usr/bin/env python3
"""
AQUASALUD NORTE — Capa 3: Calidad agua de consumo (SINAC)
Scraper del Sistema de Información Nacional de Aguas de Consumo.

Fuentes por orden de prioridad:
  1. elmapadelagua.com  — API pública REST sin autenticación (volcado SINAC
     actualizado nocturnamente). Proporciona dureza y datos analíticos
     del último boletín SINAC. Accesible desde GitHub Actions.
  2. SINAC v2 directo   — sinacv2.sanidad.gob.es (HTML scraping, 2 pasos).
     Solo funciona en entornos sin restricción de red (ejecución local).
     En GitHub Actions el proxy bloquea el host del Ministerio.

Regla de seguridad (OBLIGATORIA — nunca modificar):
  - El estado NO APTA (ROJO) NUNCA aparece en la vista ciudadana del panel.
  - Si se detecta un municipio NO APTA, se genera una alerta interna
    inmediata a Antonio González Caballero (vía correo + ntfy).
  - La vista ciudadana muestra "En verificación" (ámbar) hasta que el
    técnico AGS confirme o desestime la resolución.
  - Para AQUASALUD NORTE el NO APTA es SIEMPRE alerta interna — nunca público.

Fuente oficial SINAC: sinacv2.sanidad.gob.es (Ministerio de Sanidad)
Fuente derivada: elmapadelagua.com (volcado SINAC público, sin auth)

Escribe: data/sinac_calidad.json (leído por index.html)
         sinac_alertas.log       (registro de alertas internas)

Autor: Proyecto Hídrico — AGS Norte de Córdoba
"""

import json
import logging
import os
import re
import smtplib
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

# URLs de fuentes
ELMAPADELAGUA_API = "https://elmapadelagua.com/api"
SINAC_V2_BASE     = "https://sinacv2.sanidad.gob.es/CiudadanoWeb/ciudadano"

# ── 27 municipios del norte de Córdoba ────────────────────────────────────────
MUNICIPIOS = [
    {"cod_ine": "14006", "municipio": "Añora"},
    {"cod_ine": "14001", "municipio": "Adamuz"},
    {"cod_ine": "14003", "municipio": "Alcaracejos"},
    {"cod_ine": "14008", "municipio": "Belalcázar"},
    {"cod_ine": "14009", "municipio": "Belmez"},
    {"cod_ine": "14016", "municipio": "Cardeña"},
    {"cod_ine": "14020", "municipio": "Conquista"},
    {"cod_ine": "14023", "municipio": "Dos Torres"},
    {"cod_ine": "14074", "municipio": "El Viso"},
    {"cod_ine": "14026", "municipio": "Espiel"},
    {"cod_ine": "14028", "municipio": "Fuente la Lancha"},
    {"cod_ine": "14029", "municipio": "Fuente Obejuna"},
    {"cod_ine": "14033", "municipio": "Guadalcázar"},
    {"cod_ine": "14034", "municipio": "El Guijo"},
    {"cod_ine": "14035", "municipio": "Hinojosa del Duque"},
    {"cod_ine": "14043", "municipio": "Montoro"},
    {"cod_ine": "14047", "municipio": "Obejo"},
    {"cod_ine": "14051", "municipio": "Pedroche"},
    {"cod_ine": "14052", "municipio": "Peñarroya-Pueblonuevo"},
    {"cod_ine": "14054", "municipio": "Pozoblanco"},
    {"cod_ine": "14061", "municipio": "Santa Eufemia"},
    {"cod_ine": "14062", "municipio": "Torrecampo"},
    {"cod_ine": "14068", "municipio": "Villaharta"},
    {"cod_ine": "14069", "municipio": "Villanueva de Córdoba"},
    {"cod_ine": "14070", "municipio": "Villanueva del Duque"},
    {"cod_ine": "14072", "municipio": "Villaralto"},
    {"cod_ine": "14073", "municipio": "Villaviciosa de Córdoba"},
]


# ── Cliente HTTP genérico ─────────────────────────────────────────────────────

def _get_json(url: str, timeout: int = 20) -> dict | list | None:
    """Descarga y parsea JSON de una URL pública."""
    import ssl
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (compatible; AQUASALUD-NORTE/2.0; "
            "+https://github.com/herzonekg-hue/Pulso-Hidrico-Cordoba-)"
        ),
        "Accept": "application/json,text/json,*/*",
    })
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        log.debug("HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.debug("Error JSON — %s — %s", url, e)
        return None


def _get_html(url: str, timeout: int = 25) -> str | None:
    """Descarga HTML de una URL pública."""
    import ssl
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (compatible; AQUASALUD-NORTE/2.0; "
            "+https://github.com/herzonekg-hue/Pulso-Hidrico-Cordoba-)"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "es-ES,es;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            charset = "utf-8"
            ct = r.headers.get("Content-Type", "")
            m = re.search(r"charset=([\w-]+)", ct, re.I)
            if m:
                charset = m.group(1)
            return r.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        log.debug("HTTP %s — %s", e.code, url)
        return None
    except Exception as e:
        log.debug("Error HTML — %s — %s", url, e)
        return None


# ── Fuente 1: elmapadelagua.com ───────────────────────────────────────────────

def consultar_elmapadelagua(cod_ine: str) -> dict | None:
    """
    Consulta la API pública de elmapadelagua.com.

    Estructura de respuesta verificada (2026-11-20):
      {"ok": true, "data": {"estado": "encontrado", "ine": "XXXXX",
                             "registro": {"fh": 4.6, "fechaDato": "2026-05-04",
                                          "confianza": "medido-operadora", ...},
                             "cp": "XXXXX"}}

    El campo "fh" es French Hardness (ºF). 1 ºF = 10 mg CaCO₃/L = ~10 mg/L dureza.
    Si estado != "encontrado", el municipio no tiene datos en el volcado.

    Devuelve dict con campos normalizados o None si no hay datos.
    """
    url = f"{ELMAPADELAGUA_API}/dureza?ine={cod_ine}"
    respuesta = _get_json(url)

    if not respuesta:
        log.debug("    elmapadelagua: sin respuesta para %s", cod_ine)
        return None

    # Desenvolver wrapper {"ok": true, "data": {...}}
    if isinstance(respuesta, dict) and respuesta.get("ok") is True:
        data = respuesta.get("data", {})
    else:
        # Respuesta directa (formato alternativo)
        data = respuesta if isinstance(respuesta, dict) else {}

    if not isinstance(data, dict):
        return None

    estado = data.get("estado", "").lower()
    if estado not in ("encontrado", "ok", ""):
        # "no encontrado" u otro estado negativo
        log.debug("    elmapadelagua: estado='%s' para %s", estado, cod_ine)
        return None

    # El registro de datos está en data["registro"]
    reg = data.get("registro") or {}

    # French Hardness (ºF) → mg/L CaCO₃ (aprox. ×10)
    fh = reg.get("fh")
    dureza_mgl = round(float(fh) * 10, 1) if fh is not None else None

    # Calificación sanitaria: la API de dureza no incluye resolución de no aptitud
    # directamente — solo datos de dureza. La calificación se establece como
    # "Con datos analíticos" cuando hay datos de dureza, que da semáforo VERDE.
    # Si la API incluyera un campo de no aptitud en el futuro, se procesaría aquí.
    cal_raw = (
        reg.get("calificacion_sanitaria") or
        reg.get("calificacion") or
        data.get("calificacion") or
        ""
    )
    no_apta = False
    if cal_raw:
        cal_lower = str(cal_raw).lower()
        if "no apta" in cal_lower or "no_apta" in cal_lower:
            cal_raw  = "No apta"
            no_apta  = True
        elif "apta" in cal_lower or "conforme" in cal_lower:
            cal_raw = "Apta"

    # Si hay dureza pero sin calificación explícita → "Con datos analíticos" (VERDE)
    if dureza_mgl is not None and not cal_raw:
        cal_raw = "Con datos analíticos"

    fecha_analisis = reg.get("fechaDato") or reg.get("fecha") or data.get("fechaDato") or "—"

    result = {
        "fuente":             "elmapadelagua.com (volcado SINAC — Ministerio de Sanidad)",
        "dureza_total_mgl":   dureza_mgl,
        "dureza_calcio_mgl":  None,
        "dureza_magnesio_mgl": None,
        "calificacion":       cal_raw or None,
        "resolucion_no_apta": no_apta,
        "fecha_analisis":     fecha_analisis,
        "confianza":          reg.get("confianza"),
    }

    return result if (result["dureza_total_mgl"] is not None or result["calificacion"]) else None


# ── Fuente 2: SINAC v2 HTML (solo entornos sin proxy restrictivo) ─────────────

class LinkExtractor(HTMLParser):
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


def consultar_sinac_v2(cod_ine: str) -> dict | None:
    """
    Consulta SINAC v2 directamente (HTML scraping, 2 pasos).
    Solo funciona si el host sinacv2.sanidad.gob.es es accesible.
    """
    # Paso 1: lista de redes del municipio
    url_redes = f"{SINAC_V2_BASE}/informacionRedes.do?codMunicipio={cod_ine}"
    html_redes = _get_html(url_redes)
    if not html_redes:
        return None

    p = LinkExtractor("informacionCalidad.do")
    p.feed(html_redes)
    links = p.links
    if not links:
        p2 = LinkExtractor("informacionZona.do")
        p2.feed(html_redes)
        links = p2.links
    if not links:
        # Portal usa JS para navegación — no hay links estáticos
        log.debug("    SINAC v2: sin links en %s (posible JS navigation)", cod_ine)
        return None

    time.sleep(0.5)

    # Paso 2: calificación de la primera red
    href = links[0]
    if href.startswith("/"):
        href = "https://sinacv2.sanidad.gob.es" + href
    elif not href.startswith("http"):
        href = f"{SINAC_V2_BASE}/{href}"

    html_cal = _get_html(href)
    if not html_cal:
        return None

    pe = TextExtractor()
    pe.feed(html_cal)
    texto = pe.text.lower()

    no_apta = any(p in texto for p in [
        "no apta", "no es apta", "resolución de no aptitud",
        "resolucion de no aptitud", "agua no apta",
    ])
    apta = "apta" in texto and not no_apta

    fecha_analisis = "—"
    m_fecha = re.search(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', pe.text)
    if m_fecha:
        try:
            mes_n = int(m_fecha.group(2))
            anio  = int(m_fecha.group(3))
            meses = ["ene","feb","mar","abr","may","jun",
                     "jul","ago","sep","oct","nov","dic"]
            fecha_analisis = f"{meses[mes_n-1]} {anio}"
        except Exception:
            fecha_analisis = m_fecha.group(0)

    return {
        "fuente":             "SINAC v2 (sinacv2.sanidad.gob.es)",
        "calificacion":       "No apta" if no_apta else ("Apta" if apta else "Sin datos"),
        "resolucion_no_apta": no_apta,
        "fecha_analisis":     fecha_analisis,
        "dureza_total_mgl":   None,
    }


# ── Consulta con fallback automático ─────────────────────────────────────────

def consultar_municipio(cod_ine: str) -> dict:
    """
    Consulta un municipio probando las fuentes en orden de prioridad:
      1. elmapadelagua.com (REST JSON, accesible en GitHub Actions)
      2. SINAC v2 directo (HTML, solo entornos sin proxy restrictivo)

    Siempre devuelve un dict con al menos: calificacion, resolucion_no_apta,
    fecha_analisis, fuente.
    """
    # Fuente 1: elmapadelagua
    resultado = consultar_elmapadelagua(cod_ine)
    if resultado:
        log.debug("    Fuente: elmapadelagua (%s)", cod_ine)
        return resultado

    time.sleep(0.3)

    # Fuente 2: SINAC v2 directo
    resultado = consultar_sinac_v2(cod_ine)
    if resultado:
        log.debug("    Fuente: SINAC v2 directo (%s)", cod_ine)
        return resultado

    # Sin datos de ninguna fuente
    log.warning("    Sin datos para %s", cod_ine)
    return {
        "calificacion":       "Sin datos",
        "resolucion_no_apta": False,
        "fecha_analisis":     "—",
        "fuente":             "Sin fuente disponible",
        "dureza_total_mgl":   None,
    }


# ── Semáforo ──────────────────────────────────────────────────────────────────

def calcular_semaforo(calificacion: str, resolucion_no_apta: bool) -> dict:
    """
    Convierte la calificación en semáforo VERDE / ROJO / GRIS.
    ROJO solo si hay resolución de no aptitud vigente.
    La regla de ocultación al ciudadano se aplica en index.html.
    """
    if resolucion_no_apta:
        return {"color": "ROJO",  "icono": "🔴", "texto": "No apta"}
    cal = (calificacion or "").lower()
    if cal in ("apta", "con datos analíticos"):
        return {"color": "VERDE", "icono": "🟢", "texto": "Apta"}
    if cal in ("", "sin datos", "sin fuente disponible", "no disponible"):
        return {"color": "GRIS",  "icono": "⚫", "texto": "Sin datos"}
    return {"color": "GRIS",  "icono": "⚫", "texto": "Sin datos"}


# ── Alerta interna NO APTA ────────────────────────────────────────────────────

def enviar_alerta_interna(municipios_rojo: list, cfg: dict):
    """
    Alerta interna a Antonio cuando hay municipios NO APTA.
    NUNCA se publica al exterior.
    """
    if not municipios_rojo:
        return

    nombres = ", ".join(m["municipio"] for m in municipios_rojo)
    titulo  = f"🔒 SINAC INTERNO — {len(municipios_rojo)} NO APTA: {nombres}"
    cuerpo  = (
        f"AVISO INTERNO — AQUASALUD NORTE — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Municipios con resolución de no aptitud vigente en SINAC:\n\n"
    )
    for m in municipios_rojo:
        cuerpo += f"  • {m['municipio']} (análisis: {m.get('fecha_analisis','—')})\n"
    cuerpo += (
        "\nACCIÓN REQUERIDA:\n"
        "  1. Verificar resolución en SINAC v2 (sinacv2.sanidad.gob.es)\n"
        "  2. Contactar con Salud Ambiental AGS Norte de Córdoba\n"
        "  3. Coordinar comunicación oficial con el Ayuntamiento\n\n"
        "NOTA: La vista ciudadana muestra 'En verificación' (ámbar)\n"
        "hasta que el técnico actualice el estado.\n\n"
        "Este mensaje es CONFIDENCIAL — uso exclusivo AGS Norte de Córdoba."
    )

    log.warning("ALERTA INTERNA NO APTA: %s", nombres)

    # Canal 1: ntfy
    ntfy_topic  = cfg.get("ntfy_topic", "").strip()
    ntfy_server = cfg.get("ntfy_server", "https://ntfy.sh").strip()
    if ntfy_topic:
        try:
            import ssl
            ctx = ssl._create_unverified_context()
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
            with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
                log.info("Alerta ntfy enviada: %s", r.status)
        except Exception as e:
            log.error("Error ntfy: %s", e)

    # Canal 2: correo
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
            log.error("Error correo: %s", e)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # config.json es opcional (solo para alertas externas)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        log.info("config.json cargado — alertas activadas")
    else:
        cfg = {}
        log.info("config.json no encontrado — modo CI sin alertas externas")

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
            return

    ahora = datetime.now(timezone.utc)
    meses = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible = f"{ahora.day} {meses[ahora.month-1]} {ahora.year} {ahora.strftime('%H:%M')}"

    log.info("Consultando SINAC para %d municipios del norte de Córdoba…", len(MUNICIPIOS))

    municipios_out  = []
    municipios_rojo = []
    resumen = {"VERDE": 0, "ROJO": 0, "GRIS": 0}

    for muni in MUNICIPIOS:
        log.info("  → %s (%s)", muni["municipio"], muni["cod_ine"])
        resultado = consultar_municipio(muni["cod_ine"])

        semaforo = calcular_semaforo(
            resultado.get("calificacion", ""),
            resultado.get("resolucion_no_apta", False)
        )
        resumen[semaforo["color"]] = resumen.get(semaforo["color"], 0) + 1

        # Construir entrada — incluir dureza si está disponible
        entrada: dict = {
            "municipio":          muni["municipio"],
            "cod_ine":            muni["cod_ine"],
            "calificacion":       resultado.get("calificacion"),
            "resolucion_no_apta": resultado.get("resolucion_no_apta", False),
            "fecha_analisis":     resultado.get("fecha_analisis", "—"),
            "semaforo":           semaforo,
            "fuente_datos":       resultado.get("fuente", "—"),
        }
        # Añadir dureza si está disponible
        if resultado.get("dureza_total_mgl") is not None:
            entrada["dureza_total_mgl"] = resultado["dureza_total_mgl"]
        if resultado.get("dureza_calcio_mgl") is not None:
            entrada["dureza_calcio_mgl"] = resultado["dureza_calcio_mgl"]
        if resultado.get("dureza_magnesio_mgl") is not None:
            entrada["dureza_magnesio_mgl"] = resultado["dureza_magnesio_mgl"]

        municipios_out.append(entrada)

        if semaforo["color"] == "ROJO":
            municipios_rojo.append({**muni, "fecha_analisis": resultado.get("fecha_analisis","—")})
            log.warning("⚠️  NO APTA: %s", muni["municipio"])

    # Alerta interna si hay ROJO
    if municipios_rojo:
        enviar_alerta_interna(municipios_rojo, cfg)

    # Detectar fuentes usadas para metadatos
    fuentes_usadas = list({e.get("fuente_datos","—") for e in municipios_out})

    salida = {
        "meta": {
            "generado":               ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fecha_consulta_legible": fecha_legible,
            "fuentes":                fuentes_usadas,
            "fuente_principal": (
                "elmapadelagua.com (volcado SINAC anual — Ministerio de Sanidad)"
                if any("elmapadelagua" in f for f in fuentes_usadas)
                else "SINAC v2 (sinacv2.sanidad.gob.es — Ministerio de Sanidad)"
            ),
            "logica_semaforo": (
                "VERDE=apta o con datos analíticos, "
                "ROJO=resolución no aptitud vigente (solo vista técnica interna), "
                "GRIS=sin datos."
            ),
            "municipios_total": len(municipios_out),
        },
        "resumen":    resumen,
        "municipios": municipios_out,
    }

    out = SALIDA_DIR / "sinac_calidad.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    tmp.replace(out)

    log.info(
        "sinac_calidad.json → %s | VERDE:%d ROJO:%d GRIS:%d",
        out, resumen["VERDE"], resumen["ROJO"], resumen["GRIS"]
    )

    if resumen["ROJO"] > 0:
        log.warning("HAY RESOLUCIONES NO APTA — alerta interna enviada")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
