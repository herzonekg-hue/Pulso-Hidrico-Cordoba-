"""Estacionalidad, primer flush y comportamiento del sensor en la EAA-421 (SIRA, 2 años)."""
import json, requests
import pandas as pd, numpy as np
from scipy.stats import spearmanr

df = pd.read_csv("historico/eaa421_historico.csv", parse_dates=["timestamp"]).sort_values("timestamp")
pars = ["TA","PO","NH","PH","OX","CD","TB","NO"]
for c in pars: df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["timestamp"]).set_index("timestamp")
out = {}
# 1) por mes
m = df.copy(); m["mes"] = m.index.strftime("%Y-%m")
g = m.groupby("mes")
mes = pd.DataFrame({"n": g.size(), "TA": g.TA.median(), "OX": g.OX.median(), "OX<5_%": g.OX.apply(lambda s: (s<5).mean()*100),
    "PO": g.PO.median(), "NH": g.NH.median(), "NH>1_%": g.NH.apply(lambda s: (s>1).mean()*100), "TB": g.TB.median(), "CD": g.CD.median()}).round(2)
out["por_mes"] = mes.reset_index().to_dict("records")
# 2) ciclo diario del oxígeno (hora UTC)
out["OX_mediana_por_hora_UTC"] = df.groupby(df.index.hour).OX.median().round(2).to_dict()
out["OX_vs_TA_spearman"] = round(float(spearmanr(df.OX.dropna().align(df.TA.dropna(), join="inner")[0], df.OX.dropna().align(df.TA.dropna(), join="inner")[1])[0]), 3)
# 3) sensor: rachas de valores idénticos
def rachas(s):
    s = s.dropna(); grp = (s != s.shift()).cumsum(); l = s.groupby(grp).size()
    return {"rachas": int(len(l)), "max_lecturas": int(l.max()), "rachas>=8 (2 h)": int((l>=8).sum()), "rachas>=96 (1 dia)": int((l>=96).sum()),
            "%lecturas_en_rachas>=8": round(float(l[l>=8].sum()/len(s)*100),1)}
out["rachas_sensor"] = {p: rachas(df[p]) for p in ["PO","NH","OX","TB","CD","NO"]}
out["NH_en_techo_20"] = int((df.NH >= 19.99).sum())
# 4) primer flush con lluvia ERA5-Land
t0, t1 = df.index.min().date(), df.index.max().date(); S=[]
for la, lo in [(38.379,-4.849),(38.52,-4.71)]:
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=120, params=dict(latitude=la, longitude=lo, start_date=str(t0), end_date=str(t1), hourly="precipitation", timezone="UTC")); r.raise_for_status()
    h = r.json()["hourly"]; S.append(pd.Series(h["precipitation"], index=pd.to_datetime(h["time"]), dtype=float))
rd = pd.concat(S, axis=1).mean(axis=1).fillna(0).resample("D").sum()
dia = df[["PO","NH","OX","TB","CD","NO"]].resample("D").agg({"PO":"median","NH":"max","OX":"min","TB":"max","CD":"median","NO":"max"})
filas = []
for d in rd[rd >= 10].index:
    prev = rd[max(rd.index.min(), d - pd.Timedelta(days=30)): d - pd.Timedelta(days=1)]
    secos = 0
    for v in prev[::-1]:
        if v < 1: secos += 1
        else: break
    w = dia[d: d + pd.Timedelta(days=3)]
    if len(w) < 2: continue
    base = dia[d - pd.Timedelta(days=7): d - pd.Timedelta(days=1)]
    filas.append(dict(fecha=str(d.date()), mm=round(float(rd[d]),1), dias_secos_previos=secos, mes=d.month,
        TB_max=round(float(w.TB.max()),1), NH_max=round(float(w.NH.max()),2), OX_min=round(float(w.OX.min()),2),
        PO_med=round(float(w.PO.median()),2), PO_previo=round(float(base.PO.median()),2) if len(base) else None,
        TB_previo=round(float(base.TB.median()),1) if len(base) else None))
ev = pd.DataFrame(filas); out["eventos_ge10mm"] = filas
if len(ev):
    for nombre, mask in [("tras>=10 dias secos", ev.dias_secos_previos >= 10), ("tras<10 dias secos", ev.dias_secos_previos < 10),
                         ("mm>=25", ev.mm >= 25)]:
        s = ev[mask]
        out.setdefault("resumen_eventos", {})[nombre] = {"n": int(len(s)), "TB_max_mediana": float(s.TB_max.median()) if len(s) else None,
            "NH_max_mediana": float(s.NH_max.median()) if len(s) else None, "OX_min_mediana": float(s.OX_min.median()) if len(s) else None}
    out["PO_cae_en_evento_%"] = round(float((ev.PO_med < ev.PO_previo).mean()*100), 1)
json.dump(out, open("analisis/estacionalidad.json","w"), ensure_ascii=False, indent=1, default=str)
print(mes.to_string()); print(json.dumps({k: out[k] for k in ["OX_mediana_por_hora_UTC","OX_vs_TA_spearman","rachas_sensor","NH_en_techo_20","resumen_eventos","PO_cae_en_evento_%"] if k in out}, indent=1))
