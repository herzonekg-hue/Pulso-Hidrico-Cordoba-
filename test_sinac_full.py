#!/usr/bin/env python3
"""Imprime el JSON completo de elmapadelagua.com para Pozoblanco (14066)."""
import urllib.request, ssl, json

ctx = ssl._create_unverified_context()
req = urllib.request.Request(
    "https://elmapadelagua.com/api/dureza?ine=14066",
    headers={"User-Agent": "Mozilla/5.0 (compatible; test/1.0)", "Accept": "application/json"}
)
with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
    body = r.read().decode("utf-8")
    print("=== FULL JSON ===")
    parsed = json.loads(body)
    print(json.dumps(parsed, indent=2, ensure_ascii=False))
    print("=== KEYS TOP LEVEL ===", list(parsed.keys()))
    if "data" in parsed:
        print("=== KEYS data ===", list(parsed["data"].keys()))
