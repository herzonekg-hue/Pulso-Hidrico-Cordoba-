#!/usr/bin/env python3
"""
Histórico de la EAA-421 desde SIRA (POST /calidad, consulta EAA_DAT) y prueba de sensor virtual.
Solo lectura de datos públicos. Escribe historico/eaa421_historico.csv y imprime un resumen.
Uso: python pruebas/historico_eaa421.py   (necesita red: ejecutar en GitHub Actions)
"""
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper_sira as s  # noqa: E402

tok = s.obtener_token_sira()
if tok:
    s.HEADERS["Authjwt"] = tok

mejor, mejor_t = [], None
for t in (7, 30, 90, 180, 365, 730):
    d = s._post(s.URL_CALIDAD, {"consulta": "EAA_DAT", "estacion": "EAA-421", "tiempo": t, "fInicial": None, "fFinal": None}, timeout=90)
    reg = d if isinstance(d, list) else []
    reg = [r for r in reg if isinstance(r, dict) and r.get("Timestamp")]
    print(f"tiempo={t}: {len(reg)} registros", (reg[0]["Timestamp"], reg[-1]["Timestamp"]) if reg else "")
    if len(reg) > len(mejor):
        mejor, mejor_t = reg, t
if not mejor:
    sys.exit("sin datos")

cols = ["Timestamp", "TA", "PO", "NH", "PH", "OX", "CD", "TB", "NO"]
Path("historico").mkdir(exist_ok=True)
with open("historico/eaa421_historico.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for r in mejor:
        w.writerow([r.get(c) for c in cols])
print(f"\nCSV guardado con tiempo={mejor_t}: {len(mejor)} filas, de {mejor[0]['Timestamp']} a {mejor[-1]['Timestamp']}")


def col(c):
    return np.array([np.nan if r.get(c) in (None, "") else float(r[c]) for r in mejor])


V = {c: col(c) for c in cols[1:]}
print("\nCobertura (% no nulos) y rango:")
for c, v in V.items():
    ok = ~np.isnan(v)
    print(f"  {c}: {ok.mean()*100:5.1f}%  min={np.nanmin(v) if ok.any() else 'NA'}  mediana={np.nanmedian(v) if ok.any() else 'NA'}  max={np.nanmax(v) if ok.any() else 'NA'}")
nh = V["NH"]
print(f"\nNH == 20,00 (techo del sensor): {np.nansum(nh == 20.0)} de {np.sum(~np.isnan(nh))} lecturas")

pred = ["CD", "TB", "OX", "TA", "PH"]
for objetivo in ("NH", "PO"):
    y = V[objetivo]
    X = np.column_stack([V[p] for p in pred])
    m = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
    if objetivo == "NH":
        m &= (y < 20.0)  # sin las lecturas en el techo del sensor
    Xm, ym = X[m], y[m]
    n = len(ym)
    if n < 50:
        print(f"{objetivo}: pocos datos ({n})")
        continue
    k = int(n * 0.7)  # corte cronológico, no aleatorio
    A = np.column_stack([np.ones(k), Xm[:k]])
    coef, *_ = np.linalg.lstsq(A, ym[:k], rcond=None)
    At = np.column_stack([np.ones(n - k), Xm[k:]])
    yp = At @ coef
    ss_res, ss_tot = np.sum((ym[k:] - yp) ** 2), np.sum((ym[k:] - ym[k:].mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    mae = np.mean(np.abs(ym[k:] - yp))
    corr = {p: round(float(np.corrcoef(Xm[:, i], ym)[0, 1]), 2) for i, p in enumerate(pred)}
    print(f"\n{objetivo}: n={n} (entrena {k}, prueba {n-k}); R2 prueba={r2:.2f}; error medio absoluto={mae:.2f}; media real={ym.mean():.2f}")
    print("  correlación con cada variable:", corr)
