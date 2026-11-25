"""Busca en SIRA Guadiana qué estaciones miden CAUDAL cerca del Guadarramilla/Guadamatilla/Arenoso y qué variables tiene la EAA-421."""
import json, sys
sys.path.insert(0, ".")
import scraper_sira as s
tok = s.obtener_token_sira(); s.HEADERS["Authjwt"] = tok or ""
out = {"token": bool(tok)}
cat = s._get(s.URL_ESTACIONES)
out["claves_catalogo"] = list(cat) if isinstance(cat, dict) else type(cat).__name__
claves = ("guadarramilla", "guadamatilla", "arenoso", "lanchar", "zujar", "zújar", "colada", "viso", "pozoblanco", "cigüe", "milano", "santa maria", "guadalmez")
cerca = []
def recorre(x, ruta=""):
    if isinstance(x, list):
        for it in x:
            if isinstance(it, dict):
                txt = json.dumps(it, ensure_ascii=False).lower()
                if any(k in txt for k in claves):
                    cerca.append({k: it.get(k) for k in list(it)[:14]} | {"_lista": ruta})
            recorre(it, ruta)
    elif isinstance(x, dict):
        for k, v in x.items(): recorre(v, k)
recorre(cat)
out["estaciones_cercanas"] = cerca[:80]
# tablas públicas por prefijo
for pref in ["CR", "A", "AF", "AE", "EAA", "E"]:
    r = s._post(s.URL_RESOURCE, {"id": pref, "idEstacion": pref, "type": "ficha_redes_control"})
    out[f"tabla_{pref}"] = ("sin respuesta" if r is None else {"claves": list(r)[:8] if isinstance(r, dict) else type(r).__name__,
        "n": len((r.get("ult_res") or {}).get("valores", [])) if isinstance(r, dict) and isinstance(r.get("ult_res"), dict) else None,
        "muestra": json.dumps(r, ensure_ascii=False)[:700]})
# EAA-421: variables que realmente devuelve (últimos 7 días)
r = s._post(s.URL_CALIDAD, {"consulta": "EAA_DAT", "estacion": "EAA-421", "tiempo": 7, "fInicial": None, "fFinal": None})
if isinstance(r, dict):
    d = r.get("datos") or []
    out["EAA421_variables"] = sorted({k for x in d if isinstance(x, dict) for k in x})
    out["EAA421_CLF_FIC_no_nulos"] = {k: sum(1 for x in d if isinstance(x, dict) and x.get(k) not in (None, "", "null")) for k in ("CLF", "FIC", "SAC", "CD", "TB")}
    out["EAA421_cabecera"] = json.dumps(r.get("cabecera"), ensure_ascii=False)[:1200]
json.dump(out, open("analisis/sira_caudal.json", "w"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:6000])
