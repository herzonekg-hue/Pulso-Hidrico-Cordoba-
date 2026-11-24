"""Comprueba que cada embalse está en la zona que corresponde a su municipio.
Fuente de los municipios: MITECO, informes de viabilidad de la CHG (presas de Córdoba) y SAIH Guadalquivir.
Ejecutar: python pruebas/test_zonas.py  (falla si alguien cambia una zona sin cambiar aquí la evidencia)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper_embalses import PROVINCIA

MUNICIPIOS = {
    "E33": ["Bélmez", "Peñarroya-Pueblonuevo", "Fuente Obejuna"], "E34": ["Bélmez", "Espiel", "Villaviciosa de Córdoba"],
    "E35": ["Villaviciosa de Córdoba", "Obejo"], "E36": ["Almodóvar del Río", "Villaviciosa de Córdoba", "Córdoba"],
    "E29": ["Adamuz"], "E30": ["Alcolea"], "E28": ["Montoro"], "E27": ["Montoro", "Cardeña"], "E26": ["Montoro", "Marmolejo"],
    "E32": ["Baena", "Alcaudete"], "E48": ["Iznájar", "Rute"], "E37": ["Espiel", "Hornachuelos"],
    "E38": ["Hornachuelos"], "E39": ["Hornachuelos"],
}
NORTE = {"Bélmez", "Peñarroya-Pueblonuevo", "Fuente Obejuna", "Espiel", "Pozoblanco", "Añora", "El Viso"}
SUR = {"Baena", "Iznájar", "Rute", "Hornachuelos"}
def zona_esperada(mun):
    if mun[0] in NORTE and "Espiel" not in mun[:1] or mun[0] == "Bélmez": return "norte"
    if mun[0] in SUR: return "sur"
    if mun[0] == "Espiel": return "sur"      # Bembézar: presa en sierra de Hornachuelos
    return "capital"
errores = [f"{k} {PROVINCIA[k][0]}: tiene {PROVINCIA[k][1]}, esperado {zona_esperada(m)}" for k, m in MUNICIPIOS.items() if PROVINCIA[k][1] != zona_esperada(m)]
faltan = set(PROVINCIA) ^ set(MUNICIPIOS)
if errores or faltan:
    print("ERROR de zonas:", errores, faltan); sys.exit(1)
print("Zonas correctas:", len(PROVINCIA), "embalses")
