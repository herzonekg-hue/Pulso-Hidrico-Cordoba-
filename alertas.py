#!/usr/bin/env python3
"""
Evalúa datos.json y envía avisos cuando cambia el nivel de alerta de un punto.

Niveles: 0 normal, 1 amarillo, 2 naranja, 3 rojo.
  Ríos: umbrales oficiales del SAIH (por exceso, riesgo de desbordamiento).
  Embalses: umbrales de trabajo por defecto bajo (sequía), no oficiales. Ver config.json.

Solo avisa en las transiciones (sube, baja o se normaliza) para no repetir mensajes.
El estado se guarda en estado_alertas.json.

Uso:  python3 alertas.py            (pasada normal, para cron)
      python3 alertas.py --probar   (envía un mensaje de prueba por los canales activos)
"""
import argparse
import json
import logging
import os
import pathlib
import smtplib
import sys
from email.message import EmailMessage

import requests

BASE = pathlib.Path(__file__).resolve().parent
SALIDA = pathlib.Path(os.environ.get("HIDRICO_SALIDA", str(BASE)))
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
ETIQ = {0: "normal", 1: "amarillo", 2: "naranja", 3: "rojo"}
PRIORIDAD_NTFY = {0: "default", 1: "default", 2: "high", 3: "urgent"}

logging.basicConfig(filename=str(BASE / "alertas.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alertas")


def nivel_rio(a):
    v = a.get("valor")
    if v is None:
        return None
    am, na, ro = a["umbrales"]
    return 3 if v >= ro else 2 if v >= na else 1 if v >= am else 0


def nivel_embalse(e, u):
    if e.get("pct") is None or e.get("contraembalse") or e.get("antiguo"):
        return None
    p = e["pct"]
    return 3 if p <= u["rojo"] else 2 if p <= u["naranja"] else 1 if p <= u["amarillo"] else 0


def evaluar(datos):
    res = {}
    for a in datos.get("aforos", []):
        n = nivel_rio(a)
        if n is None:
            continue
        unidad = a["unidad"]
        txt = (f"{a['nombre']}: {a['valor']:g} {unidad}. Umbrales amarillo {a['umbrales'][0]:g}, "
               f"naranja {a['umbrales'][1]:g}, rojo {a['umbrales'][2]:g} {unidad}.")
        res["rio:" + a["id"]] = (n, a["nombre"], "Riesgo de desbordamiento", txt)
    u = datos.get("umbrales_embalse", CFG["umbrales_embalse"])
    for e in datos.get("embalses", []):
        n = nivel_embalse(e, u)
        if n is None:
            continue
        txt = (f"{e['nombre']}: {e['pct']:.1f} % ({e['volumen']:.1f} de {e['capacidad']:.1f} hm³). "
               f"Umbrales de trabajo, no oficiales: {u['amarillo']} %, {u['naranja']} %, {u['rojo']} %.")
        res["emb:" + e["id"]] = (n, e["nombre"], "Nivel bajo de embalse", txt)
    return res


# ------------------------------------------------------------------ canales
def enviar_ntfy(c, titulo, texto, nivel):
    r = requests.post(f"{c['servidor'].rstrip('/')}/{c['tema']}", data=texto.encode("utf-8"),
                      headers={"Title": titulo.encode("utf-8"), "Priority": PRIORIDAD_NTFY[nivel],
                               "Tags": "droplet,warning" if nivel else "droplet"}, timeout=20)
    r.raise_for_status()


def enviar_telegram(c, titulo, texto, nivel):
    r = requests.post(f"https://api.telegram.org/bot{c['token_bot']}/sendMessage",
                      json={"chat_id": c["chat_id"], "text": f"{titulo}\n{texto}"}, timeout=20)
    r.raise_for_status()


def enviar_correo(c, titulo, texto, nivel):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = titulo, c["desde"], ", ".join(c["para"])
    m.set_content(texto)
    with smtplib.SMTP(c["servidor"], c["puerto"], timeout=20) as s:
        s.starttls()
        if c.get("usuario"):
            s.login(c["usuario"], c["clave"])
        s.send_message(m)


CANALES = {"ntfy": enviar_ntfy, "telegram": enviar_telegram, "correo": enviar_correo}


def notificar(titulo, texto, nivel):
    enviado = False
    for nombre, fn in CANALES.items():
        c = CFG["notificaciones"].get(nombre, {})
        if not c.get("activo"):
            continue
        try:
            fn(c, titulo, texto, nivel)
            enviado = True
        except Exception as e:  # noqa
            log.error("Canal %s falló: %s", nombre, e)
    if not enviado:
        log.info("Sin canal activo o todos fallaron. Aviso no enviado: %s", titulo)
    return enviado


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar", action="store_true")
    args = ap.parse_args()
    if args.probar:
        ok = notificar("Prueba del panel hídrico", "Si lees esto, el canal de avisos funciona.", 1)
        print("Enviado" if ok else "No se envió por ningún canal. Revisa config.json y alertas.log")
        return 0 if ok else 1

    datos = json.loads((SALIDA / "datos.json").read_text(encoding="utf-8"))
    ruta_estado = BASE / "estado_alertas.json"
    previo = json.loads(ruta_estado.read_text(encoding="utf-8")) if ruta_estado.exists() else {}
    actual = evaluar(datos)
    nuevo_estado = dict(previo)
    for clave, (n, nombre, tipo, txt) in actual.items():
        antes = previo.get(clave, 0)
        if n == antes:
            continue
        if n > antes:
            titulo = f"Alerta {ETIQ[n]}: {nombre}"
        elif n == 0:
            titulo = f"Normalizado: {nombre}"
        else:
            titulo = f"Baja a {ETIQ[n]}: {nombre}"
        if notificar(f"{titulo} ({tipo})", txt, n):
            nuevo_estado[clave] = n
            log.info("Aviso enviado: %s", titulo)
        else:
            log.warning("Aviso pendiente de reintento: %s", titulo)
    ruta_estado.write_text(json.dumps(nuevo_estado, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
