# Manual de MITO

MITO (**My Integrated Terminal Orchestrator**) crea entornos de desarrollo
portátiles para Windows x64. Cada instancia utiliza una sola carpeta y un solo home;
los profiles deciden qué componentes, variables y workspace recibe cada proceso.

## Logs locales y envío de errores a PostHog

MITO guarda el diagnóstico desde el bootstrap de PowerShell, incluso antes de
disponer de Python, en **`logs\mito.log`**. Conserva tres copias rotadas
(`mito.log.1`, `.2`, `.3`), de hasta 1 MiB cada una. Los registros incluyen fecha,
fase, error, traza y versiones del sistema/runtime cuando están disponibles.

Si alguien no puede arrancar MITO, pídele estos cuatro archivos (los que existan).
No necesita que Python funcione. Si la carpeta de MITO no permite escribir, la
consola indica una alternativa dentro de `%TEMP%\MITO-logs`. `mito.cmd logs` muestra
ambas ubicaciones cuando Python funciona. No se pueden reconstruir fallos anteriores
a la instalación de esta mejora.

Las nuevas distribuciones llevan activado el proyecto de PostHog de MITO. Para
desactivar el envío, establece `posthog.enabled=false` en `config.properties`; el
log local seguirá funcionando. También puedes configurar tu propio proyecto:

```properties
posthog.enabled=true
posthog.host=https://eu.i.posthog.com
posthog.api_key=phc_TU_CLAVE_PUBLICA
```

Usa el host de ingestión de tu proyecto (el ejemplo corresponde a Europa) y su
Project API key, nunca una clave personal. Abre MITO de nuevo tras configurar.
Para desactivar el envío, cambia `posthog.enabled` a `false`.

Se envían tipo, mensaje depurado, funciones/líneas, versión, sistema, operación y,
cuando están disponibles, componente y fase. Se filtran credenciales reconocibles,
claves, parámetros de URL y rutas conocidas. Ese filtrado no garantiza detectar
datos sensibles arbitrarios en un mensaje: revisa los logs antes de compartirlos.
No se recopilan variables locales ni archivos de usuario. La captura cubre bootstrap,
CLI, operaciones principales de UI, Pocketools, configuración, actualización de MITO,
fallos fatales de Textual y trabajos MCP, no los errores internos de programas externos.

Si falla la red, el informe permanece en `logs\pending` y se vuelve a
intentar al arrancar. La cola conserva como máximo 100 informes de 128 KiB por
ubicación durante siete días; descarta los más antiguos cuando se llena. Python
intenta hasta 20 envíos por proceso y PowerShell hasta tres al finalizar el bootstrap.
No hay un servicio residente ni garantía de entrega. El UUID del informe permite
relacionar el error local con el evento remoto. Desactivar PostHog detiene nuevos
envíos; los pendientes pueden enviarse si se vuelve a activar antes de caducar.

Para comprobarlo sin modificar profiles, ejecuta `mito.cmd posthog-check`.
Envía una excepción de diagnóstico con `mito_diagnostic=true` y muestra la respuesta
HTTP o el error de envío. Usa un timeout de socket de diez segundos. Una respuesta
HTTP exitosa no confirma el procesamiento posterior: busca el evento `$exception`
en tu proyecto y en Error Tracking. Sólo se envía con la configuración activa.
Referencia: [API de errores de PostHog](https://posthog.com/docs/error-tracking/installation/manual).

## Índice

1. [Instalación y arranque](#instalación-y-arranque)
2. [La interfaz](#la-interfaz)
3. [Profiles y entornos](#profiles-y-entornos)
4. [Catálogo e instalación](#catálogo-e-instalación)
5. [Componentes instalados](#componentes-instalados)
6. [Terminales y aplicaciones](#terminales-y-aplicaciones)
7. [Aplicaciones del host](#aplicaciones-del-host)
8. [Accesos directos](#accesos-directos)
9. [Configuración](#configuración)
10. [Proxy y red](#proxy-y-red)
11. [Pocketools](#pocketools)
12. [Actualizaciones de MITO](#actualizaciones-de-mito)
13. [Agentes mediante MCP](#agentes-mediante-mcp)
14. [Línea de comandos](#línea-de-comandos)
15. [Datos, copias y traslado](#datos-copias-y-traslado)
16. [Compatibilidad y seguridad](#compatibilidad-y-seguridad)
17. [Diagnóstico](#diagnóstico)

## Instalación y arranque

1. Descarga el ZIP de la [última release](https://github.com/danielgube/mito/releases/latest).
2. Extráelo en una carpeta con permisos de escritura.
3. Ejecuta `mito.cmd`.

La primera ejecución muestra una bienvenida con las herramientas que necesita
descargar: Python, 7-Zip, Windows Terminal, innounp, Textual, Pillow, SDK MCP y sus
dependencias. Indica que requiere Internet y pide `¿Deseas continuar? [s/N]`.
Escribe `s` para iniciar la preparación; Intro cancela sin descargar herramientas.
Sus versiones y hashes están fijados. No hace falta una instalación global de
Python, Scoop ni Windows Terminal. La bienvenida no se repite cuando el runtime
ya está preparado; si cancelas o falla la preparación, vuelve a aparecer al abrir.

Puedes revisar únicamente esa bienvenida, sin instalar nada, con:
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File core\bootstrap.ps1 -PreviewWelcome`.

Comandos de arranque:

```bat
mito.cmd
mito.cmd start --profile desarrollo
mito.cmd ui
```

`mito.cmd` abre el gestor en el Windows Terminal privado. `start --profile` selecciona
un profile inicial y `ui` ejecuta la interfaz en la terminal actual.

Una carpeta de MITO equivale a una instancia y a un home. Para mantener otro home
independiente, extrae otra instancia en una ruta distinta.

## La interfaz

La barra lateral contiene:

- **Profiles:** selector del profile actual y **Gestionar profile**.
- **Inicio · Instalados:** vuelve al inventario de componentes.
- **Actualizar MITO:** aparece cuando hay una release posterior disponible.
- **Abrir Home:** abre el `AppData` portátil en el Explorador.
- **Configuración:** edita opciones globales, idioma, fuentes y variables.
- **Pocketools:** sincroniza paquetes de comandos compartidos.
- **MCP - disabled/enabled:** controla el servidor local para agentes.
- **MCP · Jobs:** monitor opcional de trabajos del servidor.
- **Estado de Internet:** vuelve a comprobar la conexión y, si procede, abre la
  autenticación del portal corporativo.

La zona inferior muestra el log de la sesión. **Copiar log** envía el texto completo
al portapapeles; los errores se resaltan en rojo en pantalla.

Atajos principales:

- `Ctrl+F`: abrir el catálogo y enfocar la búsqueda.
- `Esc`: volver a Inicio o cerrar un diálogo cancelable.
- `Ctrl+Q`: salir del gestor.

## Profiles y entornos

Un profile guarda:

- un workspace relativo a `workspaces/`;
- una lista ordenada de componentes `bucket/app` activos;
- variables de entorno propias.

Crear un profile no instala componentes. Selecciona **Gestionar profile → Crear
profile**, indica el nombre y el workspace, y después activa sus componentes desde
Inicio. No se admiten nombres que escapen de las carpetas administradas por MITO.

### Activar y desactivar

Inicio muestra todas las instalaciones. La casilla de cada fila indica si el
componente está activo en el profile seleccionado. Marcar o desmarcar sólo cambia
esa selección: no descarga ni elimina binarios.

Es posible activar componentes distintos de una misma familia, incluidos varios
Java. MITO conserva el orden del profile al construir `PATH`; si dos componentes
declaran el mismo comando o variable, prevalece el último. No intenta resolver esas
colisiones automáticamente.

Las pestañas y aplicaciones ya abiertas mantienen su entorno. Abre un proceso nuevo
para aplicar activaciones, actualizaciones o cambios de variables.

### Configurar variables

En **Gestionar profile → Configuración** se editan únicamente las variables del
profile. Los valores son texto literal: no se expanden referencias como `%HOME%`.
La cruz y `Esc` cancelan; **Guardar** aplica el conjunto completo.

Prioridad de menor a mayor:

1. variables declaradas por los componentes;
2. variables globales `env.*` de `config.properties`;
3. variables del profile.

No se pueden redefinir `PATH`, `MITO_*`, `HOME`, `USERPROFILE`, `APPDATA`,
`LOCALAPPDATA`, `TEMP`, `TMP`, `HOMEDRIVE` ni `HOMEPATH`.

`MITO_PROFILE` identifica el profile activo; `MITO_ROOT` apunta a la instalación y
`MITO_WORKSPACE` a la ruta absoluta del workspace configurado para ese profile.
Este último coincide con el directorio inicial de sus terminales y comandos, incluso
si el workspace tiene un nombre diferente al profile. Se recalcula en cada entorno
nuevo; usa `%MITO_WORKSPACE%` en CMD y `$env:MITO_WORKSPACE` en PowerShell.

### Compartir profiles

**Exportar** crea un JSON con los nombres `bucket/app` activos. No incluye binarios,
versiones, variables, workspace, persistencia, proxy ni aplicaciones del host.

**Importar** permite elegir ese JSON y un nombre nuevo. Antes de continuar muestra
el plan, las dependencias, qué está instalado y qué se sustituirá. Utiliza las
versiones disponibles en los catálogos del destinatario; no es un lock histórico.
Si la preparación falla, el profile no se crea.

```bat
mito.cmd profile export desarrollo entorno.mito.json
mito.cmd profile import entorno.mito.json --name equipo
```

### Eliminar un profile

**Eliminar profile** pide confirmación, conserva su workspace, componentes y datos,
y archiva una copia del JSON en `core/state/deleted-profiles`. El último profile no
se puede eliminar. Sus pestañas abiertas conservan el entorno hasta que se cierren.

## Catálogo e instalación

**Buscar e instalar nuevos componentes** abre los catálogos Scoop configurados.
La pantalla ofrece:

- búsqueda por nombre, bucket, descripción, palabras y números;
- casillas para incluir o excluir buckets, cuya selección se recuerda;
- estado **Disponible** o **Todavía no soportado**;
- descripción, notas y pasos relevantes del manifiesto;
- botones para instalar, elegir otra versión y actualizar los catálogos.

Las coincidencias completas y las de `bucket/app` aparecen antes que las parciales.
Por ejemplo, `java 21 lts` prioriza componentes que contienen los tres términos.

Selecciona una fila y pulsa **Instalar**. MITO resuelve primero todas las dependencias,
valida el plan, descarga en staging, verifica hashes, extrae y sólo entonces publica.
Instalar y activar son operaciones separadas; tras instalar, vuelve a Inicio y marca
el componente en los profiles deseados.

Si alguna descarga pendiente no tiene hash, un diálogo enumera los componentes y
pide confirmación antes de acceder a la red. Cancelar no descarga ni publica nada.

**Actualizar catálogos** reemplaza sus snapshots, pero no actualiza instalaciones ni
cambia activaciones. Las fuentes se configuran con `bucket.<nombre>`.

### Instalar otra versión

**Versiones…** permite instalar una versión concreta del componente seleccionado,
por ejemplo una anterior compatible con tu servidor. Tiene dos fuentes:

- **Histórico Git del bucket** (sólo buckets alojados en GitHub): lista las versiones
  que el bucket publicó y usa el manifiesto real de entonces, con su URL y su hash.
- **Otra versión** escrita a mano: se genera desde `autoupdate`, igual que
  `scoop install app@versión`. Si el manifiesto indica cómo obtener el hash, se
  verifica; si no, la descarga se confirma como cualquier descarga sin hash.

Si escribes una versión que está en el histórico, se usa el histórico. La versión
elegida sustituye a la instalada en la misma ruta y conserva activación y datos.
Queda **retenida**: Inicio la marca y **Actualizar** no la sustituye. Para volver a
recibir actualizaciones, pulsa **Liberar** en **Info**.

No todas las versiones antiguas siguen disponibles: el servidor puede haber retirado
la descarga, o el manifiesto actual puede no encajar con una versión antigua.

## Componentes instalados

Cada `bucket/app` tiene una sola versión instalada y una ruta estable:

```text
core/apps/<bucket>/<app>
```

### Info

**Info** muestra descripción, notas, binarios base, binarios específicos del profile
cuando existen y destinos persistentes. **Abrir** junto a una ruta la muestra en el
Explorador; si es un archivo, abre su carpeta. Las rutas pendientes aparecen
deshabilitadas. Consultar Info no crea datos, no activa nada y no prepara copias.
Si la versión está retenida, Info muestra su origen y el botón **Liberar**.

### Actualizar

Al abrir el gestor, MITO consulta como máximo una vez por día local los manifiestos
de los componentes instalados. No refresca todos los buckets. Si encuentra una
versión posterior compatible aparece **Actualizar**.

La actualización sustituye los binarios en la misma ruta, reconstruye las copias por
profile cuando corresponda y conserva activación y persistencia. También actualiza
componentes inactivos sin activarlos. Cierra la aplicación antes de actualizar y
abre nuevas pestañas después.

### Eliminar

**Eliminar** informa de la versión y de los profiles afectados. Retira el componente
de esos profiles y borra sus binarios y copias, pero conserva home, persistencia y
workspaces. No elimina dependientes ni accesos directos que puedan hacer referencia
al componente.

## Terminales y aplicaciones

MITO configura una ventana maximizada de Windows Terminal por instancia. El menú de
pestañas ofrece MITO, PowerShell y CMD para cada profile. Una pestaña nueva reconstruye
el entorno desde el estado actual; no hay sincronización en caliente ni procesos
auxiliares vigilando los cambios.

Cuando un componente activo declara un ejecutable disponible, su fila muestra
**Abrir**. El diálogo permite:

- **Independiente:** el programa continúa separado de la pestaña de MITO.
- **Acoplado:** se abre una pestaña dedicada que posee el árbol de procesos. Cerrarla
  termina forzosamente la aplicación y sus descendientes.
- **Argumentos:** se añaden después de los argumentos fijos del manifiesto. Usa
  comillas para agrupar valores con espacios.

En modo acoplado la pestaña conserva la salida, muestra el código del lanzador y
espera `Intro` al terminar. Procesos que reutilicen una instancia previa o deleguen
en un servicio externo pueden quedar fuera del árbol controlado.

## Aplicaciones del host

**Agregar aplicación del host** registra un `.exe`, `.bat`, `.cmd` o `.ps1` ya
existente. MITO conserva una referencia a su ruta: no copia ni instala el programa.

Estas aplicaciones reciben `PATH`, variables, workspace y `APPDATA` del profile
seleccionado. También admiten modo independiente o acoplado, argumentos y accesos
directos. **Info** muestra el ejecutable; **Eliminar** retira la referencia sin borrar
el archivo externo ni sus datos.

### Enlazar Data y Cache

**Data** corresponde a `AppData/Roaming` y **Cache** a `AppData/Local`. MITO puede
buscar una carpeta con el nombre de la aplicación o dejarte elegirla. Al enlazar,
crea una junction desde el home de MITO hacia los datos del host, compartida por los
profiles. Esto permite que la aplicación use directamente su configuración existente.

Cierra la aplicación antes de enlazar o desenlazar. Desenlazar y eliminar la
referencia conservan los datos del host; no los copian al home portátil.

## Accesos directos

### Acceso al gestor

**Gestionar profile → Acceso directo** crea en el escritorio un acceso a MITO con
el profile seleccionado. La etiqueta del icono admite hasta cuatro letras o números.
Las consolas se abren mediante pestañas; no se generan lanzadores de terminal en la
raíz de la instancia.

### Acceso a una aplicación

**Generar acceso directo** aparece en componentes activos y aplicaciones del host.
El diálogo guarda:

- profile;
- componente `bucket/app` o referencia del host;
- etiqueta del icono;
- modo independiente o acoplado;
- argumentos.

Los accesos de componentes no fijan versión ni huella, por lo que siguen resolviendo
la ruta estable después de actualizar. Los iconos ICO multirresolución se extraen del
ejecutable cuando es posible y Pillow superpone la etiqueta; si falla, se usa MITO.

Windows guarda rutas absolutas en `.lnk`. Si trasladas la instancia, regenera los
accesos desde la interfaz.

## Configuración

`terminal.defaultShell` controla el botón **+** de Windows Terminal y la primera
entrada del menú (**Ctrl+Shift+1**). Su valor predeterminado es `cmd`; también
admite `powershell` y `mito`. Puedes elegirlo en **ACCIONES → Configuración**.
CMD y PowerShell usan el profile seleccionado en el gestor MITO. Al cambiarlo,
Terminal recarga el menú; las pestañas abiertas conservan su entorno. Cambiar de
pestaña no cambia el profile seleccionado. Si hay varios gestores abiertos,
prevalece la última selección. El arranque de MITO sigue abriendo su interfaz.

**ACCIONES → Configuración** edita `config.properties` sin perder comentarios ni
claves desconocidas. Permite añadir opciones conocidas, variables `env.*`, fuentes
`bucket.*`, `pocketool.*` y campos del formulario del proxy. Sólo **Guardar** escribe;
cerrar o pulsar `Esc` cancela.

### Idioma

`mito.language=system` detecta el idioma de interfaz de Windows. También se puede
elegir manualmente cualquiera de los 24 idiomas oficiales de la Unión Europea.
Guarda y vuelve a abrir el gestor. Descripciones y notas de Scoop, información de
Pocketools y salida externa permanecen en su idioma original.

### Variables globales

```properties
env.MAVEN_OPTS=-Xmx2g
env.MI_VARIABLE=desarrollo
```

Se aplican a todos los profiles, por debajo de sus variables propias. El archivo
separa clave y valor por el primer `=` y no interpreta comillas ni escapes.

### Fuentes de catálogo

```properties
bucket.main=https://github.com/ScoopInstaller/Main
bucket.extras=https://github.com/ScoopInstaller/Extras
bucket.versions=https://github.com/ScoopInstaller/Versions
bucket.java=https://github.com/ScoopInstaller/Java
```

Se admiten repositorios Git HTTP/HTTPS y carpetas locales importadas. Refrescar un
bucket cambia su snapshot, nunca las versiones ya instaladas.

## Proxy y red

Ejemplo de proxy sin credenciales persistentes:

```properties
http_proxy=http://proxy.organizacion.example:8080
https_proxy=http://proxy.organizacion.example:8080
no_proxy=localhost,127.0.0.1,.organizacion.example
enable_proxy_authentication=true
proxy_authentication_url=https://portal.organizacion.example/login
proxy_authentication_username_field=username
proxy_authentication_password_field=password
```

El botón de Internet ejecuta una comprobación breve. Si hay red local pero falta
Internet y existe un portal configurado, MITO solicita usuario y contraseña en un
diálogo; sólo se conservan en memoria. El POST permanece en el mismo origen, valida
TLS y no reenvía credenciales mediante redirecciones.

El flujo está pensado para portales que autorizan la conexión del equipo. No
implementa HTTP 407 Basic/NTLM, SSO interactivo ni CAPTCHA. Los hijos reciben
`HTTP_PROXY`, `HTTPS_PROXY` y `NO_PROXY`, pero cada aplicación decide si los respeta.
MITO no modifica el proxy global de Windows.

El bootstrap inicial usa `https_proxy` si está declarado. Si no hay proxy en
`config.properties`, detecta el de Windows (manual, script PAC o WPAD), comprueba si
responde directamente, exige HTTP 407 o redirige a un portal, y pregunta si guardar
`http_proxy`, `https_proxy`, `no_proxy` y el portal. Con un portal, pide usuario y
contraseña en la consola antes de descargar; no se guardan. Para ver el proxy que
Windows resuelve, incluido el de un PAC:
`[Net.WebRequest]::GetSystemWebProxy().GetProxy([Uri]'https://github.com/')`.

## Pocketools

Pocketools publica paquetes de scripts en el `PATH` de todos los profiles. Configura
un repositorio Git o una carpeta local:

```properties
pocketool.equipo=https://servidor.example/herramientas.git
pocketool.local=C:/herramientas-locales
```

Cada origen debe contener en su raíz:

```json
{"info": "Herramientas y alias compartidos del equipo"}
```

Abre **Pocketools** y pulsa **Actualizar**. MITO valida el snapshot y lo publica en
`pocketools/<nombre>`; abre una pestaña nueva para cargarlo en `PATH`. Si la obtención
falla, conserva la copia anterior. Quitar la propiedad lo retira de nuevos entornos,
pero no borra sus archivos.

MITO no ejecuta scripts durante la sincronización, no instala dependencias ni admite
submódulos o Git LFS. Los repositorios privados necesitan credenciales ya disponibles
en Git.

### Crear y editar comandos

En **Pocketools → Crear paquete**, escribe el nombre de la carpeta y una descripción.
Por ejemplo, `ceniza` crea `C:\mito\pocketools\ceniza` si ésa es tu instalación.
El destino se muestra automáticamente: no hay selector de ruta ni botón Examinar.
La descripción inicializa `pocketool.json`. Una carpeta vacía ya existente se puede
usar; una carpeta con archivos no se sustituye. Para paquetes registrados, entra en
**Comandos**. **Gestionar con Git** inicializa el repositorio en esa misma carpeta;
la URL es opcional hasta subir cambios y el remoto debe existir previamente.

En **Comandos → Nuevo comando**, escribe el nombre sin extensión y el contenido.
El selector de profile sirve también de referencia para la plantilla: incluye
comentarios con `MITO_PROFILE`, `MITO_ROOT`, `MITO_WORKSPACE`, `HOME` y las rutas de
AppData y temporales. Son ejemplos de los valores disponibles, no asignaciones;
al ejecutar se usa el entorno del profile que lance el comando. CMD usa comentarios
`::` y PowerShell `#`. Cambiar de tipo adapta la plantilla sólo mientras siga intacta;
no sustituye código escrito ni añade comentarios a scripts existentes.
CMD es el tipo predeterminado; también puedes elegir PowerShell. Los nombres admiten
letras ASCII, números, guion y guion bajo, sin espacios ni nombres reservados de
Windows. El editor admite hasta 1 MiB en UTF-8 y no convierte código entre lenguajes.
PowerShell se guarda con BOM para Windows PowerShell; la plantilla CMD selecciona
la página de códigos UTF-8. Los scripts antiguos con otra codificación deben
convertirse o editarse externamente.

**Abrir carpeta** abre el origen en el Explorador, donde puedes organizar scripts
auxiliares de cualquier lenguaje. Usa `%~dp0` en CMD o `$PSScriptRoot` en PowerShell
para localizar esos archivos. MITO no instala sus dependencias.
**Guardar** escribe directamente en la carpeta del paquete, sin ejecutar el script.
Los auxiliares añadidos desde el Explorador están en esa misma carpeta. No hay un
paso adicional de Aplicar cambios. Abre una nueva terminal de MITO para cargar un
paquete recién registrado. Si alguien cambia el archivo mientras lo editas, vuelve
a abrirlo antes de guardar. **Editar** y **Acceso directo** están deshabilitados hasta
seleccionar un comando; el acceso también requiere un profile.

Los paquetes creados se configuran con `pocketool.<nombre>=pocketools/<nombre>`,
relativo a MITO para conservar su ubicación al mover la instalación. No muestran
**Actualizar**: su carpeta es el árbol de trabajo y conserva `.git` y los auxiliares.
Para un paquete consumido por URL, **Comandos → Editar copia local** descarga su
historial Git y lo asocia a los archivos instalados, conservando posibles cambios
locales. A partir de ahí se edita en `pocketools/<nombre>` y las actualizaciones se
gestionan desde Git, sin pull automático. Las diferencias respecto al remoto aparecen
en la revisión de Git. Las configuraciones antiguas con carpetas externas siguen
siendo compatibles: Guardar publica su copia, y Actualizar incorpora auxiliares
editados fuera del editor; no se trasladan automáticamente.

Para los PS1 se genera un CMD homónimo si no existe un comando que ocupe ese nombre.
En los paquetes locales ese lanzador vive en el propio repositorio y se puede incluir
en el commit. No aparece duplicado en Comandos; al renombrar el PS1, MITO retira sólo
los lanzadores generados que no hayan sido modificados.

### Git y accesos directos

**Guardar y revisar Git** guarda primero y abre la selección de archivos del commit.
Puedes incluir auxiliares o eliminaciones explícitamente; el resto del trabajo,
incluidos los archivos preparados previamente, permanece fuera del commit.
**Crear commit** registra esos archivos en la rama actual. **Commit y subir** también
los sube; **Subir commits** permite reintentar una subida sin crear otro commit.
La subida incluye todos los commits pendientes de esa rama, usa HTTP/HTTPS y nunca
fuerza la actualización remota. No se cambian la identidad global ni un origin ya
configurado. Un fallo de Git conserva los archivos y cualquier commit creado;
los archivos seleccionados pueden quedar preparados si falla el commit. Git debe
tener identidad y credenciales configuradas de antemano. No se ejecutan hooks ni
filtros de Git. No hay pull automático, selección de ramas, LFS ni submódulos.

Los comandos heredan Java, Node y el resto del entorno de la terminal de MITO.
Para los `.ps1` con nombre compatible se genera un lanzador `.cmd` en la copia
instalada, sólo cuando no existe ya un comando de ese nombre, para utilizarlos
también desde CMD. No se modifica el PATH del sistema. Si dos paquetes declaran el
mismo comando, prevalece el orden del PATH.

**Comandos → Acceso directo** permite elegir profile, etiqueta de hasta cuatro
caracteres, color de fondo y argumentos opcionales. Usa el icono de MITO de perfil
sobre el engranaje sólo en el acceso: la barra de tareas y la ventana conservan el
icono de MITO. El acceso del escritorio abre una pestaña de Windows Terminal
de esta instancia, prepara el entorno y workspace del profile y ejecuta la copia
instalada de ese comando. Conserva paquete y archivo, sin versión fija ni dependencia
de la carpeta de desarrollo. La pestaña espera Intro al finalizar para poder leer la
salida. Renombrar o quitar el comando/profile requiere regenerar el acceso, igual
que mover la instancia. Para CMD, los argumentos con comillas literales o saltos
de línea se rechazan; utiliza un script explícito para esos casos.

## Actualizaciones de MITO

MITO comprueba una vez por día local la URL configurada en `mito.update_url`. Acepta
una API de última release compatible con GitHub o Gitea. Si encuentra una versión
estable posterior, muestra **Actualizar MITO**.

La preparación ocurre fuera de la instancia. Tras confirmar se abre un CMD separado
que espera a que cierres aplicaciones y ventanas de MITO. Se sustituyen programa,
arranque e identidad; se conservan configuración, profiles, componentes, home y
workspaces. Un checkout con `.git` debe actualizarse mediante Git.

### Actualización offline

Copia en `update/`:

```text
mito-<versión>-windows-x64.zip
mito-<versión>-windows-x64.zip.sha256
```

El sidecar es opcional. La release local posterior tiene prioridad y aparece como
**Offline**; sin hash requiere confirmación. El ZIP se conserva. Como las releases
mínimas no incluyen runtimes, el modo offline exige que no cambie el bootstrap.

### Servidor de releases propio

```properties
mito.update_url=https://git.organizacion.example/api/v1/repos/herramientas/mito/releases/latest
```

La URL debe devolver JSON de release, no una página HTML. Los intentos exitosos o
fallidos se recuerdan durante el día para evitar consultas repetidas.

## Agentes mediante MCP

Pulsa **MCP - disabled**. El diálogo muestra el puerto y configuraciones para Codex,
Claude Code, VS Code y HTTP. Pulsa **enabled** para abrir una pestaña **MITO MCP** y
copia la configuración del cliente que vayas a usar. MITO no modifica esos clientes.

El servidor usa Streamable HTTP en `127.0.0.1`, sin autenticación ni exposición a
otros equipos. Cada apertura del gestor empieza desactivada; se recuerdan identidad
y puerto, no el estado. Cerrar la pestaña MCP, su Terminal o el gestor propietario
apaga el servidor y cancela los comandos pendientes.

Las herramientas MCP permiten:

- consultar, crear, configurar y eliminar profiles;
- buscar y refrescar catálogos;
- planificar, instalar, actualizar y retirar componentes;
- activar o desactivar componentes;
- editar configuración global y Pocketools;
- ejecutar comandos con un profile, consultar su salida y cancelarlos.

Instalar y activar siguen siendo operaciones distintas. Las descargas sin hash
esperan confirmación humana en la interfaz antes de empezar. Los comandos no están
aislados: usan los permisos del usuario y pueden acceder fuera del workspace.

El monitor visual de trabajos está apagado por defecto. Actívalo con
`mcp.jobs.monitor=true`; aparecerá **MCP · Jobs**. Permite ver estado, cargar salida
al pulsar **Actualizar** y cancelar. Ocultarlo no cancela los trabajos ni desactiva
las herramientas del agente.

## Línea de comandos

Comandos de uso habitual:

```bat
mito.cmd installed
mito.cmd search "java 21 lts"
mito.cmd search --compatible python
mito.cmd bucket refresh
mito.cmd bucket refresh java
mito.cmd bucket import equipo C:\catalogos\equipo

mito.cmd profile list
mito.cmd profile show desarrollo
mito.cmd profile create desarrollo --workspace mi-proyecto
mito.cmd profile delete desarrollo
mito.cmd profile export desarrollo entorno.mito.json
mito.cmd profile import entorno.mito.json --name equipo

mito.cmd install java/temurin25-jdk
mito.cmd install main/maven --profile desarrollo
mito.cmd activate main/maven --profile desarrollo
mito.cmd deactivate main/maven --profile desarrollo

mito.cmd terminal --profile desarrollo --type powershell
mito.cmd terminal --profile desarrollo --type cmd --new
mito.cmd shell --profile desarrollo --type cmd
mito.cmd run --profile desarrollo --app extras/vscode
mito.cmd run --profile desarrollo --app versions/tomcat9 -- run

mito.cmd shortcut --profile desarrollo --badge DEV
mito.cmd app-shortcut extras/vscode --profile desarrollo --badge CODE
mito.cmd doctor
```

`install --profile` conserva el atajo de instalar y activar en una sola orden. Para
ver la ayuda completa de cualquier comando, añade `--help`.

## Datos, copias y traslado

Rutas principales:

| Ruta | Contenido |
| --- | --- |
| `home/` | Home, AppData, temporales y datos portátiles |
| `home/persist/<bucket>/<app>/` | Persistencia compartida |
| `home/profiles/<profile>/persist/` | Persistencia aislada por profile |
| `core/apps/<bucket>/<app>/` | Instalación base estable |
| `core/profile-apps/<profile>/<bucket>/<app>/` | Copias aisladas estables |
| `profiles/` | Selecciones, workspace y variables |
| `workspaces/` | Proyectos de los profiles |
| `core/buckets/` | Snapshots de catálogos |
| `pocketools/` | Paquetes compartidos sincronizados |

La regla es genérica: un manifiesto que combina `persist` y `shortcuts` obtiene copia
de binarios y persistencia por profile. Los demás comparten instalación y persistencia.
Desactivar o eliminar un profile conserva sus datos. Eliminar una instalación retira
los binarios y copias, no la persistencia.

MITO puede moverse o copiarse como carpeta. Cierra antes sus aplicaciones. El
entorno, los comandos, el Terminal y los enlaces persistentes se reconstruyen al
abrir pestañas o aplicaciones. Además, MITO recuerda su ruta y, al arrancar en otra,
lo indica en el log y repara lo que guarda rutas absolutas:

- **Movida** (la ruta anterior ya no existe): reescribe hacia la nueva ruta los
  accesos del escritorio creados por MITO.
- **Copiada** (la ruta anterior sigue siendo una instancia): los accesos existentes
  siguen abriendo el original y la copia deja de considerarlos suyos. Crea accesos
  nuevos desde la copia si los necesitas.
- Explorador y la mayoría de copiadores convierten los enlaces de persistencia en
  carpetas normales. MITO aparta esas copias a `core/staging/relocated-persist-*`,
  sin borrarlas, y vuelve a enlazar los datos de `home`.
- Un enlace **Host data/cache** copiado queda como carpeta normal; MITO lo avisa y
  puedes volver a enlazarlo desde Inicio.

Los accesos `.lnk` que no creó MITO y las rutas absolutas que una aplicación guarde
en su propia configuración no se modifican.

Para convertir una instancia antigua a rutas estables:

```bat
mito.cmd migrate-storage
```

Ejecuta el comando con todas las aplicaciones cerradas. Si detecta contenido
ambiguo, se detiene sin borrar nada.

## Compatibilidad y seguridad

El motor admite actualmente:

- arquitectura Windows x64;
- descargas y redirecciones HTTP/HTTPS;
- hash MD5, SHA-1, SHA-256, SHA-512 y otros digest fijos reconocidos;
- hash ausente, vacío, `null` o `skip`, siempre con confirmación previa;
- ZIP/nupkg, 7z, RAR, TAR comprimido, MSI con CAB internos y archivos sueltos;
- `extract_dir`, `extract_to`, `persist`, `env_add_path`, `env_set`, `bin`,
  `shortcuts`, dependencias y `pre_install`.

Los archivos se extraen en staging con límites y validación contra traversal,
rutas reservadas, ADS, enlaces y duplicados sin distinción de mayúsculas.
El log distingue descarga, extracción, preparación y validación. En MSI indica
el CAB actual y el total, además de recuentos de archivos durante extracción y
copia. Los paquetes con muchos archivos pueden tardar varios minutos después
de terminar la descarga; los recuentos corresponden a cada fase por separado.

`pre_install` se ejecuta con PowerShell sin perfil después de extraer y antes de
publicar. Es código de confianza del catálogo y tiene los permisos del usuario: no
es un sandbox. `post_install` se omite y se indica en Info y en el log. Inno
Setup, módulos PowerShell y cualquier paso sin soporte bloquean la instalación.

Los MSI se extraen como archivos: no se abre su asistente ni se ejecutan sus
acciones, servicios o cambios de registro. Se reconstruyen los nombres y carpetas
de la imagen administrativa y se aplican `extract_dir` y `extract_to`. Una vez
validados, aparecen en Inicio y se activan, actualizan y eliminan como los demás
componentes. Los MSI con medios externos, archivos sin comprimir o CAB divididos
se rechazan sin publicar el componente. Extraer no resuelve dependencias del host:
por ejemplo, la cadena MSVC de Rust puede necesitar C++ Build Tools y Windows SDK.

La etiqueta **Compatible** significa que el motor entiende el manifiesto, no que
MITO haya auditado todas las escrituras o probado cada aplicación. Usa fuentes de
confianza y revisa las notas del catálogo.

En el catálogo, un componente presente en el inventario figura como **Instalado**
y su botón **Instalar** queda deshabilitado, aunque no esté activo en el profile.
Las actualizaciones se realizan desde Inicio. Al eliminar el componente vuelve a
estar disponible para instalar si el manifiesto está soportado.

## Diagnóstico

Ejecuta:

```bat
mito.cmd doctor
```

Comprueba runtime, herramientas privadas, profiles, Terminal y dependencias del
servidor MCP sin activarlo. Si una operación falla, copia el log desde la interfaz.

Para detalles internos y límites verificados consulta la
[arquitectura](spec/ARCHITECTURE.md), el [estado de continuidad](spec/CONTINUITY.md)
y la [referencia técnica de uso](spec/USAGE.md).
