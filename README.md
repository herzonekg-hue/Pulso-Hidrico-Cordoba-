# Pulso hídrico de Córdoba

Panel web con los 15 embalses de la provincia de Córdoba (incluida La Colada, de la cuenca del Guadiana) y 19 puntos de río con umbral oficial de aviso. Envía notificaciones cuando una lectura cruza un umbral.

Proyecto Hídrico, AGS Norte de Córdoba.

## Contenido

| Archivo | Función |
|---|---|
| index.html | Panel visual estático. Lee datos.json y serie.json. Sin ellos muestra datos de muestra y lo avisa. |
| scraper_saih.py | Lee el SAIH del Guadalquivir y busca La Colada. Escribe datos.json. |
| alertas.py | Evalúa umbrales y notifica solo cuando cambia el nivel de un punto. |
| config.example.json | Plantilla de configuración. Se copia a config.json, que no se sube al repositorio. |
| instalar.sh | Instalación en Linux con cron. |
| pruebas/ | Páginas de ejemplo para probar el lector sin red. |
| LEEME_INFORMATICO.md | Guía técnica completa: zonas, umbrales, La Colada, avisos y límites. |

## Inicio rápido

    cp config.example.json config.json
    python3 -m venv venv && venv/bin/pip install requests beautifulsoup4 lxml
    venv/bin/python scraper_saih.py --fixture-dir pruebas    # prueba sin red
    sudo bash instalar.sh /var/www/html/hidrico /opt/hidrico-cordoba

## Aviso importante

Los datos automáticos del SAIH no están contrastados. Este panel no sustituye los avisos de Protección Civil, AEMET ni la Confederación. Los umbrales de nivel bajo de embalse son una referencia propia, no oficial.

El archivo config.json puede contener el token de un bot o la clave de un correo. Está en .gitignore y no debe subirse.
