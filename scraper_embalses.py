#!/usr/bin/env python3
"""
Embalses del sistema de abastecimiento del Norte de Córdoba para el panel web.

Pensado para GitHub Actions: no necesita config.json ni credenciales.

  - Sierra Boyera (E33) y Puente Nuevo (E34): SAIH Guadalquivir, tabla pública
    EmbalNiv.aspx (aviso oficial: datos en tiempo real no contrastados).
  - La Colada: cuenca del Guadiana. No figura en el SAIH Guadalquivir y la
    estación SIRA E2-40 exige autenticación (HTTP 401). Se publica su capacidad
    oficial y el volumen queda vacío hasta disponer de una fuente válida.

Escribe:
  data/embalses.json        lectura actual
  data/embalses_serie.json  serie de 7 días (una lectura por hora) para las gráficas

Uso:  python3 scraper_embalses.py                  (red)
      python3 scraper_embalses.py --fixture EmbalNiv.html   (sin red)
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

URL = "https://www.chguadalquivir.es/saih/EmbalNiv.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AQUASALUD-NORTE/1.0; panel AGS Norte de Cordoba)"}
SALIDA = pathlib.Path("data")

# Ficha fija de cada embalse. Capacidades: SAIH Guadalquivir (E33, E34) y
# MITECO, informe de viabilidad de la presa de La Colada (57,7 hm3).
FICHA = {
    "E33": {"nombre": "Sierra Boyera", "rio": "Guadiato", "cuenca": "Guadalquivir",
            "fuente": "SAIH Guadalquivir",
            "papel": "Embalse de la ETAP de Sierra Boyera (Bélmez): abastecimiento de Los Pedroches y el Alto Guadiato"},
    "E34": {"nombre": "Puente Nuevo", "rio": "Guadiato", "cuenca": "Guadalquivir",
            "fuente": "SAIH Guadalquivir",
            "papel": "Reserva de la cuenca del Guadiato. Conexión con Sierra Boyera no ejecutada"},
}
LA_COLADA = {"id": "E2-40", "nombre": "La Colada", "rio": "Guadamatilla", "cuenca": "Guadiana",
             "fuente": "SIRA Guadiana (E2-40)", "capacidad": 57.7,
             "papel": "Conectada a la ETAP de Sierra Boyera. Recibe el río Guadarramilla",
             "volumen": None, "pct": None, "nivel": None, "fecha": None,
             "sin_dato": "Volumen no disponible: la estación E2-40 de SIRA exige autenticación"}


def num(t):
    t = (t or "").replace("\xa0", " ").strip()
    if not t or t in {"-", "—"}:
        return None
    try:
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def fecha_saih(html):
    m = re.search(r"Actualizados:\s*(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})", html)
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return f"{y}-{mo}-{d}T{int(h):02d}:{mi}"


def parse(html):
    soup = BeautifulSoup(html, "lxml")
    patron = re.compile(r"^(E\d+)\s+(.+?)\s+\(([A-Z]{2})\)\s*$")
    out = {}
    for tr in soup.find_all("tr"):
        tds = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not tds:
            continue
        m = patron.match(tds[0])
        if not m or m.group(1) not in FICHA:
            continue
        vals = [v for v in (num(t) for t in tds[1:]) if v is not None]
        if len(vals) < 5:
            continue
        nmn, nivel, cap, vol, pct = vals[:5]
        # control de coherencia: el porcentaje debe cuadrar con volumen/capacidad
        if cap <= 0 or abs(vol / cap * 100 - pct) > 1.0:
            print(f"Fila incoherente descartada: {tds}", file=sys.stderr)
            continue
        out[m.group(1)] = {"id": m.group(1), **FICHA[m.group(1)], "nmn": nmn, "nivel": nivel,
                           "capacidad": cap, "volumen": vol, "pct": pct}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture")
    a = ap.parse_args()

    if a.fixture:
        html = pathlib.Path(a.fixture).read_text(encoding="utf-8", errors="ignore")
    else:
        html = None
        for intento in range(3):  # la página de la CHG da 404/5xx de forma puntual
            try:
                r = requests.get(URL, headers=HEADERS, timeout=40)
                r.raise_for_status()
                r.encoding = r.apparent_encoding or "utf-8"
                html = r.text
                break
            except requests.RequestException as ex:
                print(f"Intento {intento + 1}/3 fallido: {ex}", file=sys.stderr)
                time.sleep(20)
        if html is None:
            print("SAIH Guadalquivir no responde. No se escribe nada.", file=sys.stderr)
            return 2

    leidos = parse(html)
    fecha = fecha_saih(html)
    if "E33" not in leidos:
        print("No se ha podido leer Sierra Boyera (E33). No se escribe nada.", file=sys.stderr)
        return 2

    colada = dict(LA_COLADA)
    try:  # La Colada la lee scraper_sira.py (tabla pública de embalses de SIRA) en data/sira.json
        sira = json.loads((SALIDA / "sira.json").read_text(encoding="utf-8"))
        e2 = next(e for e in sira.get("embalses", []) if e.get("cod") == "E2-40")
        if e2.get("ok") and e2.get("volumen_hm3") is not None:
            hora = (e2.get("hora_obs") or "").replace(" ", "T")[:16]
            colada.update({"fuente": "SIRA Guadiana (E2-40)", "volumen": e2["volumen_hm3"],
                           "pct": e2.get("pct_capacidad"), "nivel": e2.get("nivel_msnm"),
                           "capacidad": e2.get("capacidad_hm3") or colada["capacidad"],
                           "q_salida": e2.get("q_salida_m3s"), "fecha": hora or None})
            colada.pop("sin_dato", None)
    except Exception as ex:  # noqa: BLE001
        print(f"La Colada sin lectura de SIRA: {ex}", file=sys.stderr)

    ahora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for e in leidos.values():
        e["fecha"] = fecha
    datos = {
        "generado": ahora,
        "fuente": "SAIH Guadalquivir (EmbalNiv.aspx) · SIRA Guadiana (La Colada)",
        "aviso": "Datos del SAIH en tiempo real no contrastados (aviso oficial de la CHG).",
        "fecha_saih": fecha,
        "embalses": [leidos["E33"], colada, leidos.get("E34")],
    }
    datos["embalses"] = [e for e in datos["embalses"] if e]

    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "embalses.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")

    # serie corta: una lectura por fecha del SAIH, 7 días como máximo
    rs = SALIDA / "embalses_serie.json"
    try:
        serie = json.loads(rs.read_text(encoding="utf-8"))
    except Exception:
        serie = {}
    for cod, e in leidos.items():
        pts = serie.setdefault(cod, [])
        if fecha and (not pts or pts[-1][0] != fecha):
            pts.append([fecha, e["volumen"]])
        corte = (dt.datetime.fromisoformat(fecha) - dt.timedelta(days=7)).isoformat() if fecha else ""
        serie[cod] = [p for p in pts if p[0] >= corte][-200:]
    rs.write_text(json.dumps(serie, ensure_ascii=False), encoding="utf-8")

    for e in datos["embalses"]:
        print(f"{e['nombre']:14s} vol={e['volumen']} cap={e['capacidad']} pct={e['pct']} fecha={e.get('fecha')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
