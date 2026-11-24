import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper_sira as s
tok = s.obtener_token_sira()
if tok:
    s.HEADERS["Authjwt"] = tok
for idest in ("E", "E2-40"):
    d = s._post(s.URL_RESOURCE, {"id": "E", "idEstacion": idest, "type": "ficha_redes_control"})
    print("== idEstacion", idest, "->", bool(d))
    if not d:
        continue
    ur = d["ult_res"]
    print("encabezado:", json.dumps(ur["encabezado"], ensure_ascii=False)[:1800])
    v = ur["valores"]
    print("n valores:", len(v), "claves fila0:", list(v[0].keys()) if isinstance(v[0], dict) else v[0])
    for fila in v:
        txt = json.dumps(fila, ensure_ascii=False)
        if "E2-40" in txt or "La Colada" in txt:
            print("FILA La Colada:", txt[:1500])
    print("est_res:", json.dumps(d.get("est_res"), ensure_ascii=False)[:800])
    print("embalses_volcap:", d.get("embalses_volcap"))
