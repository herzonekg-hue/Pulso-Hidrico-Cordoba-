#!/usr/bin/env python3
"""
Recoge los datos hidrológicos de la provincia de Córdoba y escribe datos.json.

Fuentes
  1. SAIH Guadalquivir, niveles de embalses:  EmbalNiv.aspx
  2. SAIH Guadalquivir, niveles y caudales en río con umbrales oficiales: AforosTabla.aspx
  3. La Colada (CHG Guadiana). Dos vías:
       a) endpoint_tiempo_real de config.json, si el informático localiza uno en SIRA Guadiana
       b) respaldo semanal del MITECO publicado en embalses.net

Aviso oficial del SAIH: los datos en tiempo real no están contrastados.

Uso:  python3 scraper_saih.py            (una pasada, pensada para cron)
      python3 scraper_saih.py --fixture-dir ruta   (pruebas sin red)
"""
import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = pathlib.Path(__file__).resolve().parent
SALIDA = pathlib.Path(os.environ.get("HIDRICO_SALIDA", str(BASE)))  # donde se escriben datos.json y serie.json
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
URL_EMB = "https://www.chguadalquivir.es/saih/EmbalNiv.aspx"
URL_AFO = "https://www.chguadalquivir.es/saih/AforosTabla.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AGSNorteHidrico/1.0; uso interno AGS Norte de Córdoba)"}
TIMEOUT = 40

logging.basicConfig(
    filename=str(BASE / "scraper.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("hidrico")


def num(texto):
    """Convierte formato español (1.234,56) en float. Devuelve None si está vacío."""
    t = (texto or "").replace("\xa0", " ").strip()
    if not t or t in {"-", "—"}:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def celdas(tr):
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def fecha_pagina(html):
    m = re.search(r"Actualizados:\s*(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2})", html)
    if not m:
        return None
    d, h = m.group(1), m.group(2)
    hh, mm = h.split(":")
    return f"{d} {int(hh):02d}:{mm}"


def descargar(url, fixture_dir=None, nombre=None):
    if fixture_dir:
        return (pathlib.Path(fixture_dir) / nombre).read_text(encoding="utf-8")
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


# ---------------------------------------------------------------- embalses
def parse_embalses(html):
    """Devuelve {id: {...}} con las filas E## nombre (CO) de la tabla de niveles."""
    soup = BeautifulSoup(html, "lxml")
    salida = {}
    patron = re.compile(r"^(E\d+)\s+(.+?)\s+\(([A-Z]{2})\)\s*$")
    for tr in soup.find_all("tr"):
        tds = celdas(tr)
        if not tds:
            continue
        m = patron.match(tds[0])
        if not m:
            continue
        valores = [num(t) for t in tds[1:] if t.strip() != ""]
        valores = [v for v in valores if v is not None]
        if len(valores) < 5:
            log.warning("Fila de embalse con menos de 5 valores: %s", tds)
            continue
        nmn, nivel, cap, vol, pct = valores[:5]
        salida[m.group(1)] = {
            "codigo": m.group(1), "nombre_saih": m.group(2), "provincia": m.group(3),
            "nmn": nmn, "nivel": nivel, "capacidad": cap, "volumen": vol, "pct": pct,
        }
    return salida


# ------------------------------------------------------------------ aforos
def normaliza(h):
    h = re.sub(r"\(.*?\)", "", h or "").lower()
    return re.sub(r"\s+", " ", h).strip()


def parse_aforos(html):
    """Lee todas las tablas de puntos de control y devuelve {id: {...}} de Córdoba."""
    soup = BeautifulSoup(html, "lxml")
    salida = {}
    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 2:
            continue
        cab = [normaliza(c) for c in celdas(filas[0])]
        if "punto" not in cab:
            continue
        ix = {}
        for i, h in enumerate(cab):
            for clave in ("punto", "provincia", "calado", "cota", "caudal",
                          "umbral amarillo", "umbral naranja", "umbral rojo", "umbral unidad"):
                if h == clave and clave not in ix:
                    ix[clave] = i
        for tr in filas[1:]:
            tds = celdas(tr)
            if len(tds) != len(cab):
                continue
            punto = tds[ix["punto"]]
            m = re.match(r"^([A-Z]\d{2})\s+(.*)$", punto)
            if not m:
                continue
            prov = tds[ix["provincia"]] if "provincia" in ix else ""
            if not prov.lower().startswith("córdoba") and not prov.lower().startswith("cordoba"):
                continue
            def g(k):
                return num(tds[ix[k]]) if k in ix else None
            salida[m.group(1)] = {
                "codigo": m.group(1), "punto_saih": m.group(2).strip(),
                "calado": g("calado"), "cota": g("cota"), "caudal": g("caudal"),
                "umbrales_saih": [g("umbral amarillo"), g("umbral naranja"), g("umbral rojo")],
                "unidad_saih": tds[ix["umbral unidad"]].strip() if "umbral unidad" in ix else "",
            }
    return salida


# --------------------------------------------------------------- La Colada
def leer_la_colada(previo):
    cfg = CFG["la_colada"]
    cap = 58.0
    # a) endpoint de tiempo real, si existe. Formato esperado de respuesta:
    #    {"volumen_hm3": 47.2, "fecha": "04/11/2026 08:00"}
    if cfg.get("endpoint_tiempo_real"):
        try:
            r = requests.get(cfg["endpoint_tiempo_real"], headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            j = r.json()
            vol = float(j["volumen_hm3"])
            return {"volumen": vol, "capacidad": cap, "pct": round(vol / cap * 100, 2),
                    "fecha": j.get("fecha", ""), "tiempo_real": True,
                    "origen": "SAIH/SIRA Guadiana"}
        except Exception as e:  # noqa
            log.warning("Endpoint La Colada falló: %s", e)
    # b) respaldo semanal MITECO a través de embalses.net
    try:
        html = descargar(cfg["respaldo_semanal_url"])
        texto = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        m = re.search(r"Agua embalsada \((\d{2})-(\d{2})-(\d{4})\)\s*:?\s*([\d.,]+)\s*hm", texto)
        if m:
            vol = num(m.group(4))
            if vol is not None:
                return {"volumen": vol, "capacidad": cap, "pct": round(vol / cap * 100, 2),
                        "fecha": f"{m.group(1)}/{m.group(2)}/{m.group(3)}", "tiempo_real": False,
                        "origen": "MITECO boletín semanal, a través de embalses.net"}
        log.warning("No se encontró el patrón de La Colada en embalses.net")
    except Exception as e:  # noqa
        log.warning("Respaldo La Colada falló: %s", e)
    # c) último dato conocido
    if previo:
        d = dict(previo)
        d["antiguo"] = True
        return d
    man = cfg["ultimo_dato_manual"]
    return {"volumen": man["volumen"], "capacidad": cap,
            "pct": round(man["volumen"] / cap * 100, 2), "fecha": man["fecha"],
            "tiempo_real": False, "origen": man["origen"], "antiguo": True}


# ------------------------------------------------------------------- serie
def actualizar_serie(embalses, ruta):
    """Guarda una serie corta (7 días, un punto por hora) para dibujar tendencias."""
    try:
        serie = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        serie = {}
    ahora = dt.datetime.now().replace(microsecond=0)
    corte = ahora - dt.timedelta(days=7)
    for e in embalses:
        if e.get("volumen") is None or not e.get("tiempo_real"):
            continue
        pts = serie.setdefault(e["id"], [])
        if pts:
            ultimo = dt.datetime.fromisoformat(pts[-1][0])
            if (ahora - ultimo) < dt.timedelta(minutes=55):
                continue
        pts.append([ahora.isoformat(), round(e["volumen"], 3)])
        serie[e["id"]] = [p for p in pts if dt.datetime.fromisoformat(p[0]) >= corte]
    ruta.write_text(json.dumps(serie, ensure_ascii=False), encoding="utf-8")


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture-dir", help="Carpeta con EmbalNiv.html y AforosTabla.html para pruebas")
    args = ap.parse_args()

    ruta_datos = SALIDA / "datos.json"
    previo = {}
    if ruta_datos.exists():
        try:
            previo = json.loads(ruta_datos.read_text(encoding="utf-8"))
        except Exception:
            previo = {}

    try:
        html_emb = descargar(URL_EMB, args.fixture_dir, "EmbalNiv.html")
        saih_emb = parse_embalses(html_emb)
        f_emb = fecha_pagina(html_emb)
    except Exception as e:  # noqa
        log.error("No se pudo leer EmbalNiv: %s", e)
        return 1
    if not saih_emb:
        log.error("EmbalNiv sin filas reconocibles. Puede haber cambiado el diseño de la página.")
        return 1

    try:
        html_afo = descargar(URL_AFO, args.fixture_dir, "AforosTabla.html")
        saih_afo = parse_aforos(html_afo)
        f_afo = fecha_pagina(html_afo)
    except Exception as e:  # noqa
        log.error("No se pudo leer AforosTabla: %s", e)
        saih_afo, f_afo = {}, None

    prev_emb = {e["id"]: e for e in previo.get("embalses", [])}
    embalses = []
    for meta in CFG["embalses"]:
        item = {k: meta[k] for k in ("id", "nombre", "zona", "municipios", "rio", "cuenca") if k in meta}
        for k in ("nota", "contraembalse", "ute"):
            if k in meta:
                item[k] = meta[k]
        if meta["fuente"] == "guadiana":
            colada = leer_la_colada({k: prev_emb.get("COLADA", {}).get(k)
                                     for k in ("volumen", "capacidad", "pct", "fecha", "tiempo_real", "origen")}
                                    if "COLADA" in prev_emb else None)
            item.update(colada)
            item["capacidad"] = meta["capacidad"]
        else:
            fila = saih_emb.get(meta["id"])
            if fila is None:
                log.warning("Embalse %s no aparece en el SAIH", meta["id"])
                if meta["id"] in prev_emb:
                    item.update({k: prev_emb[meta["id"]].get(k) for k in
                                 ("volumen", "capacidad", "pct", "nmn", "nivel", "fecha", "origen")})
                    item["tiempo_real"] = False
                    item["antiguo"] = True
                continue
            item.update({"volumen": fila["volumen"], "capacidad": fila["capacidad"], "pct": fila["pct"],
                         "nmn": fila["nmn"], "nivel": fila["nivel"], "fecha": f_emb,
                         "tiempo_real": True, "origen": "SAIH Guadalquivir"})
        embalses.append(item)

    aforos = []
    for meta in CFG["aforos"]:
        fila = saih_afo.get(meta["id"])
        item = {"id": meta["id"], "nombre": meta["nombre"], "rio": meta["rio"], "unidad": meta["unidad"],
                "medida": meta["medida"], "umbrales": meta["umbrales"], "umbrales_origen": "config"}
        if fila:
            us = fila["umbrales_saih"]
            if all(v is not None for v in us):
                item["umbrales"] = us
                item["umbrales_origen"] = "SAIH"
                if us != meta["umbrales"]:
                    log.warning("Umbrales de %s distintos a los de config.json: %s vs %s",
                                meta["id"], us, meta["umbrales"])
            item["valor"] = fila[meta["medida"]]
            item["caudal"] = fila.get("caudal")
            item["fecha"] = f_afo
        else:
            item["valor"] = None
            item["fecha"] = None
        aforos.append(item)

    datos = {
        "generado": dt.datetime.now().replace(microsecond=0).isoformat(),
        "aviso": "Datos automáticos no contrastados (SAIH Guadalquivir). El dato validado es el boletín hidrológico del MITECO.",
        "zonas": CFG["zonas"],
        "embalses": embalses,
        "aforos": aforos,
        "umbrales_embalse": CFG["umbrales_embalse"],
        "frescura_maxima_horas": CFG["frescura_maxima_horas"],
    }
    tmp = ruta_datos.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta_datos)
    actualizar_serie(embalses, SALIDA / "serie.json")
    log.info("datos.json actualizado: %d embalses, %d puntos de río", len(embalses), len(aforos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
