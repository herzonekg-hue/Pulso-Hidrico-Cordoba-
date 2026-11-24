#!/usr/bin/env python3
"""
Sondeo de SIRA Guadiana para localizar de dónde salen los datos de La Colada (E2-40).
Solo imprime códigos HTTP y la estructura de las respuestas. No imprime el token.
Uso: python pruebas/sonda_sira_embalse.py   (necesita red: ejecutar en GitHub Actions)
"""
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper_sira as s  # noqa: E402

BASE = s.BASE_URL
CTX = ssl._create_unverified_context()


def llamar(metodo, url, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, headers=s.HEADERS, method=metodo)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=25) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def forma(x, prof=0):
    if isinstance(x, dict):
        return "{" + ", ".join(f"{k}:{forma(v, prof + 1) if prof < 2 else '…'}" for k, v in list(x.items())[:25]) + "}"
    if isinstance(x, list):
        return f"[{len(x)}×" + (forma(x[0], prof + 1) if x and prof < 2 else "") + "]"
    return repr(x)[:40]


tok = s.obtener_token_sira()
print("token:", "OK" if tok else "NO")
if tok:
    s.HEADERS["Authjwt"] = tok

print("\n== GET estaciones")
st, txt = llamar("GET", s.URL_ESTACIONES)
print("HTTP", st, "bytes", len(txt))
try:
    cat = json.loads(txt)
    print("forma:", forma(cat))

    def buscar(x, ruta=""):
        if isinstance(x, dict):
            if any(str(v) in ("E2-40", "La Colada") for v in x.values()):
                print("ENCONTRADO en", ruta, "->", json.dumps(x, ensure_ascii=False)[:1500])
            for k, v in x.items():
                buscar(v, f"{ruta}/{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                buscar(v, f"{ruta}[{i}]")

    buscar(cat)
except Exception as e:  # noqa: BLE001
    print("no es JSON:", e, txt[:200])

print("\n== cabecerasV2")
for m, b in (("GET", None), ("POST", {}), ("POST", {"id": "E", "idEstacion": "E2-40"})):
    st, txt = llamar(m, f"{BASE}/cabecerasV2", b)
    print(m, b, "HTTP", st, "bytes", len(txt), txt[:300].replace("\n", " "))

print("\n== resourceByID E2-40, variantes")
variantes = [
    {"id": "E", "idEstacion": "E2-40", "type": "ficha_redes_control_detalle"},
    {"id": "E2-40", "idEstacion": "E2-40", "type": "ficha_redes_control_detalle"},
    {"id": "E", "idEstacion": "E2-40", "type": "ficha_embalse"},
    {"id": "E", "idEstacion": "E2-40", "type": "ficha_redes_control"},
    {"id": "E", "idEstacion": "E2-40", "type": "embalses_volcap"},
    {"id": "E", "idEstacion": "E2-40", "type": "cabecera"},
    {"id": "EAA", "idEstacion": "EAA-421", "type": "ficha_redes_control_detalle"},
]
for b in variantes:
    st, txt = llamar("POST", s.URL_RESOURCE, b)
    print(b, "-> HTTP", st, "bytes", len(txt))
    if st == 200:
        try:
            print("   forma:", forma(json.loads(txt)))
        except Exception:  # noqa: BLE001
            print("   ", txt[:200])

print("\n== calidad con consulta de embalse")
for consulta in ("EMB_DAT", "E_DAT", "EMBALSE_DAT", "EAA_DAT"):
    st, txt = llamar("POST", s.URL_CALIDAD, {"consulta": consulta, "estacion": "E2-40", "tiempo": 7, "fInicial": None, "fFinal": None})
    print(consulta, "-> HTTP", st, "bytes", len(txt), txt[:200].replace("\n", " "))

print("\n== rutas candidatas GET/POST")
for ruta in ("embalses", "embalsesDatos", "tablaEmbalses", "datosEmbalse", "embalse", "ultimosDatos", "cabeceras", "reservas"):
    for m in ("GET", "POST"):
        st, txt = llamar(m, f"{BASE}/{ruta}", {} if m == "POST" else None)
        if st not in (404, 405):
            print(m, ruta, "-> HTTP", st, "bytes", len(txt), txt[:200].replace("\n", " "))
print("fin")
