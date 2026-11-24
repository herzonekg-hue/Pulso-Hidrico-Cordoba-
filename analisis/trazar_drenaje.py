#!/usr/bin/env python3
"""Traza aguas abajo cada punto de vertido sobre la red de cauces de OpenStreetMap
(sentido de la vía = sentido del flujo). Ejecutado en GitHub Actions."""
import json, math, time, urllib.request, urllib.parse
from pyproj import Transformer
T = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)
PUNTOS = [  # nombre, X, Y (ETRS89 UTM30), fuente
 ("Dos Torres-Añora (arroyo Milano)", 335198, 4257540, "BOE-B-2025-40386"),
 ("Villanueva del Duque-Alcaracejos (arroyo de las Cruces)", 326769, 4252155, "BOP Córdoba 2016-729"),
 ("El Viso-Villaralto (arroyo Arenoso)", 327326, 4261469, "Censo CHG 2025"),
 ("Pedroche (arroyo Santa María)", 345336, 4255452, "Censo CHG 2025"),
 ("El Guijo (afluente arroyo de La Matanza)", 345522, 4263156, "BOE-B-2025-28037"),
 ("Torrecampo (arroyo Figueral de Sanfría)", 352745, 4259802, "BOE-B-2025-46489"),
 ("Fuente la Lancha (arroyo Almarrán)", 320538, 4255301, "BOE-B-2025-32329"),
 ("Fuente la Lancha emergencia (afl. Lanchar)", 322577, 4254771, "BOE-B-2025-32329"),
 ("Hinojosa del Duque (arroyo de Caganchas)", 313124, 4266038, "Censo CHG 2025"),
 ("Belalcázar (arroyo Malagón)", 311233, 4273225, "Censo CHG 2025"),
]
BBOX = (38.05, -5.40, 38.80, -4.55)
OVER = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
def overpass(q):
    for u in OVER*2:
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, data=urllib.parse.urlencode({"data": q}).encode(),
                    headers={"User-Agent": "AQUASALUD-NORTE/1.0"}), timeout=180)
            return json.load(r)
        except Exception as e:
            print("overpass fallo", u, e); time.sleep(10)
    raise SystemExit("Overpass no disponible")
b = "%s,%s,%s,%s" % BBOX
data = overpass(f'[out:json][timeout:170];(way["waterway"~"^(river|stream|canal|ditch|drain|brook)$"]({b}););out geom tags;')
plants = overpass(f'[out:json][timeout:120];(nwr["man_made"="wastewater_plant"]({b}););out center tags;')
res_w = overpass(f'[out:json][timeout:120];(wr["water"="reservoir"]({b});wr["natural"="water"]["name"~"Colada|Boyera"]({b}););out geom tags;')
def hav(a, c):
    R=6371000; p1,p2=math.radians(a[0]),math.radians(c[0]); dl=math.radians(c[1]-a[1]); dp=p2-p1
    return 2*R*math.asin(math.sqrt(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))
nodes, out_e = {}, {}
for w in data["elements"]:
    g = w.get("geometry") or []; nm = w.get("tags", {}).get("name", ""); ty = w["tags"].get("waterway", "")
    ids = w.get("nodes") or []
    for i,pt in enumerate(g):
        k = ids[i] if i < len(ids) else (round(pt["lat"],6), round(pt["lon"],6))
        nodes[k] = (pt["lat"], pt["lon"])
        if i+1 < len(g):
            k2 = ids[i+1] if i+1 < len(ids) else (round(g[i+1]["lat"],6), round(g[i+1]["lon"],6))
            out_e.setdefault(k, []).append((k2, nm, ty, w["id"]))
# embalses (polígonos) para detectar entrada
polys = []
for e in res_w["elements"]:
    nm = e.get("tags", {}).get("name", "")
    if e["type"] == "way" and e.get("geometry"):
        polys.append((nm, [(p["lat"], p["lon"]) for p in e["geometry"]]))
    elif e["type"] == "relation":
        for m in e.get("members", []):
            if m.get("role") == "outer" and m.get("geometry"):
                polys.append((nm, [(p["lat"], p["lon"]) for p in m["geometry"]]))
polys = [(n, g, (min(a for a,b in g), max(a for a,b in g), min(b for a,b in g), max(b for a,b in g))) for n, g in polys if n and len(g) > 2]
def inpoly(pt, poly):
    x, y = pt[1], pt[0]; ins = False; n = len(poly)
    for i in range(n):
        y1,x1 = poly[i]; y2,x2 = poly[(i+1)%n]
        if (y1>y) != (y2>y) and x < (x2-x1)*(y-y1)/(y2-y1+1e-15)+x1: ins = not ins
    return ins
RANK = {"river":0,"canal":1,"stream":2,"brook":3,"ditch":4,"drain":5}
GRID = {}
for k,v in nodes.items():
    if k in out_e: GRID.setdefault((round(v[0],2), round(v[1],2)), []).append(k)
def trazar(lat, lon):
    near = []
    for da in (-0.02,-0.01,0,0.01,0.02):
        for do in (-0.02,-0.01,0,0.01,0.02):
            near += GRID.get((round(lat+da,2), round(lon+do,2)), [])
    cand = sorted(((hav((lat,lon),nodes[k]),k) for k in near), key=lambda t:t[0])[:1]
    if not cand: return None
    d0, k = cand[0]; tramo=[]; dist=0.0; vistos=set(); embalse=None
    while k in out_e and k not in vistos and dist < 150000:
        vistos.add(k)
        opts = sorted(out_e[k], key=lambda o:(RANK.get(o[2],9), o[1]==""))
        k2, nm, ty, wid = opts[0]
        seg = hav(nodes[k], nodes[k2]); dist += seg
        nm = nm or "(cauce sin nombre)"
        if not tramo or tramo[-1]["cauce"] != nm:
            tramo.append({"cauce": nm, "tipo": ty, "km_inicio": round(dist/1000,2), "lat": nodes[k2][0], "lon": nodes[k2][1]})
        if embalse is None:
            la, lo = nodes[k2]
            for pn, pg, bb in polys:
                if bb[0] <= la <= bb[1] and bb[2] <= lo <= bb[3] and inpoly(nodes[k2], pg):
                    embalse = {"nombre": pn, "km": round(dist/1000,2)}; break
        k = k2
    fin = nodes[k]
    return {"distancia_ajuste_m": round(d0), "recorrido": tramo, "km_total": round(dist/1000,1),
            "fin": {"lat": fin[0], "lon": fin[1]}, "entra_en_embalse": embalse}
salida = {"generado": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "fuente": "OpenStreetMap (Overpass); sentido de la vía = sentido del flujo",
          "puntos": [], "depuradoras_osm": []}
for nm, X, Y, f in PUNTOS:
    lon, lat = T.transform(X, Y)
    salida["puntos"].append({"nombre": nm, "x": X, "y": Y, "fuente_coord": f, "lat": lat, "lon": lon, "traza": trazar(lat, lon)})
for e in plants["elements"]:
    c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
    if c.get("lat") is None: continue
    salida["depuradoras_osm"].append({"nombre": e.get("tags",{}).get("name","(sin nombre)"), "operador": e.get("tags",{}).get("operator",""),
        "lat": c["lat"], "lon": c["lon"], "traza": trazar(c["lat"], c["lon"])})
salida["embalses_osm"] = sorted({p[0] for p in polys})
json.dump(salida, open("drenaje_edar.json","w"), ensure_ascii=False, indent=1)
print("OK", len(nodes), "nodos;", len(salida["depuradoras_osm"]), "depuradoras OSM")
