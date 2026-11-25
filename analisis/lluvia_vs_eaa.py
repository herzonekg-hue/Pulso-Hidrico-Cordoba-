"""Cruza el histórico de la EAA-421 (SIRA, 2 años) con la lluvia horaria (Open-Meteo, reanálisis ERA5-Land)
para ver si la lluvia agrava los parámetros. Salida: analisis/lluvia_vs_eaa.json y .md"""
import json, sys, requests
import pandas as pd, numpy as np
from scipy.stats import spearmanr

df = pd.read_csv("historico/eaa421_historico.csv", parse_dates=["timestamp"]).sort_values("timestamp")
for c in ["TA","PO","NH","PH","OX","CD","TB","NO"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
t0, t1 = df.timestamp.min().date(), df.timestamp.max().date()
pts = [(38.379, -4.849), (38.52, -4.71)]  # Pozoblanco y entorno de El Viso (aprox.)
series = []
for la, lo in pts:
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=120, params=dict(
        latitude=la, longitude=lo, start_date=str(t0), end_date=str(t1),
        hourly="precipitation", timezone="UTC"))
    r.raise_for_status()
    h = r.json()["hourly"]
    series.append(pd.Series(h["precipitation"], index=pd.to_datetime(h["time"]), dtype=float))
rain = pd.concat(series, axis=1).mean(axis=1).fillna(0)
r48 = rain.rolling(48, min_periods=1).sum().rename("r48")
r24 = rain.rolling(24, min_periods=1).sum().rename("r24")
r7d = rain.rolling(168, min_periods=1).sum().rename("r7d")
dfm = pd.merge_asof(df, pd.concat([r24, r48, r7d], axis=1).reset_index().rename(columns={"index": "timestamp"}),
                    on="timestamp", direction="backward")
dfm["bin"] = pd.cut(dfm.r48, [-0.01, 1, 5, 15, 30, 1e9], labels=["<1", "1-5", "5-15", "15-30", ">=30"])
pars = ["PO", "NH", "OX", "TB", "CD", "NO"]
tabla = dfm.groupby("bin", observed=True).agg(n=("PO", "size"), **{f"{p}_med": (p, "median") for p in pars}).round(2)
# lecturas con repetición exacta (posible sensor congelado) para avisar
rep = {p: float((df[p].diff() == 0).mean().round(3)) for p in pars}
# correlación diaria con retardos (lluvia del día d-k frente a media del día d)
dia = df.set_index("timestamp")[pars].resample("D").mean()
lluvia_d = rain.resample("D").sum()
corr = {}
for p in pars:
    corr[p] = {}
    for k in range(0, 6):
        x = lluvia_d.shift(k).reindex(dia.index)
        ok = x.notna() & dia[p].notna()
        rho, pv = spearmanr(x[ok], dia[p][ok])
        corr[p][f"lag{k}d"] = [round(float(rho), 3), float(f"{pv:.2g}")]
# eventos: días con lluvia >= 15 mm vs. los 3 días siguientes frente a mediana general
ev = lluvia_d[lluvia_d >= 15].index
resp = {}
for p in pars:
    base = float(dia[p].median())
    vals = []
    for d in ev:
        w = dia[p][d: d + pd.Timedelta(days=3)]
        if len(w): vals.append(float(w.max()))
    resp[p] = {"mediana_general": round(base, 2), "n_eventos": len(vals),
               "mediana_max_3d_tras_evento": round(float(np.median(vals)), 2) if vals else None}
out = dict(periodo=[str(t0), str(t1)], lecturas=len(df), tabla_por_lluvia48h=tabla.reset_index().astype(str).to_dict("records"),
           correlacion_spearman_diaria=corr, eventos_15mm=resp, proporcion_lecturas_repetidas=rep,
           fuente_lluvia="Open-Meteo archive (ERA5-Land, reanálisis ~9 km), media de 2 puntos; no es pluviómetro")
json.dump(out, open("analisis/lluvia_vs_eaa.json", "w"), ensure_ascii=False, indent=1)
open("analisis/lluvia_vs_eaa.md", "w").write("```\n" + tabla.to_string() + "\n```\n")
print(tabla.to_string()); print(json.dumps(resp, indent=1)); print(json.dumps(corr["TB"])); print(json.dumps(corr["PO"]))
