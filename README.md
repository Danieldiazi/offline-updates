# Offline Updates

Espejo de actualizaciones de Joomla para entornos **sin acceso a internet**.

Se ejecuta en una máquina con internet y genera una carpeta que se copia (por USB, por ejemplo) a la red aislada. Esa carpeta sirve para dos cosas:

1. **Subir los paquetes a mano**: el ZIP del core con *Subir y actualizar* y el resto con *Instalar extensión*.
2. **Que cada Joomla vea las actualizaciones solo**: servida por HTTP dentro de la red aislada, actúa como servidor de actualizaciones. Se activa instalando un paquete que genera el propio script, **sin tocar la base de datos a mano**. El icono de actualizaciones se enciende y se actualiza con el botón habitual, como si hubiera internet.

Cubre el core de Joomla, los paquetes de idioma oficiales y cualquier extensión que publique un XML de actualización o releases en GitHub.

## Requisitos

- Python 3.8 o superior. Solo usa la biblioteca estándar; no hay nada que instalar.
- Funciona en Windows, Linux y macOS.

## Uso

Copia `config.example.json` como `config.json` y ajusta `url_base` a la dirección del servidor interno antes de ejecutar el programa.

```bash
python joomla_offline.py              # usa config.json junto al script
python joomla_offline.py -c otra.json # otra configuración
```

Cada ejecución:

1. Consulta las fuentes oficiales y se queda con la última versión estable de cada paquete.
2. Descarga solo lo que haya cambiado.
3. Verifica cada descarga con el hash SHA que publica la fuente. Si no coincide, la descarta.
4. Borra los ZIP de versiones anteriores, salvo que se configure lo contrario.
5. Regenera los XML del espejo, el paquete instalable, `LEEME.txt` e `index.html`.

Termina con código 0 si todo fue bien y 1 si algún paquete falló; el resto se procesa igualmente. Esto permite lanzarlo como tarea programada (cron o el Programador de tareas de Windows) para tener la carpeta siempre al día.

## Configuración (`config.json`)

| Clave | Qué es |
|---|---|
| `url_base` | URL por la que **los servidores Joomla** de la red aislada llegarán a la carpeta. Ej.: `http://servidor-interno/joomla-offline`. Va escrita en los XML, así que si cambia hay que volver a ejecutar el script. |
| `carpeta_salida` | Dónde se genera el espejo, relativo al `config.json`. |
| `ramas_joomla` | Versiones mayores de Joomla que tienes, ej. `["6"]` o `["5", "6"]`. Cada sitio solo verá actualizaciones de su propia rama. |
| `idiomas` | Códigos de idioma, ej. `["es-ES", "gl-ES"]`. |
| `carpeta_manual` | Carpeta (relativa al `config.json`) con paquetes descargados a mano. Por defecto `manual`; se crea sola en la primera ejecución. Ver abajo. |
| `manual_en_actualizaciones` | `true` (por defecto) para que Joomla ofrezca los paquetes de `manual/` en *Sistema → Actualizar*; `false` para que solo se copien. |
| `conservar_versiones_anteriores` | `true` para no borrar los ZIP antiguos. |
| `extensiones` | Lista de extensiones (ver abajo). |

### Extensiones

**Con XML de actualización** (lo más habitual: la URL que aparece en *Sistema → Sitios de actualización*):

```json
{ "nombre": "Attachments", "tipo": "xml",
  "url": "https://raw.githubusercontent.com/jmcameron/attachments/master/pkg_attachments/update_pkg.xml" }
```

**Con releases de GitHub** (para extensiones sin XML de actualización):

```json
{ "nombre": "User Tools", "tipo": "github", "repo": "Danieldiazi/joomla-user-tools", "patron_zip": "\\.zip$" }
```

El script baja el primer ZIP de la última release que cumpla `patron_zip` y lee el manifiesto que va dentro para saber qué extensión es (`element`, `type`, `client`). Si se deduce mal, se puede forzar con las claves `element`, `type`, `client` y `folder`. Con `targetplatform` se fija la compatibilidad declarada; por defecto, las ramas configuradas.

**Con lista de varias extensiones (`list.xml`, servidor de tipo *collection*)**: por ejemplo Gantry 5, que publica en una sola lista el framework y sus plantillas (Helium, Hydrogen) para Joomla 3, 4, 5 y 6. El script se queda solo con las entradas de las ramas de `ramas_joomla`, replica cada XML de detalle y su ZIP, y reescribe la lista:

```json
{ "nombre": "Gantry 5", "tipo": "coleccion",
  "url": "http://updates.gantry.org/5.0/joomla/list.xml",
  "otras_urls": ["http://updates.gantry.org/5.0/joomla/pkg_gantry5.xml"] }
```

- `otras_urls` (opcional): otros sitios de actualización de internet de esa extensión que tengas en Joomla. Solo sirve para que `LEEME.txt` te los liste para desactivarlos; la lista ya los cubre.
- `elementos` (opcional): para replicar solo parte de la lista, p. ej. `["pkg_gantry5", "g5_helium"]` si no usas Hydrogen.

Cuando un XML de actualización trae versiones para varias ramas de Joomla, en cualquiera de los tipos, el script elige la más alta compatible con tus ramas, no la más alta sin más.

**Repositorios con releases de varias familias** (por ejemplo, una por idioma): con `patron_tag` se elige la release estable más reciente cuyo tag cumpla el patrón. Las versiones en borrador y las preliminares se ignoran. Ejemplo, el paquete en español de Attachments:

```json
{ "nombre": "Attachments - Español", "tipo": "github", "repo": "jmcameron/attachments_translations",
  "patron_tag": "^Spanish-", "patron_zip": "es-ES.*\\.zip$" }
```

Si una extensión publica releases en GitHub **y** tiene XML de actualización, usa mejor `"tipo": "xml"`: respeta lo que declara su autor.

La API de GitHub admite 60 consultas por hora sin autenticar, de sobra para esto. Si hiciera falta, define la variable de entorno `GITHUB_TOKEN`.

### Paquetes descargados a mano

Algunas extensiones no tienen servidor de actualizaciones. Las plantillas de JoomLead (Wilson, Minima…), por ejemplo, se descargan desde su área de cliente. Para llevarlas junto al resto:

1. Descárgalas a mano y déjalas en la carpeta `manual/`, junto a `config.json`. Puedes organizarlas en subcarpetas.
2. Ejecuta el script. Las copia tal cual a `joomla-offline/manual/` y las lista en `LEEME.txt`, en `index.html` y en el resumen de pantalla.
3. **El espejo hace de servidor de actualizaciones para ellas.** El script lee el manifiesto de cada ZIP (nombre interno, tipo y versión) y publica una lista en `manual_xml/list.xml`. Así, en los Joomla que ya tengan esa extensión instalada con una versión anterior, aparece en *Sistema → Actualizar → Extensiones* como cualquier otra. Si hay varias versiones de la misma extensión en `manual/`, se ofrece la más alta.
4. También se pueden instalar a mano con *Sistema → Instalar → Extensiones → Subir paquete*. Es la forma de instalar algo que el sitio todavía no tiene.

Junto a cada ZIP, el resumen indica qué se hace con él:

- `en Actualizar: template jl_wilson 2.1.1`: se ofrece como actualización.
- `versión antigua, no se ofrece`: hay otra más nueva de la misma extensión.
- `solo instalación manual (…)`: el ZIP no tiene un manifiesto de Joomla legible.

El sitio de actualización de los paquetes manuales es siempre la misma lista, así que **añadir o quitar ZIP en `manual/` no obliga a reinstalar `joomla-offline-updates.zip`**. Basta con copiar la carpeta nueva al servidor.

Si prefieres que los paquetes manuales **no** se ofrezcan como actualización (por ejemplo, plantillas con ficheros propios que hay que revisar antes de actualizar), pon `"manual_en_actualizaciones": false` en `config.json`. En ese caso solo se copian y se listan.

La carpeta del espejo se mantiene sincronizada: si borras un ZIP de `manual/`, desaparece también de `joomla-offline/manual/` en la siguiente ejecución.

Probado en Joomla 6.2: con una plantilla 1.0 instalada y su versión 1.1 en `manual/`, Joomla ofreció la actualización, la versión 1.0 repetida no se ofreció, un ZIP que no era de Joomla se marcó como solo manual, y la instalación desde el espejo dejó la plantilla en 1.1 sin actualizaciones pendientes.

## Qué genera

```
joomla-offline/
├── LEEME.txt                     versiones, qué ha cambiado, avisos y errores
├── index.html                    lo mismo, navegable y con enlaces de descarga
├── core/
│   ├── list.xml                  ← URL personalizada para Joomla Update
│   ├── joomla6.xml
│   └── Joomla_6.x.y-Stable-Update_Package.zip
├── idiomas/
│   ├── translationlist_6.xml
│   └── 6/  es-ES_details.xml, es-ES_…zip, gl-ES_…
├── extensiones/
│   └── attachments/  update.xml, attachments-4.2.5.zip
├── manual/                      ← copia de tu carpeta manual/ (paquetes descargados a mano)
├── manual_xml/                  ← servidor de actualizaciones para esos paquetes
├── joomla-offline-updates.zip   ← paquete que se instala en cada Joomla
└── estado.json                   uso interno (versiones de la última ejecución)
```

Los ZIP no se modifican nunca: son los publicados, verificados por hash.

## Puesta en marcha en la red aislada

### Opción A: solo subir a mano

No hay que configurar nada. Abre `index.html` o `LEEME.txt` para ver qué hay nuevo:

- **Core**: *Sistema → Joomla Update → Subir y actualizar*, con el ZIP de `core/`.
- **Idiomas y extensiones**: *Sistema → Instalar → Extensiones → Subir paquete*. Instalar encima de una versión anterior es una actualización.

### Opción B: que Joomla detecte las actualizaciones

Se hace una sola vez por sitio, toda desde la administración de Joomla:

1. **Servir la carpeta por HTTP** en la URL indicada en `url_base`. Sirve cualquier servidor web interno. Lo más sencillo es copiarla dentro del webroot del propio servidor de Joomla, por ejemplo `/var/www/html/joomla-offline/`. Importante: quien descarga es el **servidor** de cada Joomla, no tu navegador, así que la URL tiene que resolverse desde esos servidores.
2. **Core**: *Sistema → Joomla Update → Opciones*. En *Canal de actualización* elige **URL personalizada** y pon `<url_base>/core/list.xml`. Guarda. Desde la línea de comandos equivale a `php cli/joomla.php core:update:channel custom --url=<url_base>/core/list.xml`.
3. **Idiomas y extensiones**: *Sistema → Instalar → Extensiones → Subir paquete* con `joomla-offline-updates.zip`. Aparece como la extensión **"Offline Updates"** (tipo *Archivo*) y Joomla crea él mismo los sitios de actualización del espejo.
4. **Recomendado**: en *Sistema → Sitios de actualización*, **desactiva** (no borres) los sitios de internet que lista `LEEME.txt`. Así no salen avisos de "no se pudo abrir…" en cada búsqueda.
5. *Sistema → Actualizar → Extensiones → Buscar actualizaciones*.

Desde entonces, cada vez que copies una carpeta nueva encima de la anterior, los sitios verán las actualizaciones en su siguiente comprobación. **El paquete solo hay que reinstalarlo cuando `LEEME.txt` diga que es NUEVO**, porque has añadido o quitado algo en `config.json`. Su versión solo cambia en ese caso. Se instala encima del anterior.

**Volver a internet**: desinstala "Offline Updates" (Joomla borra sus sitios), reactiva los sitios del paso 4 y pon el canal de Joomla Update en **Por defecto**.

### Por qué funciona el paquete

Al buscar actualizaciones, Joomla consulta **todos los sitios de actualización activos** y asigna cada actualización encontrada a la extensión instalada por su nombre interno (`com_usertools`, `pkg_attachments`…). No le importa a qué extensión esté enlazado el sitio. El paquete es una extensión vacía, de tipo *file*, que solo instala un `LEEME.txt` en `media/`; su manifiesto declara las URLs del espejo como servidores de actualización, y al instalarla Joomla los da de alta.

Consecuencias:

- **No se toca la base de datos a mano ni se modifica ninguna URL original.** Todo pasa por el instalador de Joomla.
- **Aguanta las actualizaciones**: al actualizar una extensión, Joomla vuelve a registrar su URL original (que ya existe), sin reactivarla ni tocar los sitios del espejo.
- **Aguanta "Reconstruir"** en *Sitios de actualización*: Joomla regenera los sitios desde los manifiestos, incluido el del paquete, así que el espejo se recrea solo. Eso sí, los sitios de internet volverán a quedar activos y habrá que desactivarlos de nuevo.
- Funciona igual para extensiones que no declaran servidor de actualizaciones.

Probado en un Joomla 6.2.0 real: con el sitio original de la extensión desactivado, detecta la actualización de un componente a través del espejo; tras actualizar, los sitios siguen igual; al desinstalar el paquete, sus sitios desaparecen y no queda nada pendiente; y el canal personalizado del core detecta una versión nueva y vuelve a TUF al elegir "Por defecto".

## Cosas a tener en cuenta

- **Seguridad del core.** Con el canal "URL personalizada" Joomla usa el formato XML clásico en lugar de TUF, que es la verificación con firma criptográfica de los metadatos. Joomla sigue comprobando el SHA-512 del ZIP, pero ese hash lo aporta tu espejo. Este script lo obtiene del repositorio oficial por HTTPS y verifica la descarga con él, así que la cadena queda protegida hasta tu USB. A partir de ahí, la integridad depende de quién tenga acceso a la carpeta.
- **Compatibilidad declarada.** Si una extensión no declara compatibilidad con tu versión de Joomla, Joomla no ofrecerá su actualización, y `LEEME.txt` lo avisa. Ejemplo actual: Attachments 4.2.5 declara hasta Joomla 6.1. Mientras su autor no lo amplíe, en un 6.2 hay que subirla a mano. Con internet pasaría exactamente lo mismo.
- **Cambio de versión mayor** (5 → 6). Cada sitio solo ve actualizaciones de su propia rama. El salto de mayor se hace subiendo el paquete a mano, tras las comprobaciones previas que pide Joomla.
- **Base de datos**: indiferente (MySQL, MariaDB o PostgreSQL); todo pasa por la interfaz de Joomla.

## Registro de cambios

El espejo copia el changelog declarado mediante `changelogurl` en las fuentes XML (también idiomas y colecciones), conserva las entradas de la extensión y versión publicadas y cambia el enlace a una URL interna. Admite URLs relativas. Para fuentes de tipo `github`, genera el changelog a partir del cuerpo de la release, si existe, conservando Markdown como texto. No modifica los ZIP.

Si falla un changelog declarado o no contiene la versión requerida, omite su enlace y registra la incidencia sin bloquear el paquete; termina con código 1. Después de regenerar y copiar el espejo, busca actualizaciones en Joomla para refrescar los metadatos.

### Notas alternativas para fuentes XML de GitHub

Si falta `changelogurl` y el ZIP se descarga de github.com/propietario/repositorio/releases/download/tag/, el script consulta las notas de ese tag exacto. Solo las publica si el tag (sin prefijo v/V) coincide con la versión del XML y la release tiene notas y no es preliminar ni borrador. No usa la última release indiscriminadamente. El XML original sigue determinando el paquete y la compatibilidad. Esta alternativa no se aplica al core, que tiene otro procesamiento.
