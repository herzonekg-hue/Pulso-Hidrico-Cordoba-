import json, sys
sys.path.insert(0, ".")
import scraper_sira as s
tok = s.obtener_token_sira(); s.HEADERS["Authjwt"] = tok or ""
out = {}
for pref in ["CR", "EM", "NR", "S"]:
    r = s._post(s.URL_RESOURCE, {"id": pref, "idEstacion": pref, "type": "ficha_redes_control"})
    if isinstance(r, dict):
        v = (r.get("ult_res") or {}).get("valores", [])
        out[f"{pref}_n"] = len(v)
        out[f"{pref}_cols"] = sorted({k for x in v[:3] if isinstance(x, dict) for k in x})
        out[f"{pref}_interes"] = [x for x in v if isinstance(x, dict) and str(x.get("cod_estacion", x.get("COD", ""))).strip() in ("CR2-07","CR2-08","CR2-09","EM2-05","04.99.012")]
        if not out[f"{pref}_interes"]: out[f"{pref}_primeras"] = v[:2]
    else:
        out[f"{pref}_n"] = "sin respuesta"
for cod in ["EM2-05", "CR2-07", "04.99.012"]:
    pref = cod.split("-")[0] if "-" in cod else "04"
    r = s._post(s.URL_RESOURCE, {"id": pref, "idEstacion": cod, "type": "ficha_redes_control_detalle"})
    out[f"detalle_{cod}"] = "sin respuesta" if r is None else json.dumps(r, ensure_ascii=False)[:900]
for q, est in [("EM_DAT","EM2-05"),("CR_DAT","CR2-07"),("AF_DAT","CR2-07"),("EM_DAT","EM2-05")]:
    r = s._post(s.URL_CALIDAD, {"consulta": q, "estacion": est, "tiempo": 7, "fInicial": None, "fFinal": None})
    out[f"calidad_{q}_{est}"] = "sin respuesta" if r is None else json.dumps(r, ensure_ascii=False)[:500]
json.dump(out, open("analisis/sira_caudal2.json", "w"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:7000])
