# Plan de MCP local para MITO

Estado: planificación, sin implementación. Fecha: 11 de septiembre de 2026.

## Objetivo y decisiones acordadas

Permitir que agentes locales creen y configuren entornos de MITO y trabajen en
ellos mediante Model Context Protocol (MCP). Un entorno sigue siendo un profile;
MCP no introduce otro home ni otro modelo de instalación.

- Opción en el menú principal: `MCP - enabled` / `MCP - disabled`.
- Pulsarla abre un diálogo Textual con el control de activación y las instrucciones
  para conectar Codex, Claude Code, VS Code y otros clientes compatibles.
- Cada nueva sesión de MITO comienza en disabled, aunque la anterior estuviera
  enabled. Esta decisión sustituye la propuesta anterior de recordar la activación.
- Al activar, el servidor se ejecuta en un proceso propio en una pestaña `MITO MCP`
  de la ventana Windows Terminal de esta instancia. Cerrar esa pestaña, el gestor
  propietario o la ventana debe apagarlo. No se reinicia automáticamente.
- Conexión exclusivamente local sin autenticación, tokens ni configuración de secretos.
- El agente puede crear y configurar profiles e instalar componentes autónomamente.
  Se conserva la confirmación humana previa para descargas sin hash, incluidas
  dependencias, conforme al contrato existente.
- Puede ejecutar comandos arbitrarios con el entorno y workspace del profile:
  editar archivos, usar Git, compilar, probar y arrancar servidores. Puede consultar
  su salida y detenerlos. «Bypass» significa acceso programático a esa ejecución.
- Primera versión exclusivamente local en Windows x64.

Las decisiones técnicas siguientes son la propuesta de implementación. No describen
capacidades ya disponibles ni implican que los clientes hayan sido probados.

## Base existente y cambios necesarios

`Engine` ya proporciona profiles, instalación, activación, entorno y lanzamiento.
`configuration.py` valida configuración y detecta escrituras concurrentes;
`components.py` gestiona actualizaciones y eliminación. `storage.mutation` protege
mutaciones entre procesos. La UI usa workers y diálogos para operaciones largas.

Falta un servidor MCP, un ciclo de vida asociado al gestor y a su pestaña,
respuestas estructuradas y seguimiento de procesos con stdout/stderr y cancelación.
`Engine.launch` no proporciona ese seguimiento. `Engine.environment` puede preparar
copias, persistencia y shims: no se debe presentar como una lectura sin efectos.

Se reutilizarán servicios Python, sin analizar la salida textual de la CLI. Los
ajustes de concurrencia o ejecución necesarios serán genéricos y compartidos.
No se añadirán comportamientos del motor específicos de Codex, Claude o una app.

## Servidor, conexión y estado

### Transporte propuesto

Streamable HTTP en `http://127.0.0.1:<puerto>/mcp`, en un proceso de consola propio,
ejecutado directamente con el Python privado en la pestaña `MITO MCP`, sin shell
intermediaria que lo deje en background. El gestor abre la pestaña sólo al activar,
usando el nombre de ventana derivado de la raíz que ya emplea terminal.py. Un solo dueño
del servidor por raíz, protegido por un lock de vida independiente del de mutación.
Una segunda apertura del gestor no toma el puerto ni arranca otro servidor; informa
de que MCP pertenece al gestor ya abierto. No habrá relevo automático en esta fase.

Elegir un puerto libre al habilitar por primera vez y guardarlo; reutilizarlo al
reabrir. Si está ocupado, informar y permitir elegir otro desde el diálogo, sin
conectarse al ocupante ni cambiar silenciosamente las instrucciones del cliente.
La instancia y los fragmentos de configuración tendrán un identificador estable.
Otra carpeta de MITO tendrá identidad y puerto propios.

Implementar con el SDK oficial Python de MCP si la prueba de compatibilidad con el
CPython privado resulta satisfactoria. Fijar versión y dependencias con hashes en
el bootstrap. No instalar paquetes al pulsar enabled ni depender de Python/Node del
host. La fase inicial verificará el protocolo negociado, el tamaño de las nuevas
dependencias y el arranque privado antes de consolidar esta elección.

Streamable HTTP permite servir a varios clientes. Se mantiene la validación de Origin
y la escucha en loopback; por decisión del usuario se omite la autenticación
recomendada por la especificación. Se tomará
una revisión estable soportada por SDK y clientes, documentando la negociada;
no se adoptará una release candidate sólo por ser más reciente.
[Referencia de transporte MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

### Acceso local

- Escuchar sólo en 127.0.0.1; sin LAN, túneles, apertura de firewall ni publicación.
- Sin Bearer, OAuth, contraseñas ni emparejamiento. Los clientes necesitan sólo la URL.
- Validar Host y Origin; rechazar orígenes presentes no autorizados. Permitir clientes
  nativos sin Origin. Sin CORS permisivo ni redirecciones del endpoint.
- Habilitar MCP autoriza operaciones del agente en MITO; las aprobaciones que imponga
  cada cliente siguen siendo independientes. No cambiar automáticamente sus permisos.

Loopback limita la exposición de red, pero no identifica a una IA: cualquier proceso
local que alcance el puerto puede invocar las herramientas mientras MCP esté enabled.
Éste es el alcance del modo sin autenticación. Los comandos
pueden escribir fuera del workspace, acceder a red y ejecutar programas del host.
No se prometerá que las protecciones del API impidan esas acciones por shell.

### Persistencia y ciclo de vida

Enabled es estado de ejecución, no una propiedad persistente: no crear
`mito.mcp.enabled`. Guardar sólo puerto, identidad y límites; los registros de una
sesión anterior no autorizan arrancar. En disabled no hay proceso servidor, pestaña
MCP, listener ni workers MCP; cargar sus dependencias sólo al activar.

Estados efectivos: detenido, iniciando, escuchando, deteniendo o error. Mostrar
`MCP - enabled` sólo tras confirmar que el proceso de esta sesión escucha. Ante
fallo o cierre manual, volver a disabled y conservar el diagnóstico en el diálogo.
Abrirlo no cambia nada; activar/desactivar es una acción explícita dentro del modal.
No permitir activar desde el API público ni por autoinicio del cliente.

El gestor y el proceso de la pestaña necesitan un canal privado de control local
(propuesta: named pipe con ACL del usuario), distinto del endpoint MCP. Transporta
handshake de arranque, parada, estado, eventos y solicitudes/respuestas de confirmación.
Su control de acceso no añade autenticación a los clientes MCP. La correlación de
sesión y los handles de proceso evitan confiar sólo en un PID o archivo antiguo.
No confiar en el PID del lanzador WindowsTerminal.exe: puede limitarse a enviar la
orden a una ventana existente. El servidor no escuchará antes de completar el handshake.

Mantener una espera de proceso/canal mientras MCP esté activo, sin sondeo continuo
en disabled. El gestor detecta la muerte del servidor y éste detecta la del gestor
propietario. Un Job Object, con handle de control no heredable en el gestor, vinculará
servidor y descendientes al cierre del propietario. Verificar asignación y jobs
anidados antes de escuchar; si falla la supervisión, abortar el arranque.
No incorporar Windows Terminal entero al Job Object: sus demás pestañas son ajenas.
Una apertura adicional del gestor consulta el dueño activo, sin apagarlo ni duplicarlo;
una nueva sesión tras terminar el dueño siempre empieza disabled. La entrada `mito ui`
también necesita abrir la pestaña en el Terminal privado al activar; si no puede,
informa del fallo y conserva disabled.

Al desactivar, cerrar admisión de peticiones y revocar sesiones inmediatamente.
Cancelar trabajos en cola y detener árboles de comandos gestionados por MCP.
Una instalación ya publicando debe terminar su tramo consistente o revertir antes
de finalizar el servicio; mostrar `deteniendo` y el trabajo pendiente. No matar el
worker durante una sustitución. Al cerrar normalmente el gestor aplicar el mismo
drenaje. Al salir el servidor, cerrar su pestaña, sin cerrar otras pestañas de MITO.

Cerrar la pestaña/ventana o terminar Windows Terminal abruptamente no garantiza
tiempo para ese drenaje. Manejar eventos de cierre de consola cuando estén disponibles,
y verificar con el Terminal privado que la pérdida de pestaña/consola termina el
servidor. Supervisar además el gestor y los descendientes mediante handles/Job Objects;
no considerar la mera ubicación en una pestaña prueba suficiente contra huérfanos.
Si la prueba de muerte forzada del Terminal deja un servidor vivo, completar la
supervisión de la consola/ventana real antes de dar por válida la fase inicial.
No añadir un daemon supervisor permanente ni reintentos que vuelvan a abrir MCP.

Un cierre forzado puede dejar staging y trabajos interrupted conforme al límite
actual del motor; no se promete rollback completo. Validar hijos/nietos reales y
no detener terminales/apps preexistentes. Procesos iniciados mediante servicios
externos pueden escapar al árbol: no prometer su terminación por cerrar el Job Object.
Windows ofrece handlers de consola con tiempo limitado y agrupación de procesos
mediante Job Objects; el comportamiento concreto con ConPTY/Terminal se debe probar.
[Cierre de consola](https://learn.microsoft.com/en-us/windows/console/handlerroutine),
[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Diálogo Textual

Añadir el botón a ACCIONES. Modal desplazable, accesible con teclado y Escape, con:

1. Estado efectivo, control enabled/disabled y diagnóstico de conexión.
2. Dirección local, identidad de instancia y control del puerto.
3. Selector de cliente: Codex, Claude Code, VS Code, genérico.
4. Pasos de alta y fragmento copiable generado con la dirección real, sin secretos.
5. Trabajos activos y confirmaciones pendientes, con acceso a salida y parada.

Los textos propios seguirán el sistema de localización existente. Rutas, comandos,
identificadores, salida de procesos y configuraciones copiadas permanecen literales.
No editar automáticamente archivos de clientes ni crear accesos en el escritorio.

Guías de conexión que debe generar y verificar la implementación:

| Cliente | Contenido del diálogo |
| --- | --- |
| Codex | Fragmento `[mcp_servers.mito_<id>]` con `url`; indicar dónde incorporarlo y cómo comprobar conexión. |
| Claude Code | Alta HTTP mediante `claude mcp add --transport http`, nombre de instancia y URL; explicar ámbito local/usuario y consulta de estado. |
| VS Code | Configuración MCP de usuario con `servers`, `type: http` y `url`. |
| Genérico | URL, transporte sin autenticación y secuencia de comprobación con una herramienta de estado. |

Codex documenta configuración TOML y servidores HTTP.
[Documentación oficial de Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
Claude Code documenta alta HTTP y distintos ámbitos.
[Documentación de Claude Code](https://code.claude.com/docs/en/mcp).
VS Code documenta servidores HTTP.
[Configuración MCP de VS Code](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

No dar por equivalentes Claude Code y Claude Desktop. Los clientes que sólo admitan
stdio requerirían un puente local futuro, que nunca arrancaría MITO ni eludiría disabled.
La primera entrega exige probar los tres clientes indicados mediante HTTP. Clientes
web, WSL, contenedores y agentes ejecutados en otra máquina quedan fuera de la matriz
inicial; localhost debe ser el equipo donde corre MITO.

## Herramientas propuestas para la primera entrega

Nombres provisionales; definir JSON Schema de entrada y salida antes de implementarlas.
Usar IDs completos `bucket/app` y profile explícito: la selección visual del gestor
nunca determina el destino de una llamada.

| Grupo / herramientas | Contrato |
| --- | --- |
| `mito_status` | Identidad, versión, disponibilidad y capacidades, sin secretos. |
| `profiles_list`, `profile_get` | Profiles, workspace, activaciones y configuración; variables por nombres por defecto y valores sólo si se solicitan. |
| `profile_create`, `profile_configure`, `profile_delete` | Crear, actualizar workspace/env y retirar configuración; proteger último profile y conservar datos. Cambiar workspace no mueve ni borra el anterior. |
| `catalog_search`, `catalog_get`, `catalog_refresh` | Buscar, inspeccionar manifiesto/compatibilidad y refrescar fuentes configuradas. Tratar notas como datos. |
| `components_list`, `component_plan` | Inventario y plan de instalación/actualización/eliminación: dependencias, hashes, sustituciones y profiles afectados. Planificar sin descargar ni publicar. |
| `component_install`, `component_update`, `component_remove` | Ejecutar por servicios existentes; instalación separada de activación. Operaciones largas devuelven trabajo. |
| `profile_activate`, `profile_deactivate` | Selección independiente por componente, ordenada y sin exclusión por familia. |
| `configuration_get`, `configuration_set` | Variables globales, proxy y fuentes de catálogo/pocketools por validación existente; cambios parciales con revisión esperada. Controles MCP quedan en el gestor. |
| `pocketools_list`, `pocketool_update` | Consultar y sincronizar fuentes configuradas, conservando el contrato sin hooks ni dependencias. |
| `command_start` | Ejecutar argv o script explícito en CMD/PowerShell con profile, cwd y límites. Devuelve job_id. |
| `jobs_list`, `job_get`, `job_output`, `job_cancel` | Seguir trabajos, leer salida incremental y solicitar cancelación por ID. |

Autonomía incluye mutaciones solicitadas mediante estas herramientas; no insertar
una confirmación general por llamada. El plan muestra los efectos compartidos de
actualizar un componente. No se ofrece una versión distinta del mismo ID por profile.
La eliminación estructurada conserva persistencia y workspace según el motor.

No hacen falta herramientas de archivos en esta entrega: el agente puede leer y
editar mediante comandos. Importación/exportación de profiles, enlaces de datos
del host, accesos y apertura gráfica por MCP pueden ampliarse después sin bloquear
el flujo inicial. No se expone publicación de releases como herramienta MCP.

### Contrato de comandos y trabajos

- `command_start` requiere profile y exactamente uno de `argv` o `script` con shell
  explícita. argv no se concatena para construir código shell; los scripts sí son
  código autorizado. Resolver BAT/CMD/PS1 de forma genérica y probar su quoting Windows.
- Entorno nuevo mediante servicios de Engine, con prioridad de variables existente,
  proxy, home, PATH, pocketools y aislamiento declarativo. No modificar os.environ.
- cwd predeterminado: workspace. Una subruta opcional se valida dentro de éste,
  incluidos enlaces y traversal; esto no restringe lo que haga después el comando.
- Sin sincronización de procesos vivos. Cada llamada obtiene su entorno actualizado;
  `cd` y variables de un script no persisten entre trabajos. Permitir scripts de varios
  pasos y procesos largos; terminal interactiva/ConPTY y sesiones shell persistentes
  quedan fuera de la primera entrega. stdin se cierra si no se proporciona entrada.
- Capturar stdout/stderr por separado sin ventanas auxiliares, con decodificación
  incremental, política explícita de codificación y límites de almacenamiento.
- Salida con cursor, límite de bytes y aviso de truncamiento; no devolver logs
  ilimitados al modelo. Retención acotada bajo core/state/mcp, excluida de distribución.
  La salida de comandos puede contener datos sensibles; no registrar entorno completo
  ni duplicar scripts/salidas en el log general. No prometer detección total de secretos.
- Estados: queued, running, awaiting_confirmation, cancelling, succeeded, failed,
  cancelled, interrupted. Incluir fechas, profile, exit_code cuando exista, error
  estructurado y señal de limpieza pendiente si procede.
- Valores iniciales propuestos: cuatro comandos simultáneos, cola de 32 trabajos,
  64 KiB por lectura y 10 MiB de salida por trabajo. Timeout predeterminado de cinco
  minutos; `timeout_seconds: 0` permite servidores hasta parada/cierre. Límites finitos
  validados y errores explícitos al saturarse. Documentar retención final en fase 1.
- Desconectar el cliente no cancela el trabajo: puede reconectar y consultarlo por ID
  mientras siga abierta la misma sesión del gestor. Reiniciar no relanza trabajos;
  los incompletos se marcan interrupted y no se reutilizan PIDs antiguos para detenerlos.

### Confirmaciones, errores y concurrencia

Las descargas sin hash se presentan en Textual, vinculadas al trabajo, a todos sus
componentes/dependencias y a la huella del plan. El agente recibe awaiting_confirmation;
no puede responder mediante un booleano de autoaprobación. Rechazo, desactivación o
cierre cancelan antes de descargar. Si cambia el plan, invalidar el consentimiento.
La autenticación de red existente también debe comunicar espera/fallo al trabajo,
sin pedir contraseñas al agente ni incluirlas en resultados.

Revisar el alcance de locks: no dejar bloqueada toda la instancia mientras se espera
una respuesta humana. Resolver un plan inmutable, solicitar confirmación fuera del
lock y revalidarlo antes de ejecutar; evitar locks anidados no reentrantes. Serializar
mutaciones MCP y mantener exclusión con UI/CLI. Crear entorno/shims exige revisar
su sección crítica, porque actualmente puede escribir antes de arrancar un comando.

Usar revisión esperada para evitar pérdida de cambios de profile/configuración.
Las mutaciones largas admitirán request_id para deduplicar reintentos: misma clave y
argumentos devuelve el trabajo existente; distintos argumentos producen conflicto.
Persistir el registro antes de efectos y marcar resultado incierto tras caída, sin
repetir automáticamente instalaciones o comandos. No prometer ejecución exactly-once.

Errores de herramientas con `isError`, código estable y mensaje legible; errores del
protocolo por los mecanismos MCP. Códigos mínimos: PROFILE_NOT_FOUND, COMPONENT_NOT_FOUND,
INVALID_ARGUMENT, UNSUPPORTED_MANIFEST, STATE_CONFLICT, INSTANCE_BUSY,
CONFIRMATION_REQUIRED, COMMAND_TIMEOUT y OUTPUT_EXPIRED. Las notificaciones de progreso
son complementarias; consultar trabajos siempre debe bastar. No depender de extensiones
experimentales de tareas del protocolo para instalaciones largas.

Tras una mutación MCP, publicar un evento por el canal de control para refrescar Inicio y menús con
los servicios existentes. Preservar el profile que está viendo el usuario. Esto
actualiza la UI del gestor, no el entorno de terminales ya abiertas. Lecturas durante
publicación deben recibir una vista consistente o INSTANCE_BUSY.

## Fases y criterios de aceptación

1. **Viabilidad y contrato.** Probar SDK en runtime privado, elegir revisión estable,
   transporte local sin autenticación, dependencias fijadas y schemas. Verificar conexión HTTP
   desde los tres clientes, cierre y procesos Windows. Registrar versiones reales,
   límites de retención y resultados; resolver incompatibilidades antes de ampliar.
2. **Control local y diálogo.** Pestaña dedicada, ownership, canal de control,
   arranque/parada, estado, guía copiable y herramienta de estado. Probar inicio siempre
   disabled, cero servidor/workers MCP en reposo, puerto ocupado, segunda UI, fallo
   de lanzamiento/handshake y salida del gestor. Verificar cierre de pestaña MCP,
   cierre normal de ventana y terminación forzada de Windows Terminal: puerto liberado,
   servidor y árboles gestionados terminados, sin reactivación ni otras pestañas afectadas.
3. **Gestión de entornos.** Herramientas de profiles, catálogo, componentes,
   configuración y pocketools; planes, confirmación sin hash, deduplicación y eventos
   de refresco. Probar conflicto UI/CLI/MCP y conservación de datos/rutas estables.
4. **Trabajo en el entorno.** Comandos, jobs, salida, timeout, cancelación y Job Objects.
   Verificar argv/scripts con espacios y %, !, &, Unicode; salida abundante por ambos
   pipes, hijos/nietos, reconexión y cierre abrupto. Mantener entorno de cada profile.
5. **Validación integrada y documentación.** Flujo real por cliente: conectar, crear
   profile de prueba, instalar un componente con hash, activar, configurar una variable,
   crear un archivo, ejecutar y leer pruebas, arrancar/detener servidor y retirar el
   profile conservando workspace. Instancias temporales, sin modificar datos personales.

Regresiones adicionales: JSON inválido y tamaño excesivo, conexión sin credenciales,
Host/Origin indebidos, socket sólo loopback, canal de control separado del API público,
reintentos, caducidad de salida, cancelación pendiente y no duplicación al
reiniciar. Fixtures con dependencias sin hash y planes cambiados antes de aprobación.

Para cada fase de implementación ejecutar:

```powershell
core\tools\python\python.exe -B -I -m unittest discover -s core\tests -v
.\mito.cmd doctor
git diff --check
```

Verificar el empaquetador mediante pruebas temporales: bootstrap con dependencias
fijadas, arranque disabled y ausencia de jobs/datos. No crear releases,
commits ni pushes. Registrar en CONTINUITY resultados observados y límites pendientes;
no declarar compatible un cliente sin prueba real.

## Archivos previstos y límites de esta planificación

Módulos propuestos: `mcp_server.py` (protocolo), `mcp_service.py` (ciclo de vida y acceso),
`mcp_tools.py` (schemas y adaptación), `jobs.py` (ejecución genérica) y `mcp_ui.py`
(diálogo). Integraciones en ui/configuration/locales, terminal, CLI, bootstrap y doctor; cambios
genéricos en Engine/storage sólo donde lo exijan los contratos de concurrencia.
Pruebas bajo core/tests. Nombres sujetos al resultado de la fase de viabilidad.

Implementación realizada en feature/mcp-server. Este documento conserva el plan
acordado; el contrato implementado está en ARCHITECTURE y USAGE, y las pruebas
observadas y los límites pendientes en CONTINUITY. Los criterios anteriores no
implican por sí mismos que cada cliente o escenario se haya verificado.
