#!/usr/bin/env python3
"""
Diagnóstico de red para scraper SINAC.
Prueba de conectividad a elmapadelagua.com y sinacv2.sanidad.gob.es
desde el entorno GitHub Actions.
"""
import urllib.request, urllib.error, ssl, socket, json

ctx = ssl._create_unverified_context()

URLS = [
    "https://elmapadelagua.com/api/dureza?ine=14066",
    "https://elmapadelagua.com/",
    "https://sinacv2.sanidad.gob.es/CiudadanoWeb/ciudadano/informacionRedes.do?codMunicipio=14066",
    "https://httpbin.org/get",
    "https://api.github.com/",
]

for url in URLS:
    host = url.split("/")[2]
    # DNS
    try:
        ip = socket.gethostbyname(host)
        print(f"DNS OK: {host} -> {ip}")
    except Exception as e:
        print(f"DNS FAIL: {host} -> {e}")
        continue

    # HTTP
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; test/1.0)",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            body = r.read(200).decode("utf-8", errors="replace")
            print(f"HTTP {r.status} OK: {url[:60]} | body[:50]: {body[:50]!r}")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {url[:60]}")
    except Exception as e:
        print(f"HTTP FAIL: {url[:60]} -> {type(e).__name__}: {e}")

