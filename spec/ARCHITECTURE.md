# Arquitectura y decisiones

## Objetivo

MITO significa **My Integrated Terminal Orchestrator**.

Continuar la idea de EAP simplificando el modelo de datos. MITO es un proyecto
independiente, no utiliza el motor de Scoop ni depende de una instalación de EAP.
El catálogo público de Scoop es la fuente del manifiesto; el motor de instalación
y activación es Python. Pocketools aporta paquetes compartidos de comandos.

## Módulos

| Módulo | Responsabilidad |
| --- | --- |
| `bootstrap.ps1`, `bootstrap.py` | Obtener y preparar el runtime privado fijado |
| `storage.py` | Rutas confinadas, JSON atómico, exclusión de mutaciones |
| `transfer.py` | HTTP/HTTPS, hashes, extracción ZIP validada y limitada |
| `catalog.py` | Snapshots de buckets, resolución y clasificación |
| `engine.py` | Dependencias, instalación, profiles, entorno y lanzamiento |
| `shortcuts.py`, `icons.py` | Accesos de apps al escritorio e iconos Pillow multirresolución |
| `ui.py` | Navegación Textual y operaciones en workers |
| `self_update.py`, `self_update_ui.py`, `update_checks.py` | Consulta diaria configurable de releases, confirmación y actualización desde un CMD externo |
| `i18n.py`, `locales/*.json` | Bundles de textos propios de MITO; idioma por gestor, también en workers |
| `__main__.py` | CLI sobre los mismos servicios de la interfaz |
| `diagnostics.py`, `startup.py`, `error_tracking.py` | Log local rotado desde arranque, captura de errores y cola persistente de PostHog |

### Trazas remotas opcionales

El log local funciona independientemente de PostHog. PowerShell escribe desde el
bootstrap inicial; diagnostics.py usa sólo la biblioteca estándar y continúa desde
bootstrap.py. Ambos escriben JSON por línea en logs/mito.log con un límite
de 1 MiB y tres copias .1..3, bajo un lock de byte compartido entre procesos. Cada
registro está acotado; un error de log no sustituye el error de aplicación. Si falla
esa ubicación se intenta %TEMP%/MITO-logs/<hash-de-raíz> y se indica la ruta. Sin
permisos en ambas ubicaciones sólo queda la consola. No se guarda un transcript de
consolas, variables, argumentos ni contenido de archivos de usuario.

CLI, UI, instalación, actualizaciones, Pocketools, configuración, hilos no controlados
y trabajos MCP aportan errores; las fases de instalación añaden contexto explícito.
startup.py guarda fallos al importar CLI y mito.cmd registra códigos nativos negativos
o 9009 a través de PowerShell. No captura fallos internos de aplicaciones externas,
cierres forzados, errores anteriores a que PowerShell pueda ejecutar el script o todos
los códigos de salida de procesos hijos. El bootstrap registra versión de Windows,
bits de proceso, PowerShell/Python, fase y salida de preparación del runtime.

Las nuevas distribuciones incluyen el proyecto público europeo de MITO y envío
activado, por autorización del propietario. `posthog.enabled=false` lo desactiva sin
desactivar el log. No se exporta la configuración privada de la instancia; las
actualizaciones conservan la configuración existente. Se envían tipo, mensaje
depurado, funciones, líneas, versión, operación, fase y componente cuando están
disponibles. Se ocultan patrones de credenciales, claves, queries de URL y rutas
conocidas de usuario/instancia; no es un detector universal de secretos en texto libre.
No se incluyen variables locales ni código fuente. UUID aleatorio por proceso y
`$process_person_profile=false`, además de `$geoip_disable=true`.

Los informes se escriben atómicamente antes del envío en logs/pending, con UUID y
fecha originales y huella del destino, sin almacenar la clave del proyecto. Hasta
100 informes de 128 KiB por ubicación, caducidad de siete días revisada al usar la
cola; al llenarse se descartan los más antiguos. Un cambio de proyecto no reenvía
informes del anterior. Python registra hasta 20 trazas distintas e intenta hasta 20
envíos por proceso; reintenta pendientes al arrancar, con un hilo daemon temporal.
PowerShell intenta hasta tres pendientes al terminar/fallar el bootstrap. Sin timers
ni servicios, y sin reintentos en bucle en una sesión. Fallos de envío se registran
localmente y conservan la cola; recepción HTTP elimina el pendiente. Dos procesos
pueden intentar el mismo UUID: la entrega no garantiza exactamente una recepción.
Python espera hasta seis segundos al cerrar; DNS puede exceder los timeouts de socket
y un envío sin confirmar permanece en disco. `posthog-check` muestra el resultado
HTTP del diagnóstico; `logs` muestra ubicaciones, sin provocar errores ni enviar.

## Contratos

El bootstrap inicial muestra una bienvenida y requiere respuesta afirmativa antes
de crear tools/staging o descargar. Cancelar sólo registra el diagnóstico local;
no se considera error remoto. La confirmación se repite tras una preparación
incompleta y se omite cuando existe el marcador del runtime. El actualizador usa
`-Approved` tras su confirmación previa; `-PreviewWelcome` sólo muestra el diálogo.

`profiles/<name>.json` contiene únicamente `workspace` relativo a `workspaces/`,
`apps` como lista ordenada de IDs `bucket/app` y `env` como objeto de cadenas.
No hay campo schema; el formato anterior sólo se convierte mediante migrate-storage.
El nombre viene del archivo.
El inventario instalado se descubre mediante `.mito.json` en cada payload y contiene
el manifiesto completo una sola vez por instalación. Resolver selecciones no consulta
el catálogo; una referencia ausente produce un error explícito al activar el entorno.
Cada bucket/app tiene una única versión instalada en core/apps/<bucket>/<app>.
Versión y huella del manifiesto viven sólo en .mito.json, no en rutas ni selecciones.

Activar/desactivar cambia el profile sin instalar ni borrar payloads. Componentes
distintos pueden estar activos simultáneamente, incluidos runtimes de la misma
familia. No se detectan conflictos de runtime: PATH conserva orden de activación; env_set y aliases repetidos se
sobrescriben en ese orden, sin promesa de coherencia entre comandos y variables.

Los snapshots de bucket almacenan el origen y los manifiestos originales.
Los ZIP remotos se validan y sus manifiestos se leen directamente, sin extraer
el repositorio. La clasificación valida sintaxis de rutas sin acceder al disco;
las operaciones de archivos mantienen resolución física y confinamiento.
Importar una fuente local los copia; nunca deja un enlace necesario a C:\scoop.
Refrescar un bucket no modifica los locks de los profiles ni ejecuta código.
Al abrir el gestor, un worker consulta como máximo una vez al día local los
manifiestos de las claves instaladas,
sin depender de la activación y deduplicando claves. Lee JSON individuales de
la rama predeterminada de GitHub (bucket/ o raíz) o de la carpeta local configurada;
usa el origen del snapshot cuando no hay configuración. Fusiona cada resultado
atómicamente bajo lock, conservando las otras entradas y el manifiesto del payload.
Los errores conservan el catálogo anterior y se muestran en el log. No instala
actualizaciones automáticamente ni sincroniza catálogos completos al arrancar.

«Buscar actualizaciones» en ACCIONES repite las consultas de componentes instalados
y de MITO sin reutilizar la caché diaria; conserva la prioridad del paquete offline
de MITO y registra el nuevo resultado para siguientes arranques. Espera las consultas
del arranque antes de comenzar; cada servicio informa sus errores independientemente.
Instalar desde el catálogo refresca sólo la clave elegida desde su origen. Si falla,
conserva el catálogo y detiene esa instalación. Una versión numéricamente posterior
a la mostrada exige un diálogo con ambas versiones, Instalar y Cancelar. La aprobación
usa el plan del motor, que vuelve a validarse antes de publicar; las confirmaciones
de descargas sin hash, incluidas dependencias, siguen siendo independientes.

La instalación resuelve y valida las dependencias antes de descargar. Comprueba
hashes declarados, extrae todo el plan en staging y valida las entradas `bin`.
El log distingue descarga, extracción, preparación y validación. La extracción MSI
informa de cada CAB y del número de archivos completados; la copia al payload
también emite recuentos cada 1.000 archivos y al terminar, sin timers ni procesos
auxiliares. Los recuentos describen cada fase, no un porcentaje global estimado.
Publica las bases en rutas estables y reconstruye las copias aisladas. Conserva
los árboles anteriores en staging hasta publicar profiles y lanzadores; los errores
previos a la limpieza revierten la sustitución. Instalar un manifiesto diferente
del mismo bucket/app también sustituye el componente, incluidas dependencias.
La generación de accesos de apps es explícita desde la UI o `app-shortcut`.
El cuarto elemento de la entrada shortcuts seleccionada puede declarar un icono
relativo .ico/.exe/.dll. Se resuelve en el payload del profile, se compone con la
etiqueta y se usa también al regenerar accesos registrados (app actual o installation
legado). Icono ausente usa el ejecutable; si no puede leerse un icono, se usa MITO.
Rutas externas/traversal y formatos o índices de recurso no soportados se rechazan
en el catálogo. No se elige el icono de otra entrada de lanzamiento.
Las mutaciones usan un lock liberado por el OS
al terminar el proceso. Un cierre abrupto puede dejar staging para diagnóstico.

La persistencia genérica usa `home/persist/<bucket>/<app>/`. Los defaults quedan
guardados en `.mito-defaults` del payload. Se crean junctions para directorios y
hardlinks para archivos; el motor registra los enlaces y los reconstruye antes
de activar, también tras mover la instancia. Los enlaces de un ZIP/TAR/7z nunca
se aceptan como enlaces gestionados. Los componentes sin la combinación persist + shortcuts comparten payload y datos.
Los que declaran ambos campos utilizan copias y persistencia separadas por profile.
No hay políticas por ID. pre_install y después installer.script o installer.file se ejecutan en
PowerShell después de extraer y antes de validar/publicar;
post_install se omite y se comunica en detalles y log. Los demás pasos no soportados
bloquean la instalación. La compatibilidad se
calcula sólo a partir de campos del manifiesto. No se generan variables particulares
de Node ni VS Code: únicamente se aplican las declaradas mediante env_set.

## Home y procesos

### Pocketools compartidas

pocketools.py lee pocketool.<nombre> de config.properties. Cada origen es un Git
HTTP/HTTPS, una carpeta local absoluta heredada o la ruta portable pocketools/<nombre>.
Esta última debe coincidir exactamente con el nombre configurado.
Exige pocketool.json en la raíz con info como texto no vacío. La UI muestra las
configuradas y un botón Actualizar sólo para orígenes externos. No se gestionan runtimes, dependencias,
activaciones por profile, submódulos ni scripts de instalación.

Las fuentes remotas requieren Git en el PATH o un git.exe declarado por un componente
instalado. Clone superficial de la rama predeterminada sin checkout, sin hooks y sin
submódulos; git archive exporta el snapshot y el extractor validado de MITO lo prepara.
No se ejecutan filtros smudge ni se conserva .git. Git utiliza el proxy configurado;
sin prompt de credenciales, necesita autenticación ya disponible en Git para repos privados.
No se soporta Git LFS ni selección de ramas. Las fuentes locales copian su árbol actual
sin .git y rechazan enlaces. pocketool.json identifica el formato, no certifica sus scripts.

Para orígenes externos, la nueva copia se valida en staging antes de sustituir pocketools/<nombre> bajo lock.
Si falla la obtención o validación, se conserva la anterior. Actualizar reemplaza las
ediciones locales de la copia sincronizada y elimina archivos retirados del origen.
Sólo las raíces de paquetes configurados y válidos entran en PATH de todos los profiles;
no se añaden subcarpetas. Quitar la configuración retira esa entrada del nuevo PATH,
sin borrar archivos. pocketools/ queda fuera de Git y de las releases.

La autoría vive en pocketool_authoring.py y pocketool_editor_ui.py. Crear paquete sólo
pide nombre, descripción y Git opcional; crea pocketools/<nombre> y registra esa ruta
relativa a la instancia, sin selector de ubicación ni navegación por carpetas.
Admite reutilizar una carpeta vacía; nunca adopta silenciosamente archivos existentes.
Ese directorio es el árbol de trabajo: update valida in situ, prepara lanzadores y
conserva .git y auxiliares, sin staging ni sustitución. Se mantienen los orígenes
absolutos previos por compatibilidad, sin migrarlos automáticamente. Los demás
directorios internos siguen bloqueados como fuentes editables.
El editor exige nombres ASCII sin extensión, espacios ni nombres de dispositivo,
escribe UTF-8/CRLF (BOM para PS1), limita el contenido a 1 MiB y compara una huella
del archivo original antes de reemplazarlo atómicamente. Un cambio de nombre o tipo
retira el archivo anterior tras guardar el nuevo. No sobreescribe colisiones.
Guardar deja el comando disponible directamente; sólo los orígenes externos heredados
requieren publicar una copia. Un fallo posterior a la escritura se comunica sin
descartar el archivo guardado. No existe Aplicar cambios: los auxiliares del Explorador
ya viven en el paquete local, sin observadores ni sincronización en caliente.
Editar y Acceso directo se habilitan sólo con comando seleccionado (y profile para
el acceso); los handlers y script_path rechazan valores NoSelection sin TypeError.

pocketool_git.py maneja sólo la carpeta elegida. Los clones de autoría conservan
.git y materializan blobs regulares mediante ls-tree/cat-file, sin checkout ni
filtros; read-tree prepara el índice. Rechaza enlaces, gitlinks, nombres incompatibles
o duplicados en Windows, más de 10000 entradas, blobs mayores de 16 MiB y árboles
mayores de 64 MiB. No limita el tamaño del historial descargado por Git. Los clones
de consumo siguen usando el snapshot superficial existente. No ejecuta hooks,
fsmonitor ni filtros clean/smudge/process durante las operaciones de autoría.
Git mantiene su identidad y autenticación ya disponibles, sin diálogos de credenciales.

Antes del commit se revisan rama, HEAD, origin, estado y huellas de archivos.
Una revisión obsoleta se rechaza. Sólo los paths elegidos entran en git add y
commit --only; los demás cambios del índice se conservan. Un fallo puede dejar los
archivos seleccionados preparados, pero nunca descarta su contenido. Se bloquean
conflictos, HEAD separado y operaciones merge/rebase/cherry-pick/revert pendientes.
El push explícito usa la URL revisada y HEAD:refs/heads/<rama>, sin force, pull,
hooks, selección de ramas ni creación del remoto. Fallar el push conserva el commit;
Subir commits permite reintentar. La subida abarca los commits pendientes de la rama.
No se cambia origin si difiere de la URL solicitada. Los paquetes editables usan
la carpeta como pocketool.<nombre>; actualizar no sustituye su repositorio.
Comandos permite convertir un consumo por URL en copia local editable: clona a staging
y adjunta exclusivamente .git al árbol instalado, conservando todos sus archivos.
Si no hay copia instalada, publica el checkout validado completo. Registra después
la ruta portable. Un fallo al guardar configuración puede dejar .git adjunto; se puede
reintentar la conversión. La actualización de snapshots rechaza cualquier destino
que ya contenga .git, incluso si conserva la configuración remota anterior.

pocketool_execution.py deriva lanzadores CMD de los PS1 con nombre admitido en el
paquete cuando no existe CMD/BAT/EXE/COM homónimo. En los árboles locales los lanzadores
forman parte del repositorio y se ofrecen en la revisión Git. Se identifican por su
contenido completo: no se duplican en la lista de comandos y sólo se retiran huérfanos
sin modificaciones. Los lanzadores personalizados se conservan.
Los accesos de comandos reutilizan el compositor y diálogo de etiqueta/color,
añaden argumentos y conservan profile, paquete y archivo en core/state/pocketool-shortcuts.
Usan pocketools.ico sólo en el .lnk y apuntan al pythonw privado con pocketool-run,
que abre el Terminal y termina. Así Windows no asocia el icono del acceso de Pocketools
al ejecutable de la ventana compartida. Terminal conserva mito.ico en sus recursos y
perfiles. prepare migra los enlaces antiguos registrados cuyo destino y argumentos
siguen coincidiendo, conservando IconLocation y el resto de propiedades. Los enlaces
modificados manualmente o ausentes no se sustituyen; los fallos se registran sin bloquear
el gestor. El hijo pocketool-run --owned
resuelve el comando instalado al abrirse, construye execution_context del profile
y espera el árbol de procesos mediante ProcessJob. Los argumentos atraviesan Windows
Terminal como JSON/base64 para que sus separadores no se interpreten como acciones
de pestaña. CMD usa batch_command; PowerShell usa -NoProfile -ExecutionPolicy Bypass
-File sólo en ese proceso. Tras terminar, la pestaña espera Intro y muestra el código
de salida. No hay PATH global, dependencias automáticas ni ejecución durante Guardar.

`hooks.py` ejecuta pre_install y luego installer.script o installer.file (con override
de arquitectura) mediante
PowerShell -NoProfile -NonInteractive. Recibe contexto JSON separado del código:
$dir/$original_dir apuntan al payload temporal; $app, $bucket, $version, $manifest,
$architecture=64bit y $global=false describen la instalación. $fname contiene
el nombre local de la descarga (lista ordenada si hay varias URL), conservando
mayúsculas y el alias #/nombre. No incluye extract_to;
el script debe anteponer ese subdirectorio cuando corresponda. Un archivo ya
extraído no se conserva automáticamente para el hook. El entorno de home es
privado. $persist_dir usa la persistencia compartida o una carpeta temporal vacía
para bases aisladas; nunca toma datos de un profile para sembrar otras copias.
Se ejecuta sólo al instalar un payload nuevo, no al activarlo. Instalaciones antiguas
que omitieron el hook requieren reinstalación. Los errores abortan la publicación;
no se revierten efectos externos que el script haya causado.

installer admite dos formas excluyentes: script (texto/lista) o file (.ps1 relativo
dentro del payload) con args opcional (texto/lista de cadenas). EXE/MSI, args sin
file, la combinación file+script, keep y otros campos se rechazan en preflight.
El archivo se verifica existente y confinado después de pre_install, se ejecuta
con PowerShell sin perfil mediante el runner y se conserva en el payload.
args se tokeniza antes de sustituir $dir, $original_dir, $persist_dir, $version,
$architecture y $global (también con llaves); se pasa como array posicional,
sin evaluar expresiones. Otras variables permanecen literales. No se implementa
enlace de parámetros nombrados de PowerShell desde args ni inferencia de file.
null u objeto vacío omiten
esa fase; script vacío es una operación vacía. Cada fase usa un proceso PowerShell
nuevo con el mismo contexto y helpers; comparten archivos, no variables creadas por
el script anterior. Se aborta ante error de PowerShell, salida no cero o enlaces
no gestionados en el payload; pre_install fallido impide ejecutar installer.
La fase no se repite al activar ni al reutilizar una instalación idéntica.

Helpers iniciales: info/warn/error/abort, Expand-7zipArchive y Expand-InnoArchive
con Path, DestinationPath, ExtractDir y Removal. Ambos llaman a extractores privados
validados dentro del payload; el helper Inno admite sólo `{app}`. Get-HelperPath
expone únicamente innounp e Invoke-ExternalCommand ejecuta herramientas explícitas,
comprueba su salida y puede escribir su log. Parámetros y helpers no implementados
dan error.
No se importa Scoop ni se ejecuta post_install o hooks de desinstalación.

El runner expone también `$bucketsdir`, apuntando a copias privadas de `scripts/`
de los buckets referenciados por pre_install/installer.script. El plan reconoce
rutas literales `$bucketsdir/<bucket>/scripts/...` y la variante con `$bucket`
(incluidas llaves); referencias dinámicas no resolubles se marcan no soportadas.
No analiza el contenido futuro de installer.file ni descubre referencias transitivas
en scripts auxiliares. Orígenes se toman de configuración o del índice del catálogo;
las referencias entre buckets necesitan un origen registrado, sin depender de Scoop.
Antes de ejecutar hooks, se copian recursos locales o se obtiene un ZIP GitHub
validado en staging. Recursos remotos sin hash requieren la confirmación del plan,
también en dependencias. Cambiar los orígenes invalida el plan confirmado.
Ambas fases usan la misma copia; se descarta al acabar. Se obtiene el estado actual
del origen, no un commit fijado al manifiesto consultado previamente; no hay caché
offline de recursos ni sincronización al activar. El índice sigue conteniendo sólo
manifiestos. No se importa código Python de los catálogos.

Expand-DarkArchive interpreta bundles WiX 3/Burn con cabecera v2, un CAB UX y un
CAB adjunto. Lee rangos de PE/.wixburn, contempla la firma original del engine,
extrae con 7-Zip privado y reconstruye UX/ y AttachedContainer/ desde el manifiesto
UTF-8. Valida inventario, rutas, tamaños y hashes declarados antes de copiar.
Destinos repetidos con fuentes internas distintas se admiten sólo si todos los
archivos extraídos son idénticos byte a byte; se verifica cada tamaño/hash antes
de consolidarlos en una copia. Contenido distinto aborta indicando el destino.
Fuentes internas duplicadas y colisiones archivo/directorio siguen rechazadas.
Las entradas Payload externas del manifiesto se omiten con aviso, como en el lector
de WiX: no forman parte del inventario del CAB ni se leen o descargan. Una entrada
incrustada ausente sigue siendo error; si el hook necesita un archivo externo,
fallará al intentar usarlo. Packaging desconocido y UX externo se rechazan.
Otros formatos/versiones y CAB encadenados se rechazan; no se
ejecuta el bundle ni se añade WiX al bootstrap. Expand-MsiArchive reutiliza msi.py,
sin ejecutar acciones de instalación. Ambos helpers quedan confinados al payload,
admiten Removal y MSI admite ExtractDir. Parámetros ajenos al contrato se rechazan.
El formato se contrastó con
[BurnCommon.cs](https://github.com/wixtoolset/wix3/blob/develop/src/tools/wix/BurnCommon.cs)
y [BurnReader.cs](https://github.com/wixtoolset/wix3/blob/develop/src/tools/wix/BurnReader.cs).

HOME, USERPROFILE, APPDATA, LOCALAPPDATA, TEMP, TMP, HOMEDRIVE y HOMEPATH se
construyen desde una única raíz `home/`. Los profiles no pueden redefinirlas. `env` se aplica después de env_set y puede
sobrescribir variables de componentes; sus valores son literales, sin expansión ni
ejecución. PATH y MITO_* también están reservadas. Nombres sin distinción de
mayúsculas, valores de cadena y limpieza al cambiar de profile en procesos hijos.
El PATH de activación anterior no se acumula al abrir otra terminal desde MITO.
`home/custom-commands` y `core/commands` se añaden sólo a procesos hijos.
Engine.context_variables lee sin activar componentes las variables de rutas gestionadas:
MITO_PROFILE, MITO_ROOT, MITO_WORKSPACE y las de home/AppData/temporales. La construcción
del entorno reutiliza esos valores. MITO_WORKSPACE es workspaces/<workspace del profile>,
absoluto y recalculado al cambiar profile o mover la instancia; comparte la reserva MITO_*.
El editor Pocketools usa esta lectura para comentar las plantillas CMD/PS1 del profile
de referencia, sin copiar variables personalizadas ni activar componentes al abrirlo.

No hay tratamiento especial de VS Code en activación, lanzamiento ni persistencia.
Se aplica env_set/persist y los argumentos declarados por el manifiesto, igual que
para cualquier componente. Los datos antiguos por profile se conservan sin migración.

La UI ofrece apertura independiente o acoplada para cualquier aplicación lanzable,
incluidas referencias del host. El modo acoplado abre una pestaña dedicada en la
ventana nombrada de esta instancia; su título procede de la etiqueta `shortcuts`, del
ID genérico o del nombre del archivo externo. El Python de la pestaña crea el proceso
suspendido, lo asigna a un Job Object con kill-on-close y sólo entonces lo reanuda.
El handle no se hereda y pertenece al proceso de la pestaña. La pestaña espera a que
el job quede sin procesos, no sólo al ejecutable inicial. Al cerrarla, Windows cierra
el handle y termina el árbol sin incluir Windows Terminal ni otras pestañas.

Ambos modos aceptan argumentos introducidos por el usuario para ejecutables de
`shortcuts`, de `bin` y del host. Se tokenizan con las reglas de línea de comandos de
Windows y se añaden después de los argumentos fijos del manifiesto. BAT/CMD usa el
mismo transporte por variables temporales que los trabajos para evitar una segunda
expansión de `%`, `!` y metacaracteres. El runner acoplado muestra el código de salida
y espera Intro al terminar, también con código cero y ante fallo de arranque. Así
se conserva el diagnóstico de scripts que no propagan los errores de sus hijos.
Los argumentos BAT/CMD simples se transmiten sin comillas adicionales para que
las comparaciones literales de `%1` funcionen; valores vacíos, espacios y caracteres
especiales conservan comillas y el transporte por variables de una sola expansión.

Los accesos de aplicaciones guardan el modo y los argumentos elegidos y siguen
resolviendo profile y bucket/app o referencia del host al ejecutarse. Acoplado significa terminación del
árbol, no cierre elegante. Instancias previas, servicios y brokers externos no pasan
a pertenecer al job. Si no puede asignarse el proceso antes de reanudarlo, el
lanzamiento falla; no se mantiene una lista de excepciones por producto.

## Alcance de portabilidad

El core no registra programas ni modifica PATH global por sí mismo. Los scripts
pre_install e installer se ejecutan con los permisos del usuario y pueden modificar el host:
el entorno privado no es un sandbox. Una app declarativa puede también escribir fuera del
home mediante APIs del sistema; la clasificación `Compatible` significa que el
motor entiende su instalación, no una auditoría de todas sus escrituras.

Los ZIP rechazan traversal, rutas Windows reservadas, ADS, symlinks, entradas
duplicadas sin distinguir mayúsculas y tamaños excesivos. Se aceptan hashes
MD5, SHA-1, SHA-256 y SHA-512, además de algoritmos de digest fijo disponibles
en hashlib con prefijo explícito. Hash ausente, vacío, null o `skip` desactiva la
verificación para esa descarga. Un hash declarado inválido o distinto se rechaza. Las descargas y redirecciones
admiten HTTP y HTTPS, incluidas redirecciones de HTTPS a HTTP.

Si la verificación del certificado TLS falla (equipos con inspección TLS
corporativa o intermedios ausentes), transfer.download reintenta una vez sin
verificarlo, con o sin hash declarado, y el aviso queda en el log. Con hash, la
integridad la garantiza el hash declarado; sin hash la descarga queda sin
protección de integridad, aceptado porque esas descargas ya exigen confirmación
explícita y HTTP está permitido: MITO prioriza funcionar en todos los escenarios.

La URL determina un nombre local estable: alias #/nombre o último segmento de
la ruta, decodificado y validado como nombre Windows. Se ignoran query, nombres
de redirecciones y Content-Disposition; una ruta vacía requiere alias. Se admiten
nombres sin extensión: ZIP/TAR se detectan por contenido y 7z/RAR por firma;
los demás se copian como archivos sueltos para su uso o renombrado en pre_install.
No se infiere que un archivo suelto sea un ejecutable ni se ejecuta al copiarlo.
Los nombres .jar y .ico se copian íntegros antes de detectar contenedores: un JAR
no se descomprime por tener estructura ZIP. Un alias explícito #/archivo.zip permite
solicitar extracción. Los JAR declarados en bin o shortcuts se lanzan con java.exe
-jar, resolviendo Java por el PATH del profile y conservando argumentos, entorno y
workspace. Falta de Java produce un error; no se instala ni se elige una versión
automáticamente. Un wrapper .cmd declarado en shortcuts conserva su prioridad y
su propio comando (por ejemplo javaw); no se reescribe.
MSI sigue requiriendo extensión .msi; otras extensiones no soportadas se bloquean.
El extractor informa si extrajo un contenedor o copió un archivo suelto. extract_dir
sólo selecciona una carpeta en el primer caso; una carpeta ausente en un contenedor
sigue siendo error. extract_to mantiene su destino para ambos casos. Las listas
de extract_dir/extract_to conservan su correspondencia posicional con las URL.
El extractor acepta ZIP/nupkg, 7z, RAR, TAR y sus compresiones gzip/xz/bzip2. Los archivos
sueltos se copian usando el nombre del fragmento Scoop `#/nombre`; el extractor
no ejecuta los .exe sueltos. MSI se lee mediante las APIs de base de datos de
Windows Installer en modo read-only: nunca se abre un paquete ni se invoca msiexec
o una acción MSI. Se reconstruye la imagen de archivos con Directory/Component/File
y Media, nombres largos y directorios de origen (target:source), sin prefijo SourceDir,
para aplicar después extract_dir/extract_to como en Scoop. Los CAB incrustados se
leen de _Streams y se descomprimen con el 7-Zip privado, con límites y validación de
rutas, duplicados, inventario y tamaños. No requiere nuevas herramientas de bootstrap.
Sólo se admiten archivos contenidos en CAB internos independientes; medios externos,
archivos sueltos y CAB divididos se rechazan sin publicar. No se ejecutan condiciones,
acciones personalizadas, servicios, registro ni selección de features del MSI: se
extraen todos sus archivos. La presencia de bin valida instalación, no funcionamiento
sin dependencias del host. No se declara compatibilidad total con código PowerShell.

Con innosetup: true, las descargas cuyo nombre efectivo termina en .exe se extraen
con innounp 2.71.1 privado, fijado por URL inmutable y SHA-256 en bootstrap.lock.json.
Nunca se ejecuta el instalador. Se valida su listado UTF-8 antes de extraer: rutas
portables, duplicados y solapamientos sin distinguir mayúsculas, hasta 150000 archivos
y el límite MAX_EXPANDED. Sólo se extrae {app}, eliminando ese prefijo. Se comprueban
inventario, tamaños y ausencia de enlaces antes de copiar al payload. Después se
aplican extract_dir/extract_to, hooks y validación/publicación habituales.
Los archivos auxiliares de otras extensiones siguen el extractor ordinario.
Se rechazan formatos desconocidos por innounp y errores sin publicar; no se solicitan
contraseñas ni medios externos. No se reproducen registro, servicios, condiciones,
tareas ni código del instalador, y los destinos fuera de {app} se omiten. Esta
extracción no garantiza que una aplicación funcione sin esos pasos o dependencias.
Las instalaciones existentes incorporan la herramienta mediante mito.cmd bootstrap.

## Interfaz y Windows Terminal

`mito.language` es una propiedad global finita: `system` (predeterminado) o uno
de los idiomas oficiales de la Unión Europea. Sin propiedad o con `system`,
GetUserDefaultUILanguage detecta el idioma de interfaz del usuario de Windows,
incluidas variantes regionales. Cada idioma oficial usa su bundle; cualquier
idioma desconocido o fallo de detección utiliza inglés. La selección manual tiene
prioridad sobre Windows.
El gestor carga su bundle al abrirse; guardar otro idioma requiere
volver a abrirlo y muestra un aviso. No altera entornos ni profiles. Los JSON UTF-8
viven en core/app/mito/locales y se incluyen explícitamente en la distribución.
Las claves son plantillas españolas y los argumentos se interpolan después de
traducir: IDs, rutas y valores del usuario nunca son claves de traducción.
Un valor de configuración desconocido vuelve a inglés; una traducción ausente o con
parámetros incompatibles busca el bundle inglés y, si también falta, conserva el
texto fuente. El editor rechaza valores no admitidos.
El contexto del gestor cubre sus workers, sin un idioma global compartido entre
instancias. La CLI y bootstrap mantienen su salida en español; los diálogos nativos
de autenticación leen el idioma de la instancia al abrirse.
Descripciones, notes, info de pocketools, scripts y salidas de terceros se muestran
literalmente. La clasificación interna `Compatible` no cambia con el idioma.

Configuración en ACCIONES edita config.properties; Configuración dentro de Gestionar
profile edita sólo env del JSON seleccionado. configuration.py declara propiedades
reconocidas, valores finitos y validaciones; configuration_ui.py comparte el editor.
Las claves administradas son fijas y los booleanos usan Select; las variables libres
ocultan env. en la UI global y recuperan el prefijo al guardar. bucket.<nombre> y
proxy_authentication_form_field.<nombre> permiten propiedades administradas dinámicas.
Los comentarios y propiedades desconocidas existentes se conservan. Guardar valida
y escribe atómicamente bajo lock; detecta cambios externos desde la apertura para
evitar sobrescribirlos. Cerrar descarta los cambios. No se sincronizan entornos vivos.

Inicio enseña el inventario completo con checkboxes, uno por instalación. La
pantalla de catálogo busca e instala sin activar. Cambiar de profile actualiza
las casillas. No hay exclusión entre Java 11, 21, 25 u otras familias.

Windows Terminal 1.24.11911.0 y 7-Zip 26.02 pertenecen al bootstrap con hashes.
El terminal usa `.portable` y guarda ajustes bajo su carpeta privada. Los profiles
se abren desde las pestañas, sin accesos de terminal/profile. Los .lnk de apps llaman
al Python privado, guardan rutas absolutas y se regeneran al trasladar la carpeta.

Al instalar las herramientas del bootstrap y al preparar el Terminal privado, MITO
sincroniza su ICO con los recursos externos `Images/terminal_contrast-black.ico` y
`terminal_contrast-white.ico`. También deriva `YitoTerminal.exe` desde el binario
verificado y sustituye sus recursos `RT_ICON`/`RT_GROUP_ICON`; éste es el ejecutable
que abre la ventana. El `WindowsTerminal.exe` original conserva bytes, hash y firma;
la copia personalizada queda sin firma al cambiar sus recursos y se reconstruye si
cambia el original o `mito.ico`. No hay procesos auxiliares. El acceso del gestor
apunta directamente al Terminal privado; `mito.cmd` permanece como entrada de
consola, ya que un script CMD no puede contener un recurso de icono.

Al montar el gestor o iniciar un hijo de terminal, terminal_identity.py asigna
AppUserModel.ID por ruta de instancia y las propiedades RelaunchIconResource,
RelaunchCommand y RelaunchDisplayNameResource a su ventana mediante IPropertyStore.
El icono es siempre mito.ico; relanzar desde la barra abre el gestor mediante el
pythonw privado. La selección de ventanas comprueba clase de Terminal y ruta completa
del proceso para no afectar al Terminal del host ni otras instalaciones. No hay
watcher, limpieza global de caché ni reinicio de Explorer. La aplicación se registra
en logs como terminal.identity con número de ventanas; un fallo no impide el inicio.

Actualización de identidad y pestañas: el comando usa `--maximized` y un nombre
de ventana derivado de la raíz, para reutilizar la ventana de esta instancia.
`prepare` genera GUIDs estables para MITO, PowerShell(profile) y CMD(profile),
paletas verde/azul/ámbar y un menú explícito que oculta las entradas del host.
La ventana se abre maximizada, con pestañas visibles; opacidad 94 y acrílico.
Los ajustes previos se conservan una vez en core/state/terminal-settings-before-branding.json.
Los perfiles de Terminal llaman al Python privado y `shell --type powershell|cmd`.
Se reconstruye el entorno al abrir cada pestaña, sin actualizar procesos existentes.
El refresco de menús aprovecha la recarga nativa del JSON de Windows Terminal;
MITO no añade watchers, servicios ni un protocolo propio entre procesos.

`terminal.defaultShell=cmd|powershell|mito` elige la entrada predeterminada para
«+» y la primera del menú (`Ctrl+Shift+1`); el valor ausente o inválido usa cmd.
El profile es el seleccionado en el gestor; se actualiza al abrirlo, cambiar su
selector o guardar Configuración global. Se conserva en core/state/terminal-profile.json
para que las regeneraciones del motor no lo sustituyan. Si desaparece, se usa el
primero disponible; sin profiles se usa MITO. El ajuste es único por instancia:
entre varios gestores prevalece la última selección. Cambiar el foco entre pestañas
no cambia ese estado. El arranque explícito del gestor sigue abriendo MITO.

El logo, el JPG original y el ICO multirresolución viven en core/assets/branding.
Pillow compone cada resolución del ICO de la aplicación con una etiqueta inferior
derecha, usando el compositor de EAP adaptado a 1–4 letras/números (defecto MITO).
Los recursos EXE/DLL se leen como datos, sin ejecutar el archivo. Si no hay icono
extraíble se usa el ICO de MITO. Los derivados quedan en core/state/icons. La base mito.ico se genera desde
mito_icon.png con transparencia real. El diálogo de creación solicita la etiqueta
(máximo cuatro, MITO por defecto); no existe un campo permanente en el menú.

## Accesos explícitos de aplicación

### Ejecutables externos del host

Inicio divide su fila inferior entre búsqueda del catálogo y Agregar aplicación del
host. El selector nativo admite .exe/.bat/.cmd/.ps1. host_apps.py registra referencias
absolutas por huella de ruta en core/state/host-applications.json, sin copiar binarios
ni modificar los profiles. Se muestran en Inicio, compartidas por la instancia.

Abrir reconstruye engine.environment(profile): APPDATA, HOME, USERPROFILE, PATH,
variables y workspace son los de MITO, igual que para componentes instalados. No se
restaura un entorno del host. CMD y PowerShell usan procesos hijos; el paso de rutas
BAT/CMD evita reinterpretar %, ! o &. No se registra persistencia especial ni se
modifican asociaciones, PATH global o archivos de la aplicación externa.

Los accesos usan el compositor Pillow y host-run con ID y profile fijados, cargando
el entorno actual de ese profile al abrirlos y el interceptor de red existente.
Info muestra archivo/carpeta y permite abrirla. Eliminar requiere confirmación y
retira la referencia de MITO y sus junctions de datos/caché; conserva binario y datos del host. Archivos ausentes siguen
visibles para quitar la referencia, con Abrir/Acceso directo deshabilitados.
Estas rutas locales no se exportan con profiles ni se incluyen en releases.

host_data.py gestiona junctions optativas desde home/AppData/Roaming (Host data)
y home/AppData/Local (Host cache) hacia carpetas del host. Known Folders localiza
el AppData real aunque el proceso herede el entorno MITO. La búsqueda recursiva
usa el nombre del ejecutable sin extensión, sin distinguir mayúsculas, conserva
las coincidencias más externas y no recorre junctions ni propone el árbol que
contiene el ejecutable. Primero ofrece Roaming y después Local, con confirmación
individual por candidato. No hay reglas por producto.

Los botones Cache/Data · link/unlink permanecen en todas las apps externas. Sin
enlaces buscan candidatos de nuevo; si no hay candidatos o se rechazan, permiten
selección manual desde AppData Local/Roaming y revisión con origen/destino y botón Enlazar; con enlaces permiten
desenlazar la categoría. Las rutas dentro del AppData correspondiente conservan
su ruta relativa; las externas usan su nombre de carpeta bajo el AppData de MITO.
El registro separado core/state/host-data-links.json contiene ID, categoría,
ruta relativa y destino absoluto. Los enlaces son compartidos por todos los
profiles, como el home; el estado visible comprueba la junction y su destino.
También se reconocen sin escribir el registro las junctions manuales del AppData
portable cuyo nombre/ruta coincide con el ejecutable y cuyo destino es la misma
ruta relativa en el AppData real del host. No se recorren sus destinos. Los botones
permiten retirarlas con confirmación y Eliminar las incluye. Enlazar una junction
existente al destino solicitado la registra sin borrarla ni recrearla; repetir la
operación es idempotente. Enlaces a otros destinos siguen protegidos.

Una carpeta local existente exige una segunda confirmación de borrado. Se mueve
temporalmente a staging y se restaura si falla la creación o el registro; después
del éxito se elimina. Se rechazan rutas solapadas con otros enlaces gestionados,
enlaces ajenos y destinos internos/circulares. Eliminar una app retira sus junctions
mediante rmdir, nunca sus destinos del host. Desenlazar no copia datos a MITO.
Las junctions modificadas externamente se rechazan sin borrar la carpeta sustituta.
No se migran asociaciones ni se controla una instancia de la app ya abierta:
el usuario debe cerrar la aplicación antes de cambiar los enlaces. Local/Roaming
son convenciones; una app puede ignorar estas variables o guardar datos fuera.

Crear profiles, activar o instalar refresca los ajustes de Terminal sin generar enlaces.
application_link se llama únicamente por petición explícita (botón o CLI). Usa
Known Folder Desktop de Windows, registra la ruta creada y evita sobrescribir un
archivo ajeno con el mismo nombre. Fija profile y bucket/app en el comando del
Python privado, sin versión ni huella. El registro estable usa profile + bucket/app
y guarda app en lugar de installation. Nombre de los nuevos enlaces: profile - bucket-app.
El resolver existente exige un componente activo coincidente; si está
inactivo o un nombre corto coincide con varios buckets, no lo abre. No copia datos ni payloads al escritorio.

application_target es el resolver compartido entre Abrir y la visibilidad de los
botones. Usa shortcuts y bin del manifiesto efectivo y comprueba el archivo. Los
componentes sin esos puntos de entrada no muestran acciones, aunque contengan
otros ejecutables internos.
application_entries enumera destinos disponibles, sin exigir persistencia. Si hay
varios, Abrir y Acceso directo presentan un selector. La etiqueta elegida viaja como
--shortcut por CLI, pestañas y enlaces; se resuelve otra vez contra el manifiesto
actual, sin fijar versión ni ruta. Etiquetas ambiguas o ausentes producen error.
Los enlaces de destinos distintos tienen registros y nombres separados, conservan
argumentos e icono del destino. CHM usa hh.exe del directorio Windows con entorno y
workspace del profile; otras extensiones documentales no se abren por asociación.
La apertura predeterminada prioriza ejecutables frente a CHM.

Tras preparar las descargas Inno, un destino declarado en bin/shortcuts que falte
puede recuperarse desde variantes nombre,1.ext, nombre,2.ext, etc., contiguas desde 1:
deben existir al menos dos y tener el mismo SHA-256. Se copia el contenido idéntico
al nombre declarado, conservando los originales. Si difieren se rechaza la instalación;
no se interpretan condiciones del setup ni se elige una variante arbitraria.

## Fuentes consultadas

- EAP: README, guía de contribución, mapa de especificaciones, runtime y lanzadores.
- Scoop local: `Scoop-master/lib/install.ps1` y manifiestos Main, Extras, Versions.
- [Contrato de manifiestos Scoop](https://github.com/ScoopInstaller/Scoop/wiki/App-Manifests).
- [Textual DataTable](https://textual.textualize.io/widgets/data_table/) y
  [workers](https://textual.textualize.io/guide/workers/).
- [CLI VS Code: entornos separados](https://code.visualstudio.com/docs/configure/command-line).

## Gestión de profiles, entrada del gestor y releases

### Actualización de la propia distribución

`update_checks.daily` persiste fecha local, huella de configuración y resultado en
core/state/mito-update-check.json y component-updates-check.json. Bajo un lock corto
registra el intento antes de conectar; errores, 404 e interrupciones no se repiten
durante ese día. Los fallos se comunican una sola vez y los éxitos mantienen el botón
disponible desde caché. No mantiene el lock durante la red. Un token impide que una
respuesta antigua sobrescriba una consulta de otra configuración. Cambiar URL o
versión de MITO invalida su caché; cambiar inventario o fuentes de componentes
invalida la suya. La primera apertura del día siguiente vuelve a consultar, sin
servicios ni timers. La actualización manual de catálogos conserva su acción explícita.

Al abrir el gestor o pulsar Inicio · Instalados, un worker busca primero en update/
el ZIP local con mayor versión posterior, con nombre canónico de release Windows x64.
Comprueba tamaño y release.json al detectarlo; verifica íntegramente el ZIP y su
código al prepararlo. Un candidato local tiene prioridad y evita consultar GitHub.
Su `.zip.sha256` adyacente, si existe, es obligatorio y debe corresponder al mismo
nombre; si falta exige la confirmación habitual sin hash. La copia externa verifica
hash y tamaño mientras lee, conservando el ZIP original. Rechaza rutas redirigidas.
El modal muestra Offline y la ruta local. update/ está excluida de Git y las releases.
No hay watcher: pulsar Inicio vuelve a comprobar archivos añadidos o retirados.

Si no encuentra una release local posterior, consulta la URL de `mito.update_url`,
editable en Configuración. Por defecto es la API pública
`https://api.github.com/repos/danielgube/mito/releases/latest`. Debe devolver un
objeto JSON de release, no HTML: tag_name, assets con name/size/browser_download_url,
draft/prerelease opcionales y digest opcional. Es compatible con el contrato de
GitHub/Gitea y con servidores que publiquen ese formato, sin adaptadores por producto.
La configuración admite HTTP/HTTPS sin credenciales en la URL y mantiene el proxy
de la instancia, timeout y respuesta limitada. No implementa autenticación propia
de repositorios privados. La autoridad de confianza es el endpoint configurado:
sus adjuntos pueden usar otras rutas u orígenes HTTP/HTTPS, incluidos CDN.
Sólo una versión estable mayor.menor.parche posterior con su ZIP
`mito-<versión>-windows-x64.zip` muestra Actualizar MITO. Errores, 404 y límites de
GitHub quedan en el log sin bloquear Inicio. Un error retira el candidato anterior.
No hay polling ni instalación automática.

El modal exige guardar el trabajo y cerrar aplicaciones, pestañas y Terminal;
también las aplicaciones externas abiertas desde MITO. Se verifica el digest del
asset o, si falta, el `.zip.sha256` de la misma release. Sólo cuando no se declara
ninguno se solicita consentimiento explícito sin hash antes de descargar. Las URLs
de assets declaradas se validan como HTTP/HTTPS sin credenciales; el transporte y los hashes reutilizan
transfer.py. ZIP máximo 64 MiB, expansión 256 MiB, sin enlaces, traversal ni archivos
ajenos al formato de distribución. Se cotejan release.json y __version__ sin ejecutar
el archivo de versión. El bootstrap exige SHA-256 de Python, wheels y herramientas.

La preparación usa un directorio único en Temp del AppData Local real del host,
fuera de la instancia. Copia allí el Python mínimo y el código actual del actualizador.
Si cambia bootstrap.lock.json, prepara el bootstrap completo de la nueva release
en esa carpeta con hashes fijados y el proxy configurado. Un fallo deja la instancia
intacta y conserva la preparación/log para diagnóstico. No usa un Python global.

Offline compara semánticamente el lock del bootstrap con el instalado: si cambia,
se detiene antes de ejecutar bootstrap o conectarse a la red. Las releases mínimas
no contienen runtimes ni dependencias y no se inventa un modo offline capaz de
descargarlos. Con el mismo lock reutiliza las herramientas actuales. No se admiten
paquetes de bootstrap offline adicionales en esta primera implementación.

El CMD del sistema arranca con consola/grupo nuevos y breakaway del Job Object,
sin handles heredados, con cwd, runtime y home temporales externos. El gestor espera
confirmación de arranque antes de salir y detiene MCP por su flujo habitual. Si el
host impide el lanzamiento independiente, informa del error y mantiene el gestor.
El CMD espera Intro del usuario y enumera ejecutables bajo la raíz de MITO. No mata
procesos. Las apps del host, servicios y procesos inaccesibles requieren el cierre
manual indicado: no se promete detectar cualquier proceso que use datos del home.

Publicación bajo el lock común, con versión local revalidada y procesos comprobados
antes y después de copiar a staging del mismo volumen. Sólo sustituye core/app/mito,
los archivos de arranque, lock, ICO y release.json; si cambia el bootstrap, también
core/tools y su marcador. No toca configuración, profiles, home, workspaces, apps,
profile-apps, pocketools ni otros registros de core/state. Rechaza destinos redirigidos
por enlaces y checkouts .git. La copia anterior queda en core/staging/self-update-*/backup.
Fallos de sustitución intentan restaurar todos los destinos; una restauración incompleta
indica la ruta recuperable. Interrupciones abruptas no tienen recuperación automática.
Los temporales y backups se conservan, sin limpieza automática ni reinicio automático;
el CMD muestra el resultado e indica volver a abrir MITO.

`delete_profile` protege el último profile bajo el lock de mutación, conserva los
metadatos del inventario y archiva la configuración antes de retirarla. No elimina
home, payloads ni workspaces. Se actualiza el menú del Terminal.

`shortcut --profile N` crea un enlace explícito al gestor: Python privado ejecuta
`start --profile N`, que abre una pestaña MITO con `ui --profile N`. El profile
inicial se transmite por argumentos, sin modificar el estado de otras ventanas.
La interfaz comparte el diálogo de etiqueta con los accesos de aplicaciones.

`release --local` detecta la versión declarada en core/app/mito/__init__.py mediante AST,
sin ejecutar ese archivo ni consultar la red. Empaqueta sólo código Python, el ICO
y los archivos de arranque,
con configuración pública y profile default nuevos. No lee datos del home ni
incluye herramientas instaladas. Verifica el ZIP y escribe un SHA-256; no sobrescribe
una release existente. Bootstrap descarga las dependencias fijadas al primer arranque.


`publish.py` implementa `release [mayor|minor|hotfix]`, tomando la mayor versión
numérica local/remota como base M.m.p. Sin argumento o con hotfix genera M.m.(p+1);
minor genera M.(m+1).1 y mayor genera (M+1).0.1. El parche reinicia en 1 en ambos
saltos, por contrato de MITO. No se introduce una versión literal como argumento.
Requiere main limpio, origin del repositorio MITO y permisos
push. Usa la credencial de Git en memoria; no la guarda ni la imprime. Ejecuta las
pruebas antes del commit de versión. Publica mediante la API REST de GitHub: borrador,
assets verificados por SHA-256 y publicación final. Estado en pending-release.json
permite reanudar el mismo commit y ZIP después de errores de red; bloqueo local
impide dos publicadores simultáneos. Nunca fuerza pushes ni reemplaza assets distintos.
Al reanudar se conserva la versión pendiente independientemente del tipo solicitado;
no se vuelve a incrementar. --local rechaza tipos explícitos porque sólo empaqueta
la versión actual; --output sigue disponible tanto al publicar como en modo local.

Las notas incluyen los títulos y enlaces de los commits desde el último commit
`release: vX.Y.Z` de la línea principal, excluido, hasta el HEAD previo al incremento.
Incluyen commits integrados por merge; sin marcador previo se usa todo el historial.
No se infiere contenido de los diffs ni se requiere disponer de tags locales.
La misma sección se añade a CHANGELOG.md con versión y fecha local de preparación,
en el commit de versión, conservando las secciones manuales y «Sin publicar».
Las notas de GitHub conservan también las instrucciones de arranque y se guardan en
pending-release.json para reanudar sin recalcularlas. Pendientes antiguos sin notas
mantienen el texto anterior. No se reconstruyen releases históricas automáticamente.
`release --local` no modifica el changelog y el ZIP mínimo sigue sin documentación.


`search.py` comparte ranking entre UI y CLI: tokeniza palabras/números y normaliza
acentos/separadores. Ordena por cobertura, coincidencias en identificador, bucket,
nombre exacto y orden alfabético. La versión y notas aportan coincidencias secundarias.
Se muestran coincidencias parciales al final, sin inventar metadata LTS. Los filtros
por bucket de la UI persisten en core/state/catalog-filters.json y no afectan a la
resolución de dependencias ni a las instalaciones. El panel de detalles muestra notes
como texto literal y permite desplazarse; las filas instaladas, sus casillas y sus
botones ocupan una línea, tienen una línea de separación y alternan dos tonos para
facilitar su lectura.


components.py gestiona instalaciones por ID, sin excepciones por producto. Eliminar
mueve el payload a staging antes de retirar referencias de profiles; revierte si falla
la escritura. rmtree de Python en Windows no recorre destinos de junctions, preservando
persist. Si quedan archivos bloqueados se informa de la ruta de staging. Se regeneran
shims y menú del Terminal. La confirmación pertenece a la UI.

Actualizar compara versiones numéricas y sufijos prerelease genéricos contra el catálogo
local; sólo instala manifiestos compatibles. Instala el plan antes de modificar profiles,
prepara las copias nuevas de profiles activos o con copias anteriores y conserva sus
destinos persistentes. Rechaza cambios en persist o en el aislamiento sin migración
explícita. Conserva el ID bucket/app en cada profile afectado, añade sus dependencias
y mantiene la inactividad de los demás. No existen versiones paralelas de un mismo componente.
Convierte los accesos antiguos registrados con ID de instalación a bucket/app para
profiles vigentes, conservando ruta y etiqueta. Los accesos estables ya no requieren
migración entre versiones. Regenera shims. Comprueba acceso de eliminación de archivos en Windows
sin recorrer junctions. Retira a staging la base antigua y todas sus copias; revierte
referencias, accesos y movimientos si falla antes de la limpieza final. Elimina staging
sin recorrer destinos persistentes. Un fallo en la limpieza se informa como pendiente,
con la nueva versión instalada. Si falla la sustitución, se restaura la instalación
anterior. No hay rollback de efectos externos de pre_install ni recuperación automática de una interrupción abrupta del proceso.


## Aislamiento declarativo por profile

isolation.py decide con el manifiesto efectivo: bool(persist) and bool(shortcuts).
La instalación base sigue identificando el inventario y los locks, sin duplicar filas.
Al construir el entorno se valida la base y prepara una copia real (no hardlinks)
en core/profile-apps/<profile>/<bucket>/<app>. La copia se publica
tras completarse en staging; no se siguen junctions ni se copian los enlaces persist
compartidos. .mito-defaults aporta los datos iniciales. No se migra el estado compartido.
Persist se dirige a home/profiles/<profile>/persist/<bucket>/<app>; la API de persist
recibe el destino, sin conocer aplicaciones. El entorno y el lanzamiento usan esa copia.
Las copias sobreviven a desactivación y eliminación de profile; eliminar la instalación
retira todas las copias incluso inactivas, sin recorrer las junctions de datos.
Actualizar reemplaza todas las copias existentes, incluso inactivas, conservando
sus rutas y la persistencia independiente de cada profile.
La regla no añade adaptadores de aplicaciones ni ejecuta hooks por profile.


persistence.locations resuelve cada entrada y alias de persist mediante persist_root,
sin preparar payload ni crear rutas. ComponentInfoDialog muestra descripción, notas,
binarios base, copia del profile cuando corresponde y todas las entradas persistentes.
Cada ruta existente tiene un botón Abrir; las pendientes quedan deshabilitadas.
Archivos abren su carpeta contenedora con Explorer. Funciona en componentes inactivos
y respeta el profile seleccionado.

## Variables globales

Engine.global_env lee env.NOMBRE=valor de config.properties en cada entorno nuevo.
Comparte validate_env con los profiles, sin caché ni expansión. Prioridad:
env_set de componentes, globales, profile. Se normalizan nombres a mayúsculas y
se registran en MITO_ACTIVE_VARS para retirarlos al reconstruir otro entorno.
Las claves duplicadas en un mismo origen se rechazan sin distinguir mayúsculas.

## Proxy y portal de autenticación

network.py lee http_proxy/https_proxy/no_proxy; destinos vacíos suprimen herencia.
El motor aplica esas variables a hijos y transfer.download recibe la raíz explícita
para elegir su opener, también en catálogos y bootstrap Python. No cambia os.environ.
Network.check espera el estado configurado sin seguir redirects y con timeout.
UI lanza workers puntuales al inicio y al pulsar el estado, sin servicio ni polling.
ProxyDialog solicita credenciales, limpia la contraseña y ejecuta POST fuera del hilo UI.
CLI run sin --bin intercepta los accesos existentes pythonw mediante un diálogo nativo
WinForms; sólo datos por pipes en memoria, ningún secreto en comandos ni archivos.
Cancelar permite lanzar. Sesión CookieJar efímera; formularios sólo mismo origen,
POST HTTPS por defecto y redirecciones de POST nunca reenviadas. Comprobación final
independiente del CookieJar. No implementa autenticación HTTP 407 ni cookies en hijos.

Los ZIP se validan y extraen con Python. Si la lectura encuentra un método de
compresión no implementado, archives.extract_7zip reutiliza el extractor privado,
con listado previo, límites y rechazo de enlaces. No se relajan hashes ni rutas.

## Nombres de comandos

command_name valida aliases bin como nombres de archivo Windows individuales,
separados de identifier (IDs internos). Admite espacios y signos como +. Los shims
usan comillas, escapan porcentajes literales y desactivan expansión retardada para
conservar !. Rutas, ADS, caracteres prohibidos y nombres reservados siguen rechazados.

## Confirmación de descargas sin hash

Engine.install recibe confirm_unverified. Tras resolver y validar todas las
instalaciones, reúne descargas sin hash de payloads ausentes, incluidas dependencias,
y pide una única confirmación antes de descargar. Sin callback o con rechazo cancela
sin cambiar profiles ni publicar payloads. UI espera un ModalScreen desde el worker;
CLI pide confirmación por stdin. components.update transmite el mismo callback.

## Intercambio de profiles sin datos

profile_transfer exporta exclusivamente {apps: [bucket/app,...]} desde las selecciones
activas; deduplica nombres y no copia el JSON del profile ni los records instalados.
El lector rechaza campos adicionales e identificadores que no sean bucket/app.
La importación usa los catálogos del destinatario y Engine.install con una lista de
peticiones: un plan completo de dependencias, revisión antes de descargar y la
confirmación habitual de hashes ausentes. Sólo tras éxito escribe un profile nuevo
con workspace propio, apps resueltas y env vacío; no sobrescribe profiles existentes.
No importa fuentes, versiones históricas ni manifiestos externos. La revisión distingue
componentes ya instalados de sustituciones que afectan a los demás profiles. Si la
instalación termina y falla después la creación del nuevo profile, los componentes
quedan instalados; un fallo durante la publicación del plan revierte sus binarios.


## Conversión del almacenamiento anterior

`mito.cmd migrate-storage` convierte explícitamente las carpetas versión-huella a
rutas estables y las selecciones antiguas a bucket/app, sin descargar ni actualizar.
Requiere aplicaciones cerradas. Conserva datos, copias por profile y accesos registrados.
Valida todo antes de mover; si existen varias instalaciones de un mismo componente
o contenido adicional ambiguo, se detiene sin elegir ni borrar. Revierte movimientos,
metadatos, profiles y accesos ante errores normales. No ofrece recuperación automática
tras una terminación abrupta: staging conserva los árboles para diagnóstico.
Las rutas absolutas guardadas por IDEs externos deben corregirse una vez a la ruta
estable. La migración no edita configuraciones de terceros.


## MCP local optativo

`mcp_service.py` pertenece al gestor Textual. Arranca disabled en cada sesión;
leer su configuración no importa el SDK ni crea listeners, workers o procesos.
El botón MCP abre `mcp_ui.py`, con control e instrucciones copiables. Su encabezado
y cierre están fuera del contenido desplazable. Sólo identidad y puerto se guardan
en core/state/mcp/settings.json. La configuración global `mcp.jobs.monitor` admite
true/false, con false por defecto. En true muestra el botón independiente MCP · Jobs
y su propio diálogo; guardar la opción en Configuración actualiza su visibilidad.
Esta opción sólo controla la presentación: el registro, consultas MCP, cancelación
y confirmaciones humanas siguen funcionando siempre. El monitor consulta salida
únicamente por acción explícita; las actualizaciones de estado no descargan logs.

Activar usa `terminal.launch_mcp` para abrir el Python privado directamente en una
pestaña MITO MCP de la ventana nombrada de la instancia. `mcp_server.py` sirve
Streamable HTTP en 127.0.0.1, sin autenticación. Usa MCP Python SDK 1.30.0 (línea v1),
Starlette y Uvicorn con dependencias transitivas y SHA-256 fijados en bootstrap.lock.json.
Rechaza Host ajeno y cualquier Origin de navegador; no ofrece CORS ni escucha en LAN.
Cualquier proceso local que alcance el puerto puede ejecutar las herramientas.

El gestor mantiene mcp.lock durante toda la vida y limpieza del servidor. Un pipe
Windows aleatorio, limitado por ACL al propietario/SYSTEM, transmite JSON, nunca
pickle. Su PID cliente se obtiene del sistema antes de asignarlo a un Job Object
con kill-on-close; el handshake precede a la apertura HTTP. No es autenticación MCP.
El servidor observa el handle del gestor y su pipe. Cerrar la pestaña/ventana mata
el proceso de consola y cerrar o matar el gestor cierra su Job Object. No hay
supervisor permanente ni reinicio automático. Desactivar cancela comandos y esperas;
una publicación ya iniciada termina antes de la salida normal. Un cierre forzado
puede interrumpir escrituras y no garantiza rollback; los jobs no se reproducen.

`mcp_tools.py` expone 25 herramientas sobre Engine, Configuration, componentes y
pocketools. Las mutaciones devuelven trabajos; los conflictos requieren relectura
y reintento. `profile_configure` y `configuration_set` exigen revisión. Las lecturas
principales y las publicaciones usan el lock común con UI/CLI. Los eventos refrescan
Inicio conservando el profile seleccionado; no cambian consolas existentes.

`Engine.installation_plan` obtiene dependencias, descargas sin hash e instantánea de
instalaciones/profiles. Las confirmaciones ocurren fuera del lock de publicación;
al recuperarlo se recalcula y compara el plan antes de descargar/publicar. Sólo
el diálogo Textual permite aprobar descargas sin hash, incluidas dependencias.
No hay herramienta MCP de aprobación. Instalar y activar siguen separados.

`jobs.py` registra antes de ejecutar y deduplica request_id dentro de la sesión.
`command_start` obtiene entorno y workspace juntos bajo lock, ejecuta argv o un
script CMD/PowerShell y gestiona stdin, stdout/stderr, timeout y cancelación.
`processes.py` crea el comando suspendido, lo asigna a un Job Object y lo reanuda:
los descendientes terminan con él, incluso si el servidor muere. Los comandos tienen
los permisos del usuario Windows; la carpeta inicial confinada no es un sandbox.

Límites: cuatro workers, 32 trabajos pendientes y 128 totales por activación;
10 MiB de salida combinada por trabajo y 64 MiB por sesión. Se drenan ambos pipes
incluso al truncar. Salida UTF-8 con cursor de bytes y lecturas de 4..65536 bytes.
Se conservan como diagnóstico hasta dos sesiones anteriores de menos de siete días,
además de la actual; la limpieza sucede al activar. Trabajos antiguos incompletos
pasan a interrupted, sin reanudación; las herramientas sólo consultan la sesión actual.
HTTP admite cuerpos de hasta 1 MiB, 32 sesiones de cliente y 30 minutos de inactividad.
