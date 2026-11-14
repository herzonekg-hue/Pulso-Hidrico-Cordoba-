# Pulso hídrico de Córdoba. Guía de instalación

Panel web que muestra los 15 embalses de la provincia de Córdoba (incluida La Colada, de la cuenca del Guadiana) y 19 puntos de río con umbral oficial de aviso, y que envía notificaciones cuando una lectura cruza un umbral.

Versión: 4 de noviembre de 2026.

## 1. Qué contiene el paquete

| Archivo | Función |
|---|---|
| index.html | Panel visual. Es una página estática, sin dependencias externas. Lee datos.json y serie.json cada 15 minutos. Si no existen, muestra datos de muestra y lo avisa. |
| scraper_saih.py | Descarga embalses y niveles de río del SAIH del Guadalquivir y busca La Colada. Escribe datos.json y serie.json. |
| alertas.py | Compara datos.json con los umbrales y notifica solo cuando cambia el nivel de un punto. |
| config.example.json | Plantilla de configuración: embalses, zonas, municipios, umbrales y canales de aviso. instalar.sh la copia como config.json, que es el único archivo que se edita y que nunca se sube al repositorio. |
| instalar.sh | Instalación en Linux con cron. |
| pruebas/ | Dos páginas HTML de ejemplo que reproducen la estructura observada del SAIH, para probar el scraper sin red. |

## 2. Requisitos

Servidor Linux con Python 3.9 o superior y un servidor web que sirva archivos estáticos (Apache, Nginx o similar). Salida HTTPS hacia estos dominios:

1. www.chguadalquivir.es (obligatorio).
2. www.embalses.net (respaldo semanal de La Colada).
3. El servidor de avisos que se elija: ntfy.sh o uno propio, api.telegram.org, o el servidor SMTP del área.

Si el servidor es Windows, el código Python es válido, pero hay que programar la ejecución con el Programador de tareas y sustituir instalar.sh por pasos manuales.

## 3. Instalación

    sudo bash instalar.sh /var/www/html/hidrico /opt/hidrico-cordoba

El script copia la aplicación a /opt/hidrico-cordoba, publica solo index.html y los datos en la carpeta web, crea un entorno virtual con requests, beautifulsoup4 y lxml, programa una ejecución cada 15 minutos en /etc/cron.d/hidrico-cordoba y lanza una primera lectura.

El archivo config.json queda fuera de la carpeta web a propósito, porque puede contener el token del bot de Telegram o la clave del correo. No debe servirse por HTTP.

Comprobación: abrir /opt/hidrico-cordoba/scraper.log tras la primera ejecución. Debe aparecer la línea "datos.json actualizado: 15 embalses, 19 puntos de río".

## 4. Zonas y municipios

La zona se asigna por el municipio donde se sitúa la presa. No se usa el sistema de abastecimiento ni la confederación. Para cambiar un embalse de zona basta editar el campo "zona" en config.json.

| Zona | Embalses | Municipios de la presa |
|---|---|---|
| Norte | La Colada | El Viso, Belalcázar |
| Norte | Sierra Boyera | Belmez, Peñarroya-Pueblonuevo, Fuente Obejuna |
| Norte | Puente Nuevo | Villaviciosa de Córdoba, Espiel |
| Capital y sierra | San Rafael de Navallana | Córdoba |
| Capital y sierra | Guadanuño | Córdoba, Obejo, Villaviciosa de Córdoba |
| Capital y sierra | Guadalmellato | Adamuz, Obejo |
| Este | Arenoso | Montoro |
| Este | Yeguas | Montoro |
| Este | Martín Gonzalo | Montoro, Cardeña |
| Oeste | Bembézar | Hornachuelos, Espiel |
| Oeste | Hornachuelos (contraembalse) | Hornachuelos |
| Oeste | Retortillo | Hornachuelos, Puebla de los Infantes (Sevilla) |
| Oeste | La Breña II | Almodóvar del Río |
| Sur | Vadomojón | Baena, Alcaudete (Jaén) |
| Sur | Iznájar | Iznájar, Rute |

Correcciones respecto a la versión anterior del panel: Vadomojón se sitúa en Baena, en la Campiña Sur, no en la capital. Hornachuelos, Bembézar y Retortillo pertenecen a la zona oeste, junto al límite con Sevilla. Yeguas, Arenoso y Martín Gonzalo corresponden a Montoro y su sierra. Hornachuelos y Bembézar no son zona norte ni zona sur.

Municipios comprobados en fuentes concordantes: el contraembalse de Hornachuelos está en el término de Hornachuelos. La presa de Yeguas está en el límite entre Córdoba y Jaén, entre Montoro y Marmolejo, según el inventario de la Junta de Andalucía; se asigna a Córdoba y a la zona Este por el lado cordobés. El vaso de Iznájar se extiende por otras provincias (consta Cuevas de San Marcos, en Málaga); la presa está en Córdoba.

## 5. Umbrales

Ríos. Son los umbrales oficiales amarillo, naranja y rojo que publica el SAIH del Guadalquivir. El scraper los lee de la página en cada ejecución y, si difieren de los de config.json, lo anota en scraper.log. Si el SAIH no los publicara en algún momento, se usan los de config.json.

Embalses. El método oficial de sequía no usa un porcentaje de llenado fijo. El Plan Especial de Sequía del Guadalquivir calcula un indicador de escasez por Unidad Territorial de Escasez (UTE), con cuatro escenarios (normalidad, prealerta, alerta y emergencia) y umbrales propios de cada sistema y de cada mes. Por eso los valores de 40, 25 y 15 por ciento del panel son una referencia propia, no oficial, y se marcan así en el panel y en los avisos.

Cada tarjeta indica la UTE oficial cuando se ha podido verificar: Sierra Boyera (UTE 0707), Guadalmellato (UTE 0712), Bembézar y Retortillo (UTE 0801), Martín Gonzalo (UTE 0705) y Regulación General (UTE 0701) para Puente Nuevo, La Breña II y San Rafael de Navallana. Para Arenoso, Yeguas, Vadomojón, Guadanuño, Hornachuelos e Iznájar no se ha verificado su UTE y no se indica.

El estado oficial vigente de cada UTE lo publica la CHG en su página de sequía y escasez (IDE-CHG), pero esa página prohíbe el acceso automatizado, así que el scraper no puede leerla. Opciones: introducir cada mes el escenario oficial a mano, o pedir a la CHG un acceso a ese dato. Hasta entonces el panel avisa por los umbrales propios y por los oficiales de los ríos.

Los contraembalses, como Hornachuelos, no generan aviso por nivel bajo porque funcionan casi llenos por diseño.

Pendiente de decisión del área: si se quieren avisos por caudal mínimo en ríos. No existe umbral oficial de mínimos en la tabla del SAIH.

## 6. La Colada

Pertenece a la Confederación Hidrográfica del Guadiana y no aparece en el SAIH del Guadalquivir.

Estado actual de la integración: el panel la muestra junto al resto, con el dato semanal del MITECO obtenido de embalses.net (último dato verificado: 47 hm³ el 19/10/2026). No hay medición automática, y el panel lo indica en su tarjeta.

Para conectar una fuente en tiempo real. SIRA Guadiana (https://www.siraguadiana.com) publica embalses y niveles de río, pero es una aplicación que carga los datos de forma dinámica y no se ha podido verificar un punto de acceso público estable. Dos vías posibles:

1. Abrir siraguadiana.com en un navegador, entrar en la sección de embalses, abrir las herramientas de desarrollo, pestaña Red, y localizar la petición que devuelve los datos de La Colada.
2. Preguntar a la Confederación Hidrográfica del Guadiana si ofrece un acceso para organismos públicos.

Si se localiza una dirección que devuelva un JSON con las claves "volumen_hm3" y "fecha", basta escribirla en config.json, campo la_colada.endpoint_tiempo_real. Si el formato es otro, se adapta la función leer_la_colada() de scraper_saih.py.

Situación del abastecimiento, con fuentes. Desde marzo de 2023, tras la conexión de emergencia ejecutada por la CHG Guadiana, el agua de La Colada llegó a la ETAP de Sierra Boyera mediante bombeo (informes del MITECO y de EMPROACSA). El 31 de marzo de 2026 La Voz de Córdoba informó de que la CHG Guadiana denegó la concesión de aguas superficiales que solicitaba la Diputación para el abastecimiento del norte, y de la discrepancia sobre las obras de conexión definitiva con Sierra Boyera. Esa información no permite afirmar si hoy se bombea agua o no. El panel lo expresa como "Concesión denegada" y deja el uso actual pendiente de contrastar con el gestor del abastecimiento. No se ha podido verificar la cita atribuida a la Diputación sobre el estado de la estación de bombeo, y por eso no figura.

## 7. Avisos

Se configuran en config.json, apartado "notificaciones", activando uno o varios canales:

1. ntfy: notificaciones push al móvil sin cuenta, con la aplicación ntfy. El tema debe ser largo y privado. Puede usarse el servidor público o uno propio del área.
2. Telegram: se necesita un bot propio (token) y el identificador del chat.
3. Correo: servidor SMTP con STARTTLS.

Prueba de un canal:

    /opt/hidrico-cordoba/venv/bin/python /opt/hidrico-cordoba/alertas.py --probar

Funcionamiento: se avisa solo cuando un punto sube, baja o vuelve a la normalidad, no en cada lectura. Si ningún canal está activo o todos fallan, el aviso queda pendiente y se reintenta en la siguiente pasada.

Alternativa oficial que complementa a lo anterior: el menú del SAIH del Guadalquivir incluye un bot de Telegram y una sección de gestión de avisos por SMS. No se han podido comprobar sus condiciones de acceso desde el entorno de desarrollo.

## 8. Qué se ha probado y qué no

Probado: el análisis de las dos páginas del SAIH con archivos de ejemplo que reproducen la estructura observada, el cálculo de niveles de alerta con valores forzados, el control de duplicados de avisos, la sintaxis de los scripts y la visualización del panel en un navegador móvil.

No probado: la descarga real desde chguadalquivir.es, porque el entorno de desarrollo no tenía acceso a ese dominio, y el envío real por ntfy, Telegram o correo. Si el SAIH cambia el diseño de sus páginas, el scraper lo registra en scraper.log y conserva el último datos.json válido. Las funciones a revisar serían parse_embalses y parse_aforos.

Los datos automáticos del SAIH llevan el aviso oficial "Datos no contrastados". El panel no sustituye los avisos de Protección Civil, AEMET ni la Confederación.

## 9. Problemas habituales

1. El panel muestra el aviso de datos de muestra: datos.json no está en la carpeta web. Revisar la variable HIDRICO_SALIDA del cron y los permisos.
2. El panel muestra horas de antigüedad: el cron no se ejecuta o el SAIH no responde. Revisar scraper.log.
3. El log indica "EmbalNiv sin filas reconocibles": cambió el diseño de la página del SAIH. Revisar parse_embalses.
4. Un embalse figura como dato antiguo: dejó de aparecer en la tabla del SAIH. Se conserva su último valor.
