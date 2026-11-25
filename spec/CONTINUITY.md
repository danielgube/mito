# Estado al 23 de septiembre de 2026

## Raíz Inno {code_*} en extract_dir (25 de noviembre)

- extras/antigravity-ide 2.7.0 (innosetup, `extract_dir: "{code_GetDestDir}"`) falló a
  otro usuario con «extract_dir no encontrado: {code_GetDestDir}». MITO extraía siempre
  {app} y aplicaba extract_dir como subcarpeta; Scoop lo pasa a innounp como `-c`.
- `inno.inno_root` acepta `{app}` y `{code_<Func>}` exactos; el motor y
  Expand-InnoArchive extraen esa raíz y no la tratan como subcarpeta. Constantes del
  sistema, subrutas y `{code:Func}` siguen rechazadas.
- No verificado con el instalador real: se asume, como Scoop, que innounp lista esos
  archivos como `{code_GetDestDir}\…` y que `-c` elimina el prefijo. post_install
  (copia de datos de usuario y extensiones) sigue omitido.
- Regresiones en test_inno.py (`-k scripted_destination -k declared_inno_root
  -k hook_helper`), no ejecutadas.

## Raíz corta sin rutas largas de Windows (25 de noviembre)

- Otro usuario, con MITO en `C:\Users\<usuario>\Downloads\mito-0.3.1-windows-x64`,
  falló al copiar el árbol expandido de antigravity
  (`…\app.asar.unpacked\node_modules\chrome-devtools-mcp\build\src\third_party\issue-descriptions`).
  7-Zip extrajo; `shutil.copytree` no puede pasar de 260 caracteres sin LongPathsEnabled.
  Sólo se vio el final del error; la causa por longitud no está confirmada con el log.
- Nuevo `path_limit.py` y `Get-YitoRootPathProblem` en bootstrap.ps1: si
  LongPathsEnabled no vale 1 y la raíz supera 32 caracteres, `ui`, `start`, `bootstrap` y
  el bootstrap PowerShell (salvo `-Approved`) se detienen, piden mover la carpeta (por
  ejemplo a C:\mito) y esperan Intro. El ZIP extraído en C:\ (25) pasa; Descargas no.
  Las apps, shims y MCP ya instalados no se bloquean.
- Límite: una raíz corta no garantiza árboles arbitrariamente profundos; no se usa `\\?\`
  ni se da todavía un error específico al superar 260 en una instalación.
- Regresión `core\tests\test_path_limit.py`, no ejecutada.
- La release se detuvo: test_diagnostics ejecuta bootstrap.ps1 en carpetas temporales
  (`home\tmp\tmpXXXXXXXX`, 33 caracteres) y recibía el código 3 de la comprobación.
  `MITO_ROOT_PATH_LIMIT` (Python y PowerShell) sólo puede subir el umbral; esas pruebas
  lo fijan en 1000. En la misma ejecución falló
  test_application_buttons_only_visible_for_active_launchable_app (0 botones y
  update-checks.lock bloqueado al limpiar): su worker de arranque consultaba los
  catálogos reales por red. Ahora se simula `refresh_installed` y se esperan los
  workers antes de comprobar. No verificado.

## Procesos residentes al actualizar MITO (25 de noviembre)

- Caso real: al actualizar una 0.2.17, el CMD esperaba indefinidamente a
  figma_agent.exe. Figma, instalado con LOCALAPPDATA en el home, dejó su agente en
  home\AppData\Local\FigmaAgent y lo registró en HKCU\...\Run, así que Windows lo
  arranca al iniciar sesión aunque MITO esté cerrado.
- El CMD muestra ahora la ruta relativa de cada proceso y acepta C para cerrarlos a
  la fuerza (Stop-Process -Force sólo de PIDs que siguen bajo la raíz). Intro sigue
  siendo volver a comprobar; no se cierra nada sin esa respuesta.
- Antes de pedir Intro informa de los valores Run/RunOnce (HKCU, HKLM, WOW6432Node)
  que lanzan binarios de la instancia. No los edita. No cubre la carpeta Inicio,
  tareas programadas ni servicios.
- Límite: el CMD ejecuta el actualizador de la versión instalada, así que la mejora
  sólo aplica al actualizar desde una instancia que ya la contenga (0.3.3 o posterior).
- Regresiones en test_self_update.py (`-k wait_for_closed -k autostart_entries
  -k real_stop_processes`), no ejecutadas. No verificado en una actualización real.

## Helpers Expand-* por tubería (25 de noviembre)

- extras/antigravity 2.35.1 falló a otro usuario (sin log local). Su installer.script
  hace `Get-Item "$dir\`$PLUGINSDIR\app*.7z" | Expand-7zipArchive -DestinationPath "$dir"`.
  Reproducido en Windows PowerShell 5.1 con la firma anterior: «faltan uno o varios
  de los parámetros obligatorios: Path», porque Path no aceptaba tubería.
- Expand-7zipArchive, Expand-InnoArchive, Expand-DarkArchive y Expand-MsiArchive
  aceptan Path por valor y por propiedad (alias FullName), con bloque process para
  varios archivos; DestinationPath por defecto se calcula por archivo. Lógica común
  en Invoke-MitoExpand. Helper genérico, sin excepciones por componente.
- post_install de antigravity (desactivar app-update.yml) sigue omitido: su
  actualizador integrado queda activo. Regresión `-k test_installer_script_pipes_archives`
  pasada por el usuario. Pendiente instalación real.
- `mito release` v0.3.2 se detuvo sin publicar: test_installer_runner_exposes_generic_inno_helpers
  buscaba el literal anterior (ajustado a Invoke-MitoExpand) y
  test_clone_materializes_blobs_without_checkout_and_retains_git falló con WinError 5
  al renombrar el clon recién creado en home/tmp. El clon de pocketools reintenta
  ahora el rename hasta 10 s ante PermissionError (antivirus/EDR), como move_whole.
  No verificado que esa fuera la causa; pendiente repetir la release.

## Descargas reanudables, progreso y cancelación de instalaciones (25 de noviembre)

- Caso real: extras/antigravity por VPN inestable; la descarga se detenía minutos y
  cada reintento empezaba de cero, sin progreso visible ni forma de cancelar.
- transfer.download conserva lo recibido tras un corte y reanuda con
  `Range: bytes=N-` cuando el resultado sigue siendo verificable: ETag fuerte o
  Last-Modified como If-Range, o hash declarado. Sin validador ni hash empieza de
  cero. 200 ante Range, 416 o un 206 que no continúa el archivo reinician desde
  cero; un hash distinto tras reanudar repite una descarga completa antes de fallar.
  Content-Length incompleto se trata como corte. Un intento que recibe ≥1 MiB
  restaura el presupuesto de reintentos y la espera.
- operation.py: Operation por hilo (contextvar) con fase, bytes/archivos, aviso y
  cancelación cooperativa. Sin Operation todo es no-op (CLI, bootstrap, MCP).
  La red de cada intento va en un hilo daemon que pasa bloques por una cola; el
  hilo del motor sólo espera la cola y comprueba la cancelación cada 0,25 s, así
  que no espera al timeout de 60 s. En Windows shutdown() no despierta un recv
  bloqueado (comprobado); el hilo abandonado termina con el siguiente dato o el timeout. El motor comprueba la cancelación tras descargar, extraer, mover (cada
  50 archivos en la copia por archivo), recursos, pre_install e installer, y por
  última vez en publish_plan antes de sustituir rutas estables; desde ahí la fase
  PUBLICANDO no es cancelable. Extracción y hooks en curso terminan antes de cancelar.
- UI: OperationDialog sustituye a LongStepDialog durante instalaciones (este queda
  para LongStep sin Operation). Aparece a los 2 s con barra, bytes, velocidad,
  aviso de reintento/reanudación, tiempo y «Cancelar instalación». Cancelar deja
  «Instalación cancelada. No se ha publicado nada.» sin enviar error tracking.
- Límites: MCP no conecta todavía su cancelación de jobs con Operation; la caché
  parcial no sobrevive a cerrar MITO (vive en el staging temporal de la instalación).
- Regresiones añadidas sin ejecutar: reanudación con ETag, con hash, reinicio sin
  ambos y servidor que ignora Range; cancelación de lectura bloqueada; diálogo con
  bytes y cancelación. Validación pendiente del usuario:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -k test_interrupted_download -k test_cancellation_interrupts -k test_install_progress_dialog -k test_long_engine_step -k test_bundles_cover -v`
  y una instalación real con red inestable.

## Correcciones de pruebas tras la suite de 357 casos (28 de septiembre)

- El usuario reporta dos fallos y un error; la release se detuvo sin publicar.
- La prueba de identidad comparaba C:/Yito con c:/mito como si fueran la misma
  raíz. Ahora comprueba igualdad entre C:/MITO y c:/mito y diferencia con C:/Yito.
- La prueba MCP publica child.pid y ready.json mediante renombrado del temporal
  cerrado, evitando leer archivos todavía vacíos. Conserva la comprobación de
  muerte del servidor y descendiente, con mensajes explícitos si faltan marcadores.
- La prueba del editor espera al montaje de Textual tras ambas aperturas antes
  de leer o cambiar Select; el widget inicializa su selección durante Mount.
- Sólo se modifican pruebas; no se cambia el comportamiento de la aplicación.
  No se han ejecutado tests ni doctor. Validación pendiente del usuario:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -k test_owner_crash -k test_taskbar_identity -k test_global_configuration_editor -v`.
- El ResourceWarning del extracto no incluye origen suficiente para atribuirlo;
  no se considera corregido por estos cambios.

## Icono Pocketools: tuerca en primer plano (27 de septiembre)

- El usuario aportó una composición con el octágono MITO sobre una tuerca y
  pidió la inversa: la tuerca delante. Nuevo maestro Pocketools generado con
  image_gen usando como referencias el propio `mito_icon.png` (para que el
  octágono no derive) y la composición aportada, invirtiendo el orden de
  capas: octágono detrás, tuerca-engranaje naranja plana con contorno verde
  oscuro completa y en primer plano, tapando el tercio inferior.
- El maestro llega con paleta final y transparencia real; build_branding.py
  deriva ambos iconos ya sin desplazamientos de color (MITO margen 2 px,
  Pocketools margen 8 px) y shift_cyan_to_blue queda disponible para futuros
  retoques. La zona inferior derecha de la tuerca es naranja plana y conserva
  la legibilidad de la etiqueta superpuesta de los accesos.
- Regenerados pocketools.ico (nueve tamaños, lectura verificada con Pillow),
  maestro y resto de variantes MITO sin cambios de concepto. Caché de iconos
  del Explorador refrescada. Sin ejecutar tests ni la aplicación; validación
  visual pendiente del usuario, en especial un acceso de Pocketools con
  etiqueta compuesta.

## Nuevo icono principal MITO aportado por el usuario (27 de septiembre)

- El usuario aportó el render definitivo del icono MITO: insignia octogonal de
  neón con borde verde, trazas de circuito cian-azuladas y chevron «>» naranja
  en relieve. Sustituye al hexágono generado por IA; Pocketools conserva su
  hexágono con engranaje naranja sin cambios de concepto.
- El PNG venía con fondo claro opaco: se eliminó por flood fill desde el marco
  sobre la máscara «claro y desaturado», con erosión de 2 px contra la franja
  anti-alias. El contenido sólido se escaló hasta dejar 2 px de margen: el
  octágono ocupa 968 × 1020 px del lienzo de 1024, prácticamente el 100 %.
- `build_branding.py` distingue ahora ambos maestros: MITO se deriva sin
  desplazamiento de color y con margen 2; Pocketools mantiene el azul
  eléctrico por desplazamiento de tono y margen 12. Regenerados ICO (nueve
  tamaños, lectura verificada con Pillow), preview, JPG y banner del README.
  Caché de iconos del Explorador refrescada.
- Sin ejecutar tests ni la aplicación; validación visual pendiente del
  usuario en barra de tareas, accesos regenerados y banner del README.

## Iconos ampliados y conexiones en azul eléctrico (27 de septiembre)

- A petición del usuario: el contenido sólido de ambos iconos se amplía hasta
  dejar sólo 12 px de margen en el lienzo (antes ~40 px sobre el halo), y
  algunas conexiones interiores pasan de cian a azul eléctrico (~216°),
  recuperando el acento de los ojos de la cotorra original. El borde del
  hexágono permanece verde: el desplazamiento de tono (banda 155–182°, +48°)
  se limita al interior erosionando la máscara sólida ~80 px con Pillow.
- La derivación desde los maestros queda versionada y es reproducible en
  `core/assets/branding/build_branding.py`: azul, limpieza de alfa, escalado,
  ICO de nueve tamaños, preview y variantes aplanadas. La carpeta de trabajo
  `.scratch` se había vaciado entre sesiones; ya no se depende de ella.
- Lectura de ambos ICO verificada con Pillow (nueve tamaños) y caché de
  iconos del Explorador refrescada. Sin ejecutar tests ni la aplicación;
  validación visual pendiente del usuario en barra de tareas, accesos
  regenerados y banner del README.

## Acento naranja en los símbolos centrales (26 de septiembre)

- A petición del usuario, el chevron «>» de mito.ico y el engranaje de
  pocketools.ico pasan a naranja cálido con halo ámbar; el hexágono y las
  trazas conservan exactamente los mismos verdes. El naranja recupera el
  acento del pico de la cotorra original y armoniza con la etiqueta naranja
  por defecto de los accesos. El usuario confirmó que el cambio anterior no se
  veía por la caché de iconos de Windows, no por los archivos.
- Regenerados ambos maestros con image_gen usando los iconos verdes como
  referencia de composición, y reconstruidas todas las variantes con el mismo
  proceso Pillow (limpieza de alfa, recorte, ICO de nueve tamaños, preview,
  JPG y banner). Lectura de ambos ICO verificada con Pillow.
- El acento naranja mejora la lectura a 24–32 px: punto cálido reconocible
  sobre el verde. Sin ejecutar tests ni la aplicación; validación visual
  pendiente del usuario (refrescar la caché de iconos y revisar barra de
  tareas, accesos regenerados y banner del README).

## Nueva identidad de iconos: hexágono de circuitos en verde (26 de septiembre)

- El usuario aportó una referencia (insignia hexagonal de neón con trazas de
  circuito y un símbolo «>» luminoso) y pidió sustituir la cotorra de la etapa
  YITO y el icono de Pocketools por esa familia en tonos más verdosos, en todas
  las variantes de core/assets/branding.
- Nuevo maestro MITO: hexágono redondeado de neón, interior verde muy oscuro
  con trazas de circuito y un chevron de terminal «>» centrado. Pocketools
  conserva identidad propia con un engranaje de ocho dientes en el centro de la
  misma insignia. Ambos generados con image_gen integrado a partir de la
  referencia, con fondo transparente real, limpieza de alfa y recorte al
  contenido con Pillow. Maestros RGBA de 1024×1024.
- mito.ico y pocketools.ico se derivan en los nueve tamaños habituales (16–256,
  PNG RGBA) y se verificó su lectura con Pillow. El chevron se reconoce hasta
  20–24 px; a 16 px queda una insignia verde con centro luminoso. Se conservan
  mito-original.ico y la historia git de la cotorra.
- Las variantes aplanadas se recomponen con Pillow sobre degradado radial verde
  oscuro: mito_ico.jpg, mito_logo.jpg, mito_shortcut.jpg y el banner
  mito_logo.png del README, que pasa de «YITO / Your Integrated…» a
  «MITO / My Integrated Terminal Orchestrator».
- Sin ejecutar tests ni la aplicación. Validación pendiente del usuario: abrir
  `mito.cmd` y comprobar el icono de la ventana y la barra de tareas (la
  sincronización con el Windows Terminal privado y YitoTerminal.exe se
  regenera por hashes del ICO), generar un acceso de componente y uno de
  Pocketools para ver la composición con la etiqueta inferior derecha, y
  revisar el banner del README.

## Regresiones reportadas, diálogo Pocketools y workspace (24 de septiembre)

- El usuario ejecutó 354 pruebas: tres fallos y un error; release se detuvo. Corregidos
  los fixtures de autoría resolviendo la raíz temporal (Windows puede devolver CHIFRI~1)
  y creando .git/hooks explícitamente, ya que init --template= no lo crea.
  En la prueba de Configuración se vuelve a mostrar la fila antigua antes de pulsar
  Eliminar, se comprueba el click y su desaparición antes de guardar. La lista se había
  desplazado al añadir las filas nuevas; no se cambia la semántica de eliminación.
- Diálogo Pocketools: margen bajo Crear paquete, líneas y padding entre paquetes.
  Comandos: Abrir carpeta junto a la ruta, margen antes del primer selector, separación
  del selector de profile y sus acciones, Nuevo comando azul.
- Añadida MITO_WORKSPACE al entorno real: ruta absoluta del workspace configurado para
  el profile (no se presupone que tenga su mismo nombre). context_variables centraliza
  las rutas gestionadas y permite leerlas sin activar componentes ni crear carpetas.
- Las plantillas nuevas incluyen comentarios con los valores de referencia del profile
  elegido: MITO_PROFILE, MITO_ROOT, MITO_WORKSPACE, HOME, USERPROFILE, AppData y temporales.
  No son asignaciones: se usa el entorno del profile al ejecutar. Sin profiles, se
  muestran marcadores para profile/workspace. Se convierte sólo la plantilla intacta
  al cambiar CMD/PowerShell; no se modifica código del usuario ni scripts existentes.
  No se copian variables personalizadas, credenciales ni el PATH del host a los scripts.
- Añadidas regresiones de workspace distinto al profile, cambio de profile heredado,
  traslado de instancia, lectura sin activación y conservación del texto editado.
  No se ejecutan tests, aplicación, doctor ni release. Validación pendiente del usuario:
  `mito.cmd` → Pocketools → Comandos → seleccionar profile → Nuevo comando y comprobar
  comentarios, espaciado y selector CMD/PowerShell. En una pestaña CMD nueva,
  `echo %MITO_WORKSPACE%` debe coincidir con el workspace del profile.
  Pruebas pendientes: patrones test_pocketool*.py, test_ui.py y test_engine.py con
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p "<patrón>" -v`.

## Identidad explícita de la ventana en la barra de tareas (24 de septiembre)

- El usuario confirma que cambiar el destino de los enlaces no resolvió el icono.
  Lectura del estado real: el acceso registrado ya usa pythonw; el marcador y los
  recursos externos coinciden con mito.ico. Inspección del PE como datos: las nueve
  imágenes de YitoTerminal.exe coinciden con mito.ico, no con pocketools.ico.
  No se encontraron enlaces anclados/implícitos con destino bajo C:\mito. En ese
  momento no había un proceso Terminal de MITO abierto; no se inspeccionó una ventana.
- Añadida identidad explícita por ventana e instancia mediante
  SHGetPropertyStoreForWindow: comando de relanzamiento del gestor, nombre MITO,
  mito.ico,0 y AppUserModel.ID derivado de la ruta. El ID se escribe después de las
  propiedades de relanzamiento, conforme a la documentación de Microsoft:
  https://learn.microsoft.com/en-us/windows/win32/properties/props-system-appusermodel-relaunchiconresource
- Se aplica al montar el gestor y arrancar shells/hijos owned, no mediante observadores
  o servicios. EnumWindows filtra por clase y ruta completa del ejecutable de esta
  instancia. No modifica ventanas del host, iconos de Pocketools, cachés globales ni
  enlaces anclados. terminal.identity registra cuántas ventanas encontró o el error.
- Añadidas regresiones de identidad estable/independiente por raíz, aislamiento de
  ventanas, ABI x64/propiedades COM y tratamiento no fatal de errores. Sin ejecutar
  tests ni abrir la aplicación. AST de cuatro archivos y git diff --check correctos.
  Pendiente validar el resultado visible: cerrar MITO,
  ejecutar `mito.cmd` y comprobar la barra, después abrir un acceso de Pocketools.
  Regresión concreta pendiente:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p "test_terminal_identity.py" -v`.
  La solución anterior queda documentada abajo como cambio insuficiente por sí solo.

## Icono de Pocketools limitado a sus accesos (24 de septiembre)

- El usuario informa que la barra de tareas muestra Pocketools como icono de MITO.
  Los recursos y perfiles del Terminal siguen usando mito.ico, pero los accesos
  Pocketools apuntaban directamente al mismo YitoTerminal.exe. Ese destino compartido
  permitía que Windows asociara el icono del .lnk a la ventana de MITO.
- Los nuevos enlaces usan pythonw privado → pocketool-run → Terminal → ejecución
  owned. El lanzador termina tras abrir la pestaña; no queda ningún proceso auxiliar
  persistente. Icono, badge, color, profile y argumentos se conservan en el .lnk.
- prepare actualiza enlaces antiguos registrados sólo si coinciden destino y
  argumentos generados. Cambia TargetPath/Arguments conservando IconLocation y demás
  propiedades. No modifica enlaces personalizados, ausentes ni no registrados.
  Fallos quedan en log sin impedir abrir MITO; el registro marca launcher=pythonw
  sólo después de guardar el enlace. Una repetición tras fallo del registro es segura.
- Añadidas regresiones de separación del launcher, ejecución owned, iconos del
  Terminal, migración única y conservación de propiedades de enlaces Windows.
  AST de los seis archivos Python afectados y git diff --check correctos.
  No se ha ejecutado aplicación ni pruebas. Pendiente comprobar con todas las ventanas
  de MITO cerradas y `mito.cmd`, y después con el acceso de una Pocketool: ventana y
  barra de tareas MITO, icono de engranaje exclusivamente en el acceso.
  Los enlaces anclados o copiados fuera del registro pueden requerir volver a anclarse
  desde el acceso corregido. No se borra la caché de iconos ni se reinicia Explorer.

## Corrección del destino y selección de Pocketools (24 de septiembre)

- El usuario detectó el rechazo de C:\mito\pocketools\ceniza y un cierre al pulsar
  Acceso directo sin comando. El log identifica NoSelection recibido por Path.
- Crear paquete ahora sólo crea pocketools/<nombre> bajo la instancia: nombre,
  descripción y Git opcional, con destino visible. Eliminados ruta libre, modos de
  carpeta existente/clonado y Examinar del formulario de creación. Se puede reutilizar
  una carpeta vacía; un paquete registrado se gestiona desde Comandos. No se sustituyen
  carpetas no registradas que ya contengan archivos.
- La configuración nueva guarda pocketools/<nombre> relativo a MITO. Su nombre debe
  coincidir con la propiedad; no se admiten otras rutas relativas. Guardar escribe en
  ese árbol directamente, preservando .git y auxiliares. Update valida in situ sin
  copiarlo sobre sí mismo; la UI no muestra Actualizar para esos paquetes. Eliminado
  Aplicar cambios de Comandos. Las fuentes externas previas continúan siendo compatibles.
- Editar y Acceso directo nacen deshabilitados y siguen la selección tras cambios,
  trabajos asíncronos y recomposición. Acceso directo requiere también profile.
  Los handlers y script_path comprueban el tipo del valor, sin depender de la identidad
  del sentinel de Textual. NoSelection se convierte en error de usuario, no TypeError.
- Comandos → Editar copia local conserva los archivos de paquetes consumidos por URL
  y adjunta su historial Git descargado antes de registrar el destino portable.
  Desde entonces las actualizaciones son de Git, sin pull automático. Si falla el
  guardado de configuración tras adjuntar .git, puede reintentarse; Actualizar se
  niega a reemplazar cualquier destino que contenga .git. No se cambian archivos de
  configuración ni paquetes reales del usuario durante este desarrollo.
- Los CMD derivados de PS1 viven también en el árbol local. Se ocultan del selector
  sólo si su contenido coincide exactamente con el generado. Renombrados y cambios
  de tipo limpian únicamente wrappers huérfanos intactos, conservando los personalizados.
- Añadidas regresiones para destino, portabilidad, conservación de Git/auxiliares,
  reutilización de carpeta vacía, conversión remota, selección vacía y wrappers locales.
  Pendiente de ejecución por el usuario; no se ha lanzado la aplicación, tests ni doctor.
- Comprobaciones estáticas: AST de 61 archivos, coherencia de claves y parámetros en
  los 24 bundles, y git diff --check sin errores de whitespace. No validan el flujo
  en ejecución. No se ha hecho commit, push ni release.
- Validación manual pendiente: `mito.cmd` → Pocketools → Crear paquete → nombre
  `ceniza` → comprobar destino → Crear carpeta. Comprobar Editar/Acceso directo sin
  selección, guardar un CMD y un PS1, seleccionar y quitar la selección, y crear un
  acceso con profile. Regresiones específicas pendientes:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p "test_pocketool*.py" -v`.

## Autoría inicial de Pocketools (24 de septiembre; destino y flujo corregidos arriba)

- Implementadas las pantallas Crear paquete, Comandos, editor CMD/PowerShell y
  revisión de Git. Permiten usar carpetas/repositorios existentes, crear carpetas,
  inicializar Git y clonar URL HTTP/HTTPS en una carpeta elegida. Los repos remotos
  deben existir. Se conserva pocketool.json existente o se crea con la descripción.
- Los orígenes editables son locales, fuera de las copias publicadas y directorios
  internos. Guardar escribe UTF-8/CRLF en el origen y vuelve a publicar su snapshot;
  no ejecuta scripts. Renombrado/cambio de tipo explícito, rechazo de colisiones y
  nombres reservados, huella contra modificaciones externas y confirmación al salir
  con cambios sin guardar. Abrir carpeta usa Explorador; Aplicar cambios incorpora
  auxiliares externos. No hay watcher, dependencias ni PATH global.
- Git revisa archivos y rama antes de actuar; commit --only incluye únicamente los
  paths elegidos. Los cambios ajenos preparados permanecen en el índice. Identidad
  y credenciales deben estar disponibles en Git. Sin hooks, filtros, fsmonitor,
  submódulos, LFS, force push, pull, cambio de rama ni creación de repos en servidor.
  Push separado/reintentable; fallar no descarta archivos ni commits. Un commit
  fallido puede dejar los archivos seleccionados preparados. El push incluye todos
  los commits pendientes de la rama, indicado en el diálogo.
- Los clones de autoría materializan blobs regulares sin checkout y conservan .git;
  límites: 10000 archivos, 16 MiB por blob, 64 MiB de árbol. El historial descargado
  por Git no tiene límite de tamaño propio. La publicación de orígenes locales
  conserva las reglas existentes, incluido copiar archivos ignorados salvo .git.
  Editar un paquete remoto cambia su fuente de consumo a la carpeta elegida;
  Actualizar copia esa carpeta y no descarga commits nuevos. Un fallo al registrar
  o publicar puede dejar la carpeta creada; sus archivos nunca se eliminan como
  recuperación. Puede retomarse como carpeta existente.
- Icono aprobado incorporado como pocketools_icon.png y pocketools.ico, con alfa
  real y nueve tamaños. La distribución incluye únicamente el ICO. El actualizador
  admite este recurso opcional y conserva compatibilidad con payloads anteriores
  que no lo contengan. Actualizadores antiguos cuyo allowlist sólo admite mito.ico
  requieren actualización manual para aceptar una distribución con el nuevo ICO.
- Accesos con profile, paquete, comando, etiqueta/color y argumentos. Abren una
  pestaña del Terminal de esta instancia, activan el profile/workspace, resuelven
  la copia instalada al ejecutar y esperan Intro al terminar. Sin versiones fijadas.
  JSON/base64 evita que los argumentos se interpreten como acciones de Terminal;
  CMD conserva los límites de batch_command para comillas/saltos de línea. El
  compositor original añade el distintivo al icono nuevo. Registros separados en
  core/state/pocketool-shortcuts. Renombrados, profiles eliminados o una instancia
  movida requieren regenerar los enlaces. Se derivan CMD para PS1 en la copia,
  sin sobreescribir comandos homónimos ni añadir archivos al repositorio de origen.
- El editor limita scripts a 1 MiB UTF-8; PS1 lleva BOM, la plantilla CMD usa
  chcp 65001. No convierte lenguajes ni codificaciones antiguas. Textos nuevos
  disponibles en español e inglés; los otros 22 bundles conservan sus traducciones
  previas y usan inglés para los mensajes nuevos de esta funcionalidad.
- Añadidas regresiones de autoría, publicación, conflicto externo, colisiones,
  enlaces, selección Git e índice ajeno, ausencia de hooks/filtros, push fallido y
  reintento, clones sin checkout, contexto de ejecución, argumentos y flujo de UI.
  Extendidas las regresiones de distribución/actualización para el icono nuevo.
  No se han ejecutado pruebas, aplicación ni doctor; no se ha hecho commit, push
  ni release. La comprobación de sintaxis es estática y no sustituye la validación.
- Comprobaciones estáticas realizadas: AST de 15 archivos implicados sin errores,
  claves y parámetros coherentes en los 24 bundles, nueve tamaños presentes en el
  ICO y `git diff --check` sin errores de whitespace. No implican validación funcional.
- Validación pendiente del usuario: `mito.cmd` → Pocketools → Crear paquete →
  carpeta nueva → Nuevo comando → Guardar → nueva pestaña CMD/PowerShell; crear
  además un acceso con etiqueta, color y parámetros y comprobar profile y workspace.
  Regresiones concretas:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p "test_pocketool*.py" -v`.
  Tras ellas, comprobar `test_i18n.py`, `test_release.py` y `test_self_update.py` con
  el mismo comando de descubrimiento cambiando el patrón. Todo pendiente.

## Pestaña predeterminada configurable (24 de septiembre)

- El usuario confirma que el comportamiento funciona correctamente. Ajustado después
  el orden para dejar MITO al final del menú cuando la opción predeterminada es CMD
  o PowerShell. Si se elige expresamente mito, permanece primero para conservar
  la coincidencia entre «+» y Ctrl+Shift+1. Ajustadas las expectativas de regresión,
  sin ejecutar pruebas. Pendiente comprobar este orden con `mito.cmd` y desplegar
  el menú de pestañas.

- «+» y la primera entrada del menú (Ctrl+Shift+1) abren CMD del profile seleccionado
  en el gestor. `terminal.defaultShell=cmd|powershell|mito` está disponible en
  Configuración global y en los defaults de distribución; ausente o inválido usa cmd.
- La selección se conserva en core/state/terminal-profile.json entre regeneraciones;
  al borrar el profile se usa el primero disponible, y sin profiles se abre MITO.
  Se aplica al montar el gestor, cambiar profile y guardar Configuración global.
  El arranque explícito del gestor conserva su pestaña MITO. Entre varios gestores
  prevalece la última selección; el foco de pestañas no modifica la selección.
- Añadidas regresiones de generación, selección, borrado, modos y arranque explícito.
  No se han ejecutado pruebas ni doctor. Pendiente validación del usuario con
  `mito.cmd`, cambiar profile y probar «+», Ctrl+Shift+1 y Configuración.
  Prueba concreta: `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_terminal.py -v`.

## Expectativas de UI tras la suite de 312 pruebas (23 de septiembre)

- El usuario comunica 312 pruebas ejecutadas, con dos fallos de expectativas
  de UI; la release no se publica. La prueba de checkboxes todavía esperaba los
  colores antiguos y la del sidebar no incluía Buscar actualizaciones.
- Actualizado test_ui.py a los colores actuales #8A4B50/#69FF64 y al botón
  check-updates entre home-button y self-update. Sin cambios en la interfaz.
- No se han ejecutado pruebas ni reintentado la publicación. Pendiente repetir:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_ui.py -v`.

## Destinos repetidos en bundles Burn (23 de septiembre)

- Ante «duplicate or empty payload path», se descargó el EXE declarado por
  main/python 3.14.7 verificando su hash y se leyó únicamente el CAB UX para
  diagnosticar su manifiesto. No se ejecutó el instalador, hooks ni pruebas.
- Evidencia: core.msi y otros MSI tienen entradas AllUsers/JustForMe con distintos
  SourcePath, pero mismo FilePath, tamaño y hash; py.exe tiene seis entradas.
  El lector rechazaba de antemano cualquier destino repetido.
- Corrección genérica: conservar todas las fuentes en el inventario, validar cada
  archivo y consolidar destinos repetidos sólo tras comparar los bytes extraídos.
  Distintos contenidos abortan antes de copiar, indicando el destino. No se elige
  una variante por condición, orden ni nombre de producto. Fuentes duplicadas y
  colisiones archivo/directorio siguen rechazadas.
- Regresiones añadidas para igualdad, diferencias con hashes válidos o ausentes,
  mayúsculas/minúsculas, fuentes duplicadas y colisiones. No ejecutadas.
  Pendiente: `mito.cmd ui` → instalar main/python. Prueba específica:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_burn.py -v`.

## Corrección de referencias externas en bundles Burn (23 de septiembre)

- El usuario confirma que main/python supera pre_install y llega a installer.script;
  el lector Burn abortaba ante cualquier Payload con Packaging="external".
  Era una restricción excesiva: el lector de WiX sólo incluye los embedded en el
  inventario del contenedor adjunto, aunque el manifiesto también declare externos.
- Corregido de forma genérica: se omiten referencias externas de Payload raíz,
  se registra su cantidad y se extraen/verifican las incrustadas. No se resuelven
  rutas externas, no se descargan archivos adicionales ni se ejecuta el bundle.
  Packaging desconocido, UX externo y archivos incrustados ausentes siguen dando
  error. Si installer.script exige uno de los externos omitidos, seguirá fallando.
- Regresiones adaptadas para manifiestos mixtos, aviso, ausencia de descargas y
  conservación del rechazo de inventarios/hashes incorrectos. No se han ejecutado
  tests ni repetido la instalación. Validación pendiente: `mito.cmd ui` → instalar
  main/python; prueba concreta:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_burn.py -v`.

## Recursos de buckets y extracción Burn/MSI en hooks (23 de septiembre)

- Implementado el acceso genérico a `$bucketsdir/<bucket>/scripts/` y la variante
  con `$bucket`: copia privada por instalación, compartida entre pre_install e
  installer. Usa el origen configurado o registrado; soporta carpetas locales y
  ZIP de repositorios GitHub, con validación de rutas/enlaces y límites.
- Las descargas de recursos sin hash entran en la confirmación previa del plan,
  incluidas dependencias. Cancelar no descarga nada. El plan vuelve a comprobar
  los orígenes antes de publicar. No modifica Scoop ni los índices de manifiestos.
- Expand-DarkArchive lee PE/.wixburn y CAB mediante Python/7-Zip privados, nunca
  ejecuta setup.exe. Admite Burn v2 de WiX 3 con UX y un contenedor adjunto, firma
  original del engine, inventario, rutas, tamaños y hashes internos. Rechaza otros
  formatos y CAB encadenados; referencias externas se omiten con aviso (corrección
  posterior descrita arriba). Expand-MsiArchive reutiliza msi.py.
- Regresiones añadidas en test_bucket_resources.py y test_burn.py: recursos
  locales/remotos, referencias cruzadas, confirmación de dependencias, rollback,
  ambas fases PowerShell, CAB reales con makecab/7-Zip, rangos de bundles firmados,
  traversal, XML no permitido, inventarios y hashes incorrectos, confinamiento MSI.
- Límites: sólo referencias explícitas a scripts/ en el manifiesto; no se descubren
  referencias transitivas o dentro de installer.file. Referencias dinámicas visibles
  se clasifican no soportadas. Los recursos reflejan el origen al instalar, sin pin
  al commit del manifiesto ni caché offline. No se garantiza pip: post_install sigue
  omitido. Los .reg generados con $dir pueden contener staging; no importarlos como
  registro de la ruta estable. No se reescriben archivos de aplicaciones por nombre.
- Comprobaciones estáticas: sintaxis de los siete archivos Python implicados
  revisada con ast.parse y `git diff --check` sin errores de whitespace.
- Validación pendiente: no se han ejecutado pruebas ni instalado Python. Comandos:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_bucket_resources.py -v`
  y `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_burn.py -v`.
  Prueba funcional: `mito.cmd ui`, catálogo → main/python → Instalar, aceptar la
  confirmación de recursos sin hash si se desea continuar, activar en un profile
  y abrir una pestaña de ese profile para ejecutar `python --version`.

## Diagnóstico de main/python 3.14.7 (23 de septiembre)

- El manifiesto del snapshot local lee plantillas PEP 514 mediante
  `$bucketsdir/main/scripts/python/`. El runner no define `$bucketsdir` y el
  catálogo conserva sólo JSON de manifiestos, no los recursos del repositorio.
  La variable vacía produce una ruta desde la raíz de la unidad actual, coherente
  con el error comunicado `D:\main\scripts\python\install-pep-514.reg`.
- Resolver esa ruta no basta: installer.script requiere Expand-DarkArchive y
  Expand-MsiArchive, ausentes del runner. Existe extractor MSI propio, pero no
  está expuesto como helper. El EXE queda como setup.exe suelto hasta el hook;
  el mensaje «Extrayendo» no confirma que se haya extraído Python.
- La clasificación Compatible valida campos, no todas las dependencias del
  código PowerShell. Este caso revela una limitación de esa clasificación.
- Solución genérica pendiente: snapshots de recursos de buckets y contexto
  `$bucketsdir`, extracción validada de bundles WiX/Burn y helper MSI reutilizando
  el motor propio. Debe cubrir orígenes locales/remotos, consultas individuales y
  referencias entre buckets, sin depender de Scoop instalado ni excepciones por app.
- post_install ejecuta ensurepip en este manifiesto y MITO lo omite por contrato;
  resolver los pasos anteriores no garantiza pip inicializado. Además las plantillas
  .reg usan $dir temporal: no debe prometerse registro portable de la ruta final.
- Investigación por lectura de código y snapshot, sin ejecutar instalación ni
  pruebas. No se ha corregido todavía la instalación. Reproducción comunicada por
  el usuario: `mito.cmd ui`, catálogo → main/python → Instalar.

## Consulta al instalar y búsqueda manual de actualizaciones (23 de septiembre)

- ACCIONES incluye «Buscar actualizaciones»: repite la consulta de componentes
  instalados y MITO saltando la caché diaria, sin cerrar el gestor. Espera las
  consultas de arranque pendientes, bloquea acciones duplicadas y refresca Inicio.
  Conserva la prioridad de releases offline y permite que un servicio continúe
  aunque falle el otro; los errores se muestran en el log.
- Instalar desde catálogo consulta sólo bucket/app seleccionado, con el mismo
  lector de manifiestos individuales de Inicio. Si hay una versión posterior muestra
  versión anterior → nueva y los botones Cancelar e Instalar. Sin novedades continúa.
  Las confirmaciones de descargas sin hash se conservan, también para dependencias.
- Si falla la consulta no instala desde el snapshot antiguo; el motivo queda en el
  log y puede reintentarse. Versiones no comparables mantienen el criterio existente
  de Inicio. Un manifiesto nuevo incompatible es rechazado por el plan del motor.
  No se refrescan catálogos completos ni dependencias automáticamente.
- Regresiones añadidas para caché forzada, reintento tras fallo, consulta selectiva,
  botón manual y confirmación/cancelación al instalar. Adaptada la prueba de descargas
  sin hash con un origen local. Traducciones añadidas en los 24 idiomas.
- Validación interactiva pendiente: no se han ejecutado tests, suites ni doctor.
  Probar con `mito.cmd ui`: ACCIONES → Buscar actualizaciones y después Instalar
  un componente del catálogo cuyo snapshot tenga una versión anterior al origen.

## Estados de checkbox con color explícito (21 de septiembre)

- El símbolo de todos los checkboxes se muestra rojo claro cuando está desmarcado
  y verde MITO cuando está marcado. La etiqueta conserva su color legible y el
  bloqueo temporal del control durante operaciones no altera el estado representado.
- Regresión añadida sobre los selectores y colores de ambos estados. Validación
  visual pendiente; no se han ejecutado pruebas automáticamente.

## Correcciones tras la suite del usuario (21 de septiembre)

- Un callback retardado del indicador podía ejecutarse después de desmontar la
  pantalla durante una actualización de MITO y lanzar `NoMatches`. Ahora actualizar
  u ocultar el indicador tolera que la pantalla ya no exista.
- Los errores nuevos de Expand-InnoArchive reutilizan las claves traducidas del
  helper de archivo en los 24 idiomas, sustituyendo sólo el nombre del helper.

## Indicador retardado de operación en curso (21 de septiembre)

- Las acciones que bloquean controles muestran `⏳ …` en un
  espacio reservado de la barra lateral sólo si siguen activas después de dos segundos.
  Las operaciones rápidas no producen parpadeos ni cambios de disposición.
- El indicador usa el temporizador de la propia interfaz, sin procesos auxiliares,
  y se oculta al completar tanto operaciones normales como la actualización de MITO.
- Validación interactiva pendiente; no se han ejecutado pruebas automáticamente.

## Helper Inno genérico para scripts de instalación (21 de septiembre)

- Caso real: extras/gimp 3.2.6 llega como setup Inno sin `innosetup: true` y su
  `installer.script` llama a `Expand-InnoArchive`, `Get-HelperPath -Helper Innounp`
  e `Invoke-ExternalCommand`; el runner sólo exponía el helper 7-Zip y abortaba
  antes de preparar GIMP.
- El entorno PowerShell incorpora esos tres helpers de forma genérica. La expansión
  Inno usa el extractor privado y la validación de inventario de MITO, se limita a
  `{app}` y a rutas dentro del payload; `Get-HelperPath` sólo expone innounp y el
  comando externo comprueba el código de salida y conserva el log solicitado.
- Regresión añadida para fijar la disponibilidad del contrato del runner. No se han
  ejecutado pruebas ni instalado GIMP; queda pendiente la validación interactiva.

## Reintentos de descarga ante errores transitorios (18 de septiembre)

- Caso real: primer arranque de la release 0.2.4 abortando con «HTTP Error 504:
  Gateway Time-out» al descargar las herramientas core tras un proxy empresarial.
  El reintento sin verificación TLS no lo cubría: un 504 es HTTPError y el bootstrap
  moría en el primer intento.
- transfer.download ahora reintenta con espera exponencial (delay 2s, x2 por
  intento, 5 intentos por defecto; parámetros keyword-only attempts y delay):
  - HTTP 5xx, 408 y 429 (servidor, proxy o gateway transitorios).
  - URLError de red (DNS, conexión rechazada, etc.).
  - OSError y http.client.HTTPException durante la lectura (cortes, timeouts,
    lecturas incompletas).
  - Los 4xx restantes (404, 403, …) son permanentes y abortan sin reintentar.
  - El reintento sin verificar certificado TLS se conserva: se activa una vez por
    descarga y sigue imprimiendo su aviso.
  - Cada reintento imprime un aviso en la salida (español, sin i18n) que el
    bootstrap captura al log; los archivos parciales se descartan en cada fallo.
  - Los hash incorrectos y los límites de tamaño no se reintentan.
- Regresión en core/tests/test_transfer.py: 504 dos veces y éxito al tercer
  intento con hash correcto; 404 aborta sin reintentar; 504 permanente propaga el
  error agotados los intentos y descarta el archivo.
- Validación pendiente: comprobada sólo la sintaxis con py_compile.

## Reintento sin verificación TLS (18 de septiembre)

- transfer.download reintenta una vez sin verificar el certificado cuando la
  verificación SSL falla (SSLCertVerificationError), con o sin hash declarado, y
  se imprime un aviso en la salida, que el bootstrap captura al log. Con hash, la
  integridad queda garantizada por ese hash; sin hash, la descarga queda sin
  protección de integridad, aceptado deliberadamente: esas descargas ya exigen
  confirmación explícita del usuario y HTTP está permitido. Política elegida:
  MITO funciona en todos los escenarios aunque baje la seguridad del canal.
- Motivo: el CPython embebido valida con OpenSSL, que no descarga intermedios que
  falten (sin AIA chasing); equipos con inspección TLS corporativa o almacenes de
  certificados incompletos fallaban en el primer arranque aunque el navegador y
  PowerShell (Schannel) funcionaran. El bootstrap siempre fija SHA-256 en
  bootstrap.lock.json, así que el primer arranque queda cubierto por este reintento.
- Regresión en core/tests/test_transfer.py con servidor HTTPS local de
  certificado autofirmado (core/tests/fixtures/tls_cert.pem y tls_key.pem): éxito
  con hash correcto, éxito sin hash, y descarte con hash incorrecto tras el reintento.
- Validado: `core\tools\python\python.exe -B -I -m unittest discover -s core\tests
  -p test_transfer.py -v` — 4 pruebas correctas. El aviso apareció en los tres
  casos del reintento (con hash, sin hash y con hash incorrecto, este último
  descartado). Suite completa después: 288 pruebas correctas
  (`discover -s core\tests`), incluida la regresión de i18n tras retirar el tr()
  del aviso de log.
- Caso real que motivó el cambio: primer arranque fallando en
  files.pythonhosted.org con «unable to get local issuer certificate» en un
  equipo con Windows 10.0.19045; PowerShell había descargado Python sin problema.

## Test de bienvenida del bootstrap bajo -X utf8 (18 de septiembre)

- El release lanza la suite con `-X utf8` (publish.py), así que subprocess con
  text=True decodifica la salida capturada como UTF-8; PowerShell 5.1 escribe en
  la codepage legacy de la consola (texto español del aviso) y el hilo lector
  moría con UnicodeDecodeError, dejando stdout=None con returncode=0. El test
  test_bootstrap_welcome_requires_explicit_yes fijó ahora la salida del proceso
  hijo a UTF-8 ([Console]::OutputEncoding) y decodifica con encoding='utf-8'
  explícito, haciéndolo hermético a la codepage del equipo y a -X utf8.
- Detección: falló al ejecutar el release (las pasadas manuales sin -X utf8
  pasaban). Aislado con discover -p test_diagnostics.py y un spy sobre
  subprocess.run; los otros cuatro tests de PowerShell no asertan stdout y no
  se vieron afectados.
- Validado: `core\tools\python\python.exe -B -I -X utf8 -m unittest discover -s
  core\tests -p test_diagnostics.py` — 10 pruebas correctas. La release que lo
  detonó abortó antes de versionar/commit; no quedó estado pendiente.
- Pendiente: relanzar el release completo (suite entera con -X utf8).

## Bienvenida y confirmación del bootstrap

- Antes del primer bootstrap, PowerShell muestra el nombre completo de MITO,
  Python, las herramientas del lock (7-Zip, Windows Terminal e innounp), Textual,
  Pillow, SDK MCP y dependencias, e indica que requiere Internet y usa la carpeta
  de MITO. Sólo s/si/sí confirma. Intro, otra respuesta o entrada no disponible
  cancelan antes de crear tools, staging o descargar. Se conserva un log local
  de cancelación, sin reportarla a PostHog. Mientras no termine el bootstrap se
  vuelve a preguntar al abrir; con runtime preparado no se repite.
- El actualizador pasa `-Approved` después de su confirmación existente, para
  evitar un prompt invisible en su proceso de preparación. `-PreviewWelcome`
  permite revisar el mensaje sin modificar la instalación, descargar ni enviar.
- Pendiente comprobación del usuario:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File core\bootstrap.ps1 -PreviewWelcome`.
  Regresiones de aceptación/cancelación y ausencia de descargas añadidas sin ejecutar.

## Aviso de búsqueda sin componentes

- Con cero filas en la búsqueda, el resumen conserva el recuento y sustituye la
  explicación de orden por «Sin componentes. Pulsa el botón "Actualizar catálogos".
  Si no aparece ninguno comprueba tus fuentes en "Configuración"», en azul intenso
  y negrita. Aplica también si los filtros o la búsqueda dejan cero resultados.
  Con resultados se recupera el texto habitual. Altura adaptable para no cortar el
  aviso al estrechar la ventana; texto incorporado a los 24 idiomas.
- Pendiente revisión visual: `mito.cmd ui`, abrir Buscar e instalar, buscar un texto
  sin coincidencias y después borrarlo. No se han ejecutado pruebas ni la aplicación.

## Filtros compactos en búsqueda

- Checkboxes de buckets en una sola línea, sin borde vertical, con una línea
  de separación antes del resumen de componentes/disponibles. Contenedor de altura
  automática para conservar el desplazamiento horizontal cuando sea necesario.
- Comprobación visual pendiente del usuario: `mito.cmd ui`, abrir Buscar e instalar
  y revisar altura de filtros y separación del recuento. No se ha ejecutado la UI.

## Logs visibles en la raíz

- Ubicación principal cambiada a `logs/mito.log`, junto a mito.cmd; PowerShell,
  Python y pendientes PostHog usan `logs/`. Rotación y fallback temporal conservados.
  Carpeta excluida de Git y de las releases; validador de actualización adaptado.
- El usuario indica que los logs anteriores funcionan. Este cambio de ubicación
  queda pendiente de comprobación: `mito.cmd profile show posthog/check` debe
  registrar el error en `logs/mito.log`; `mito.cmd logs` debe mostrar la nueva ruta.
  No se han ejecutado pruebas ni la aplicación durante este cambio.
- Los logs anteriores de `core/state/logs` se conservan; no se trasladan mientras
  puede haber procesos de la versión anterior escribiéndolos.

## Log local desde bootstrap y recuperación de informes

- PowerShell y Python escriben JSON por línea en `logs/mito.log`, con
  1 MiB por archivo y tres copias rotadas. Lock de byte entre procesos, mensajes
  acotados y fallback a `%TEMP%/MITO-logs/<hash-de-raíz>` si no se puede escribir.
  `mito.cmd logs` muestra las ubicaciones. El log no depende de activar PostHog.
- Bootstrap registra Windows/build, arquitectura, versión de PowerShell/Python,
  fase y error; comienza antes de crear tools o descargar Python. Guarda la salida
  de preparación del runtime. `mito.cmd` usa startup.py para diagnosticar errores
  de importación de CLI y llama al diagnóstico PowerShell ante códigos nativos
  negativos o 9009. No se ha reproducido el problema del compañero en Windows 10;
  no se atribuye a ninguna causa sin un log de esa máquina.
- CLI, UI principal, errores fatales Textual, hilos sin manejar, jobs MCP,
  actualización aplicada desde su proceso externo, Pocketools, configuración y
  refresco de catálogos tienen captura. Instalación añade fases download-and-hash,
  extract, copy-payload, pre-install, installer y validate, con componente. Errores
  de autenticación sólo registran el tipo localmente para evitar credenciales.
- Los informes incluyen mensaje depurado y contexto; filtran patrones de secretos,
  queries de URL y rutas conocidas. No hay captura de variables locales ni código
  fuente. Es un filtro de patrones, no garantía sobre todo texto arbitrario. Se
  solicita omitir GeoIP. Para soporte se comparten los archivos mito.log y .1..3,
  revisados previamente; no es necesario enviar toda la carpeta de instalación.
- Cola persistente `logs/pending`: hasta 100 informes de 128 KiB por ubicación,
  siete días, descarte de los más antiguos al llenarse, UUID/fecha originales y
  huella del proyecto (sin guardar su clave). Hasta 20 trazas distintas y 20 intentos
  Python por proceso; reenvío al arrancar. PowerShell intenta hasta tres pendientes
  al finalizar/fallar bootstrap y se detiene si falla la red. No hay servicio/timer.
  Si la red falla, queda el informe y el motivo local. Puede repetirse un UUID por
  concurrencia o confirmación perdida; no se promete entrega exactamente una vez.
- Autorizado por el usuario: nuevas distribuciones con PostHog activo hacia el
  proyecto europeo 276586. `posthog.enabled=false` conserva sólo el log local y
  detiene envíos; pendientes retenidos pueden reenviarse al reactivar. La release
  sigue usando configuración pública fija, sin copiar configuración local, logs
  ni pendientes. Actualizaciones conservan opciones existentes. El validador permite
  logs sólo después de preparar un bootstrap, nunca dentro del ZIP distribuido.
- El usuario confirmó recepción y procesamiento del diagnóstico inicial en PostHog
  (evento de 2026-09-16 20:47:28 UTC, RuntimeError/check, versión 0.2.1). El problema
  inicial de envío fue resolución DNS/WARP. Esa evidencia corresponde a la versión
  anterior de captura, no valida todavía esta ampliación.
- Verificación realizada: análisis de sintaxis Python/PowerShell y revisión de diff.
  No se han ejecutado tests, bootstrap, aplicación, envíos ni publicaciones. Añadidas
  regresiones de rotación, escritores concurrentes, fallback, filtrado, cola offline,
  cambio de proyecto, límites/caducidad y PowerShell sin Python ni red. Pendientes:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_diagnostics.py -v`
  y el mismo comando con `-p test_error_tracking.py`, `-p test_cli.py`,
  `-p test_release.py` y `-p test_self_update.py`. Validación interactiva:
  `mito.cmd profile show posthog/check`, revisar `logs/mito.log` y PostHog;
  `mito.cmd posthog-check` muestra estado de envío. El compañero necesita una copia
  que incluya estos cambios; no hay logs retroactivos ni release nueva creada.
- Límites: si PowerShell ni siquiera puede ejecutar el script, hay un cierre forzado,
  falla el disco en ambas ubicaciones o el error ocurre dentro de una app externa,
  no se garantiza captura. Una versión antigua del actualizador que no admite logs
  de bootstrap puede rechazar la preparación si cambia el runtime; usar una nueva
  extracción en ese caso. No se ha validado bootstrap real en Windows 10 con cambios.

## Integración inicial de PostHog (histórico, ampliada arriba)

- `posthog.enabled`, `posthog.host` y `posthog.api_key` en configuración global.
  Desactivado por defecto en distribución; activado localmente para el proyecto
  europeo 276586 con la clave pública facilitada por el usuario. Pendiente validar
  la recepción de eventos en PostHog mediante ejecución del usuario.
  Las releases mantienen valores vacíos y no copian la configuración local.
- Captura errores de CLI, operaciones principales y actualización de la UI,
  errores fatales de Textual y excepciones de trabajos MCP. No intercepta los
  programas lanzados, bootstrap PowerShell ni cada validación de los diálogos;
  tampoco convierte logs de texto o códigos de salida de procesos en excepciones.
- API HTTP directa de PostHog, sin dependencias nuevas. Envía tipo, cadena de
  causas (hasta cinco), hasta 50 frames por causa y versión. No envía mensajes
  de excepción, variables, código fuente, argumentos, perfiles ni logs. Las rutas
  de frames de MITO son relativas; en otras bibliotecas sólo se envía el basename.
  Identificador aleatorio por proceso, sin perfiles de persona en PostHog.
- Un hilo daemon sólo cuando hay errores, cola en memoria y máximo de 20 trazas
  distintas por proceso, sin reintentos. Usa proxy de MITO, no sigue redirecciones
  y limita cada operación de socket a cinco segundos. El cierre de CLI espera hasta
  seis segundos: no garantiza entrega ante DNS bloqueado, desconexión o cierre
  forzado. No persiste eventos ni confirma ingestión en la interfaz.
- Regresiones añadidas en test_error_tracking.py; no ejecutadas. Validación pendiente:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_error_tracking.py -v`.
  Tras configurar PostHog, `mito.cmd profile show posthog/check` provoca un error de
  identificador sin crear ni modificar profiles; comprobar `$exception` en PostHog.
- El usuario ejecutó el error controlado de profile; PostHog sigue esperando la
  primera excepción. Causa aún no identificada. Se amplían los tiempos anteriores
  de dos segundos y se añade `mito.cmd posthog-check`, que envía una excepción
  marcada `mito_diagnostic=true` con timeout de socket de diez segundos y muestra
  respuesta HTTP o fallo de transporte/configuración. No inicializa ni modifica
  profiles; HTTP exitoso no demuestra procesamiento posterior en Error Tracking.
  Pendiente ejecución por el usuario. El agente no ha ejecutado pruebas ni envíos.

## Incremento de versión seleccionable en release

- CLI: `mito.cmd release [mayor|minor|hotfix]`. Sobre M.m.p, mayor produce
  (M+1).0.1, minor M.(m+1).1 y hotfix M.m.(p+1). Sin argumento equivale a hotfix.
  Se toma como base la mayor versión numérica local/remota. Los patrones son
  explicativos: no se pasa un número de versión ni una X literal en el comando.
- Una publicación pendiente conserva su versión y notas aunque se indique otro
  tipo al reanudar. --output se conserva; --local rechaza un tipo explícito y
  continúa empaquetando la versión actual sin incrementarla.
- Añadidas regresiones de cálculo, selección CLI, errores de argumentos, modo
  local y reanudación con otro tipo. Pendientes de ejecución por el usuario:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_publish.py -v`
  y `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_cli.py -v`.
  No se han ejecutado tests ni creado commits, pushes o releases durante el cambio.

## Notas de release y changelog automáticos

- release prepara títulos de commits con enlaces desde el último commit
  `release: vX.Y.Z` en primera línea de ascendencia, incluyendo cambios integrados.
  Sin marcador anterior usa todo el historial; no resume diffs ni mejora los títulos.
- La misma sección de commits se inserta en CHANGELOG.md con versión y fecha de
  preparación y se incluye en el commit de versión. Conserva el historial manual
  y «Sin publicar», que puede contener notas antiguas y requiere mantenimiento manual.
  No rellena huecos históricos ni modifica releases ya publicadas.
- Las notas se guardan con la publicación pendiente para reanudar sin duplicar
  entradas. Pendientes anteriores sin este campo conservan el texto de instalación.
  Ante fallo del commit se restauran versión y changelog. --local sólo empaqueta.
- Regresiones añadidas para límites de commits, primera release, formato, conservación
  del historial, rollback y reanudación. No se han ejecutado pruebas ni publicaciones.
  Validación pendiente del usuario:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -p test_publish.py -v`.

## Filas compactas y alternas en Inicio

- Las filas de componentes instalados ocupan una sola línea; checkbox y botones
  usan también una línea, sin borde ni padding vertical. Un margen de una línea,
  la unidad mínima de la terminal, evita que las filas queden pegadas. Se conservan
  las acciones, sus anchos y el desplazamiento horizontal cuando no caben.
- Las filas alternan dos verdes para facilitar el seguimiento visual. Las
  aplicaciones agregadas del host mantienen una identidad ámbar más sobria: el
  anterior tono oscuro es ahora el principal y se alterna con otro aún más oscuro.
- Se añadieron regresiones de altura y alternancia en test_ui.py, sin ejecutarlas.
  Validación interactiva pendiente: `mito.cmd ui`, revisar Inicio con varias filas
  y comprobar los botones de una aplicación activa y de una aplicación del host.

## Entorno en Info de componentes instalados

- La captura del usuario confirma que el padding del propio VerticalScroll
  desplazaba también la barra hacia la izquierda. Se elimina ese padding y se
  traslada a un Vertical interior que contiene las filas; el scroll ocupa todo
  el ancho hasta el borde y el encabezado conserva su margen independiente.
  Pendiente comprobación visual del usuario con `mito.cmd ui`; no se han
  ejecutado pruebas para esta corrección.

- Presentación reorganizada en grupos con separadores horizontales: RUTAS DEL
  COMPONENTE y APORTACIÓN AL ENTORNO. Etiquetas de rutas en verde; subtítulos
  Variables de entorno y path con espaciado uniforme y sin repetir PATH por fila.
  El encabezado y la X quedan fuera del VerticalScroll interior. El nuevo título
  se traduce a los 24 idiomas. Este ajuste visual no se ha probado automáticamente;
  pendiente ejecutar `mito.cmd ui`, abrir Info y desplazar un componente con muchas
  rutas para comprobar que el encabezado y el cierre siguen visibles.

- Info muestra variables env_set resueltas y las entradas PATH de env_add_path
  y carpetas de bin, sin duplicados. Usa el mismo cálculo que la activación,
  con el manifiesto instalado y su override de arquitectura, respetando las
  copias y persistencia del profile seleccionado. Consultar no prepara payloads.
- Las rutas absolutas individuales de variables y las entradas PATH existentes
  permiten Abrir en Explorer; los archivos abren su carpeta y las rutas ausentes
  tienen el botón deshabilitado. Valores literales no se ejecutan ni expanden
  contra el entorno del host. Las listas de rutas en variables se muestran como texto.
- Se muestra la aportación del componente incluso inactivo, no el entorno final
  combinado; se avisa de posibles sobrescrituras. No se atribuye al componente
  el directorio compartido de shims del profile. Etiquetas en los 24 idiomas.
- Regresiones añadidas en test_component_environment.py, sin ejecutar.
  A petición del usuario se comprueban las traducciones nuevas en los 24 bundles
  y se ejecuta test_i18n.py: 9 pruebas OK (incluida la UI en inglés). No se ha
  comprobado visualmente el diálogo en cada uno de los 24 idiomas.
  Validación interactiva pendiente: `mito.cmd ui`, seleccionar profile y abrir
  Info en Java y en un componente con persistencia aislada; revisar variables,
  PATH y botones Abrir, además de un componente sin aportaciones.

## Validación interactiva acordada

- A partir de ahora el usuario ejecuta la aplicación y las pruebas. Al terminar
  un cambio, el agente indica el comando concreto de comprobación y espera el
  resultado. No ejecuta suites, tests ni doctor ni acompaña su progreso salvo
  petición explícita. Esta preferencia sustituye la validación automática anterior.
- Se pueden añadir regresiones sin ejecutarlas. Los resultados históricos de
  pruebas se conservan como evidencia; las validaciones futuras pendientes no
  se presentan como realizadas.

## Licencia y manifiesto en el catálogo

- La tabla de Buscar e instalar añade Licencia: muestra el texto declarado o el
  identifier de un objeto license (URL si falta identifier); sin valor muestra
  «No declarada». Etiquetas traducidas a los 24 idiomas de la interfaz.
- Manifiesto, junto a Instalar, abre el JSON completo del snapshot del catálogo
  en un diálogo de solo lectura, con sangría de cuatro espacios, sin resaltado
  y con desplazamiento vertical y horizontal. Se cierra con × o Escape.
  Disponible para cualquier fila seleccionada, sin exigir profile ni compatibilidad.
- Validación interactiva pendiente: ejecutar `mito.cmd ui`, abrir Buscar e instalar,
  revisar licencias y consultar/cerrar manifiestos de distintas filas, incluidos
  componentes no soportados y búsquedas sin resultados. No se han ejecutado tests.

## Correcciones tras la validación de 250 pruebas

- El usuario aporta una ejecución con dos errores y dos fallos; la publicación
  quedó detenida. host-run leía args.shortcut, inexistente y ajeno a las referencias
  del host: se retira ese argumento. run ahora transmite --shortcut al motor,
  conservando la etiqueta también en modo coupled/owned.
- Pruebas de UI adaptadas al selector Proceso y a la etiqueta Editor transmitida
  al abrir y crear accesos. Expectativas CLI actualizadas para shortcut opcional;
  añadidas regresiones de etiqueta explícita en run y app-shortcut.
- El usuario verifica las 8 pruebas CLI: OK. De 20 pruebas UI pasan 19; la restante
  consultaba el primer Input (ahora Argumentos) como si fuera la etiqueta del icono.
  Se corrigen sus cuatro selectores para usar #shortcut-badge explícitamente.
  El usuario repite la prueba afectada: 1 test en 18.742 s, OK. Quedan verificadas
  las 8 pruebas CLI y las 20 UI en ejecuciones separadas; no se ha repetido la suite
  completa. El agente no ha ejecutado pruebas ni reintentado la publicación.

## Extracción genérica de Inno Setup

- Diálogos de Abrir y Generar acceso directo unificados: destino, argumentos y
  Proceso (detached/attached, literales en todos los idiomas) en una sola pantalla.
  Generar conserva las letras del icono. Se elimina el selector modal previo;
  Enter respeta el proceso elegido. Cambio de UI pendiente de prueba del usuario.

- El usuario confirma que AGS se instala. El diagnóstico de Abrir localizó dos
  archivos AGSEditor,1.exe y AGSEditor,2.exe con SHA-256 idéntico, sin AGSEditor.exe.
  Se recuperan genéricamente destinos declarados desde variantes idénticas durante
  la instalación; variantes distintas producen error. Requiere reinstalar el componente
  previamente extraído. No se ha modificado su payload instalado durante el desarrollo.
- Abrir y Acceso directo ofrecen selección entre entradas disponibles, con etiqueta
  conservada en CLI, accesos y pestañas. No requieren persistencia, sí activación.
  CHM se abre con hh.exe y el entorno del profile. Otras asociaciones no implementadas.
- Regresiones de variantes y selección añadidas sin ejecutar; cambios nuevos pendientes
  de la prueba interactiva del usuario.

- innosetup booleano deja de bloquear el manifiesto. Con true, los .exe se extraen
  mediante innounp privado; sólo {app} se publica, normalizado a la raíz.
- Bootstrap incorpora innounp 2.71.1 desde InnoUnpacker-Windows-GUI, commit
  6fb49264aacf512a093e7b4fc6fb3dd266dad31a, con SHA-256 fijado. Las instalaciones
  existentes necesitan ejecutar `mito.cmd bootstrap` para obtenerlo.
- Se valida el listado antes de extraer y se cotejan archivos y tamaños después.
  Se rechazan rutas peligrosas, duplicados, solapamientos, enlaces y límites excedidos.
  Nunca se ejecuta el setup. Registro, servicios, tareas, condiciones y destinos
  ajenos a {app} no se reproducen; contraseñas y medios externos no se solicitan.
- Regresiones preparadas en core/tests/test_inno.py. Validación pendiente del usuario:
  no se han ejecutado pruebas ni innounp ni instalado Adventure Game Studio.
  La compatibilidad del extractor no garantiza funcionamiento sin pasos del setup.

## installer.file PowerShell y descargas mixtas

- Se admite installer.file relativo .ps1 y args opcional (texto/lista de cadenas),
  alternativo a installer.script. Se bloquean EXE/MSI, file+script, args sin file,
  keep, campos desconocidos y rutas fuera del payload. Sin inferencia de archivo.
- Se ejecuta después de pre_install con el runner y contexto privados; el archivo
  se conserva. args se tokeniza antes de sustituir las variables de contexto
  soportadas y se transmite como datos posicionales, sin evaluación PowerShell.
  Parámetros nombrados desde args no implementados; variables desconocidas literales.
- extract_dir sólo se aplica a contenedores realmente extraídos. Los archivos
  sueltos de una descarga mixta no necesitan esa subcarpeta. extract_to y las listas
  posicionales se conservan; una carpeta ausente dentro del ZIP sigue dando error.
- Regresiones con HTTP local, hashes y PowerShell reales: ZIP+PS1, espacios en
  rutas, argumentos vacíos y expresiones literales, override x64, código de salida
  no cero, hash incorrecto, confirmación sin hash, archivo ausente y conservación
  de versión/profile al fallar. No se importa ni ejecuta el Python de los fixtures.
- La UI y el log identifican installer.file. No se ha instalado ni ejecutado el SDK
  App Engine real; su Python debe estar disponible en el profile, no se selecciona
  automáticamente el Python privado del bootstrap.
- extras/appengine-go 1.9.70 del catálogo local devuelve Compatible. Validación:
  241 pruebas correctas en 571 s, sin fallos ni omisiones; evidencia en
  core/state/installer-file-tests.log. Doctor OK en los tres profiles y
  git diff --check correcto. Sin commits, pushes ni releases.

## JAR, ICO e iconos declarados en shortcuts

- .jar y .ico se copian byte a byte antes de detectar ZIP/TAR. .ico ya no bloquea
  el catálogo; los alias #/archivo.zip siguen solicitando extracción del contenido.
- Los JAR declarados se lanzan mediante java.exe -jar resuelto con el PATH del
  profile, sin recurrir a asociaciones de archivos de Windows. Se conservan
  argumentos, entorno y workspace. Java debe estar disponible: no se instala ni
  selecciona versión automáticamente. El .cmd de shortcuts mantiene su prioridad.
- shortcuts[3] admite un icono relativo .ico/.exe/.dll, tomado de la misma entrada
  de lanzamiento y del payload del profile. Icono ausente usa el ejecutable;
  fallo de lectura/composición usa el icono MITO. Rutas externas/traversal y
  formatos/índices de recursos no implementados se rechazan en el catálogo.
- Los accesos actuales (registro app) se regeneran también al sustituir el
  componente, igual que los legados (installation), conservando nombre, etiqueta,
  argumentos y rollback. El compositor cambia el ICO derivado si cambian sus bytes.
- Regresiones: JAR ZIP conservado, alias ZIP extraído, JAR+ICO y wrapper preparados
  con PowerShell real, selección de Java/argumentos/entorno y error si falta,
  icono multirresolución declarado, sustitución de icono en acceso actual,
  override x64, aislamiento por profile, fallback y confinamiento de rutas.
- Validación final: 235 pruebas correctas en 446 s, sin fallos ni omisiones;
  evidencia en core/state/jar-icon-tests.log. Doctor OK en los tres profiles
  y git diff --check correcto. Incluye las regresiones de installer.script
  y traducciones incorporadas en la iteración anterior.
- extras/bytecode-viewer 2.13.2 del catálogo local devuelve Compatible. No se ha
  instalado ni ejecutado Bytecode Viewer: se probaron payloads inertes. Su wrapper
  requiere javaw en el entorno del profile; el manifiesto no declara esa dependencia.
  Sin commits, pushes ni releases.

## Ejecución genérica de installer.script

- Por petición explícita, el motor admite installer con sólo script (cadena o
  lista de líneas). Rechaza campos adicionales, incluso vacíos, y tipos inválidos
  antes de descargar, también en dependencias. Sin excepciones por componente.
- Orden: extracción, pre_install, installer.script, validación y publicación.
  Ambas fases usan el mismo runner, entorno privado, contexto ($fname incluido)
  y helpers, en procesos PowerShell separados; no comparten variables del script.
  Fallos y enlaces no gestionados abortan; se conserva la instalación anterior
  y el profile. Los efectos externos de los scripts no se revierten.
- post_install sigue omitido. No se implementan installer.file/args/keep ni
  se promete compatibilidad con todos los helpers de Scoop. No se repiten scripts
  al activar ni al reutilizar un payload idéntico.
- UI y log identifican installer.script; mensajes genéricos en los 24 idiomas.
  Pruebas con PowerShell y 7-Zip reales sobre payloads inertes: archivo anidado en
  $PLUGINSDIR, eliminación de auxiliares, Expand-7zipArchive -Removal, orden,
  contexto y override x64, campos inválidos, fallo de pre_install y conservación
  de versión/profile ante error, bin ausente o junction no gestionada.
- Verificado extras/android-messages 6.1.1 del catálogo local: Compatible. No se
  ha instalado ni ejecutado la aplicación real. Doctor OK en los tres profiles.
- Validación: 38 pruebas del motor correctas. Suite completa de 230 casos en
  468 s: sólo fallaron 11 subcasos del test de placeholders de traducciones;
  algunos bundles traducían el literal pre_install. Corregidos los parámetros
  genéricos y revalidadas las nueve pruebas de i18n/UI traducida, todas OK.
  Evidencias: core/state/installer-script-tests.log y
  core/state/installer-script-i18n-tests.log. git diff --check correcto.

## Descargas sin extensión y contexto de pre_install

- Catálogo, extracción y hooks comparten el nombre de descarga validado: último
  segmento de URL o alias #/nombre, decodificado y sin query. Se conserva el caso.
- Archivos sin extensión se copian como archivos sueltos; ZIP/TAR se reconocen
  por contenido y 7z/RAR por firma. MSI mantiene el requisito de extensión .msi.
  No se usan Content-Disposition ni el nombre de una redirección; URL sin nombre
  necesita alias. Otras extensiones no soportadas permanecen bloqueadas.
- pre_install recibe $fname como cadena para una descarga y lista ordenada para
  varias, como [Invoke-ScoopDownload de Scoop](https://github.com/ScoopInstaller/Scoop/blob/master/lib/download.ps1).
  extract_to no forma parte de ese nombre;
  los archivos extraídos no se conservan automáticamente para el hook.
- Regresiones con PowerShell real: descarga sin extensión, alias con espacios,
  override x64, renombrado, múltiples URL y extract_to. También ZIP sin extensión,
  traversal rechazado, nombres Windows inválidos y 7z real sin extensión.
- No se ha instalado ni ejecutado AdwCleaner: su patrón de manifiesto se prueba
  con payloads locales inertes. La verificación de hashes y la confirmación para
  descargas sin hash conservan el flujo existente.
- Verificado el catálogo local: extras/adwcleaner 8.8.1 devuelve Compatible.
  Suite general: 226 casos en 424 s, 225 correctos y un error del fixture nuevo
  (7-Zip añadía .7z al crearlo). Corregido el fixture y revalidadas las diez pruebas
  de activación/extracción, todas correctas. Las 34 del motor también pasan con
  el código final. Evidencias: core/state/extensionless-tests.log y
  core/state/extensionless-engine-tests.log. Doctor y git diff --check correctos.

## Catálogo consciente del inventario instalado

- Disponibilidad muestra «Instalado» para todo bucket/app presente en el inventario,
  aunque esté inactivo en el profile o su versión difiera del catálogo. Instalar
  queda deshabilitado; los detalles muestran el mismo estado. Las actualizaciones
  continúan en Inicio y el contador de disponibles excluye los ya instalados.
- El filtro lee el inventario una vez, no por fila. Reabrir el catálogo, terminar
  una operación o recibir cambios MCP refresca el estado. Sin selección/resultados,
  para entradas no soportadas o durante una operación, Instalar también está
  deshabilitado. El manejador ignora intentos de pulsar el botón deshabilitado.
- Regresión gráfica ampliada: instalado inactivo con versión distinta, botón sin
  acción, otra entrada disponible, búsqueda vacía de resultados y retorno a
  disponible tras retirar el registro. Prueba focalizada y doctor correctos.
  Suite completa: 221 pruebas correctas en 449 s, sin fallos ni omisiones;
  evidencia en `core/state/catalog-installed-tests.log`. `git diff --check` correcto.
- Textos en los 24 idiomas. Sin cambios en instalación/activación del motor,
  sin modificar componentes o profiles del usuario, ni crear commits o releases.

## Progreso visible durante extracción y preparación

- Instalación real iniciada por el usuario: Rust terminó su descarga a las
  15:06:37 y publicó su registro a las 15:19:15. A las 15:17 seguía procesando
  el séptimo CAB y consumiendo CPU; no estaba bloqueado. `rustc --version` y
  `cargo --version` del componente instalado devuelven 1.98.1 correctamente.
- El log conservaba «Descargando» durante toda la extracción y preparación.
  `archives.extract` recibe ahora el callback de log del motor y anuncia la
  extracción. MSI informa de CAB actual/total y archivos completados cada 1.000
  entradas y al terminar. El motor anuncia preparación, progreso de copia y
  validación. No hay porcentajes ficticios, timers ni procesos de seguimiento.
- Los mensajes están en los 24 bundles. La regresión MSI real comprueba el orden
  de las fases hasta publicación; sus cinco pruebas pasan, igual que doctor.
  Suite completa: 221 pruebas correctas en 457 s, sin fallos ni omisiones;
  evidencia en `core/state/msi-progress-tests.log`. `git diff --check` correcto.
- Este cambio informa del trabajo, no acelera la extracción. La sesión ya abierta
  conserva el código cargado; los mensajes nuevos se aplican al reabrir el gestor.

## Extracción MSI sin ejecutar el instalador

- `msi.py` lee la base MSI con `MsiOpenDatabaseW` en modo read-only y reconstruye
  la imagen de archivos mediante Directory, Component, File y Media. Usa nombres
  largos y directorios de origen (incluidos target:source y puntos), sin anteponer
  SourceDir. `extract_dir`/`extract_to` mantienen el flujo genérico del motor.
- Lee sólo los CAB incrustados de `_Streams` y los entrega al 7-Zip privado.
  Valida rutas, referencias, ciclos, duplicados, colisiones archivo/directorio,
  secuencias, inventario exacto y tamaños. Cada CAB tiene límites de extracción
  derivados de su tabla File, además del límite global. No añade runtime ni paquetes.
- No invoca msiexec, abre asistentes, registra productos ni ejecuta acciones MSI.
  Los archivos externos/sin comprimir y CAB divididos se rechazan. No se aplican
  selección de features, condiciones, registro, servicios ni acciones personalizadas;
  se extraen todos los archivos. Tampoco se añade el helper PowerShell Expand-MsiArchive.
- La publicación, activación, sustitución y eliminación usan el motor existente.
  Las cinco regresiones específicas incluyen MSI/CAB real generado localmente,
  instalación sin activación, activación posterior, actualización, rollback ante
  bin ausente, eliminación, corrupción, medios externos y límites/rutas inválidas.
  El fixture contiene una acción MSI de error que la extracción no ejecuta.
- Prueba real: Rust MSVC x64 1.98.1 descargado con el SHA-256 del manifiesto,
  ocho CAB y 65.893 archivos extraídos correctamente en staging (707 s en este
  equipo, durante la suite general). `rustc`, `rustdoc` y `cargo --version` devuelven
  1.98.1 y código cero en un home/workspace temporal de MITO. Compilar una biblioteca
  rlib funciona; enlazar un EXE falla por ausencia de `link.exe`, dependencia del host
  que la extracción no instala. No se afirma portabilidad autónoma de la cadena MSVC.
- Evidencias locales fuera de Git: `core/staging/msi-check/versions.json`,
  `compilation.json`, MSI verificado y archivos extraídos. Rust no se ha publicado
  en el inventario ni activado en los profiles personales de esta instancia.
- Suite general: 220 pruebas en 822 s; 219 correctas y un fallo de temporización
  del test de layout del diálogo de profiles (regiones todavía en cero). Añadida
  espera de render con `pilot.pause()` y revalidado el test, correcto en 13 s.
  Las cinco pruebas MSI finales pasan (una se añadió después de iniciar la suite):
  221 casos distintos entre ambas ejecuciones. No se repitió la suite completa.
- `mito.cmd doctor` correcto en los tres profiles y `git diff --check` correcto.
  Logs locales: `core/state/msi-tests.log` y `core/state/msi-ui-recheck.log`.
  Sin release, commit, push ni cambio de versión por esta tarea.

## Auditoría de pruebas previa a 1.0.0

- Revisados contratos, código, aserciones e historial local sobre `f69bc3b`,
  versión 0.1.5. Informe en `spec/TEST_READINESS_1.0.md` con matriz funcional,
  huecos priorizados y aceptación propuesta para una distribución candidata.
- Suite completa: 216 pruebas OK en 442,408 s, sin fallos ni tests omitidos;
  `mito.cmd doctor` OK en los tres profiles y `git diff --check` correcto.
  Evidencia local: `core/state/test-review.log`, fuera de Git. El log contiene
  avisos de temporización Textual y limpieza implícita de un HTTPError simulado;
  el informe registra esa mejora menor de higiene de fixtures.
- Cobertura sólida de instalación, activación, persistencia, rollback, host,
  MCP y actualización online/offline. No se ha medido cobertura de líneas/ramas
  ni repetido una aceptación gráfica completa en Windows limpio.
- Antes de publicar se recomienda reforzar fallos del bootstrap inicial,
  exclusión del lock común entre procesos y tras cierre forzado, ramas de error
  del publicador y matriz de archivos comprimidos malformados/límites.
  Son huecos de pruebas; esta revisión no demuestra fallos de esos mecanismos.
- Creado `CHANGELOG.md` con historial 0.1.0–0.1.5 reconstruido desde commits
  locales y sección Sin publicar. No se acredita publicación remota a partir
  de esos commits. No se han añadido tests ni modificado contratos del motor.
- El salto exacto a 1.0.0 sigue pendiente: `release` incrementa siempre el parche;
  fijar 1.0.0 antes de ejecutarlo produciría 1.0.1 si no hay otra versión mayor.
  Versión sin cambios; no se ha creado release, commit ni push.

## Landing, manual y capturas en color

- README convertido en una landing breve orientada a beneficios: profiles,
  catálogo, portabilidad, aplicaciones del host, MCP y uso corporativo. La guía
  exhaustiva pasa a MANUAL.md y enlaza la referencia técnica existente.
- MANUAL.md documenta el flujo completo de usuario: arranque, interfaz, profiles,
  catálogo, instalaciones, terminales, aplicaciones y datos del host, accesos,
  configuración, proxy, Pocketools, actualización online/offline, MCP, CLI,
  almacenamiento, compatibilidad, seguridad y diagnóstico.
- Capturas de Inicio y Catálogo regeneradas desde Textual 0.1.4 con el inventario
  y los 4.972 manifiestos reales de esta instancia. La captura de catálogo conserva
  la búsqueda `java 21 lts` y los filtros de sus cuatro buckets. Para evitar datos
  personales en la landing se usa el profile neutro `java21`; la fila del host es
  una referencia de solo lectura al 7-Zip privado, presentada como ejemplo.
- Los SVG anteriores se exportaron bajo `NO_COLOR` y sólo contenían escala de grises.
  Los nuevos conservan la paleta truecolor de MITO y texto vectorial. Ambas vistas
  se renderizaron localmente a PNG para su inspección visual.
- La captura headless desactivó en ese proceso la red, las comprobaciones de
  actualización y la escritura de filtros. No cambió profiles, catálogos,
  componentes ni configuración. No se ha creado una release, commit ni push.

## Rendimiento del catálogo

- Clasificación de manifiestos con validación sintáctica de rutas Windows, sin
  resolver rutas contra el filesystem del host. `inside` conserva la resolución
  física y el confinamiento para operaciones reales sobre archivos. Los alias
  se validan sintácticamente, sin consultar una carpeta ficticia en C:.
- Catálogos remotos: lectura de JSON directamente del ZIP, sin extraer el árbol
  del repositorio. Se validan todas las rutas, duplicados sin distinguir mayúsculas,
  enlaces y límites de entradas/tamaño; se exige raíz única. Se respeta bucket/
  frente a manifiestos en raíz y se mantiene publicación atómica por catálogo.
- Búsqueda vacía ordena sin tokenizar; caché acotada de términos por texto para
  búsquedas sucesivas, sin conservar resultados obsoletos al cambiar manifiestos.
- Medición local con 4.972 entradas: lectura y clasificación 0,84 s (clasificación
  anterior 14,58 s). Importación remota completa de cuatro buckets a una carpeta
  temporal: 5,47 s. Son mediciones de servicios, no del render completo de la UI;
  la red y el equipo pueden variar. No se modificaron los snapshots de la instancia.
- Se mantienen descargas secuenciales completas y clasificación en el hilo UI;
  no se incorporan ETag, caché persistente de compatibilidad ni nuevos workers.
  Las mejoras medidas evitan por ahora esa complejidad adicional.
- Verificación: suite completa de 215 pruebas OK y `mito.cmd doctor` OK.
  Reejecución focalizada de 30 pruebas del motor, cuatro de búsqueda y cuatro
  del catálogo tras los últimos ajustes; todas OK.

## URL corporativa y comprobación diaria

- Configuración incorpora mito.update_url con la API de GitHub como valor por
  defecto. Admite HTTP/HTTPS y JSON de release compatible con GitHub/Gitea:
  tag_name y assets con name, size, browser_download_url y digest opcional.
  Las URLs declaradas de adjuntos pueden usar rutas/orígenes propios del servidor;
  no se fuerza el patrón de GitHub. Se mantienen pins y revisión de hashes ausentes.
- Caché persistente por día local para MITO y componentes, incluidos intentos
  fallidos o interrumpidos. Un 404 sólo se consulta y registra una vez por día.
  El botón sigue disponible desde el resultado guardado. Cambios de URL/versión o
  inventario/fuentes renuevan la consulta correspondiente. ZIP offline sin espera.
- Estado bajo core/state, excluido de las releases; lock breve al reclamar el
  intento y token contra resultados tardíos. Sin procesos, polling ni nuevos timers.
- Regresiones de respuesta Gitea con adjuntos y SHA-256, caché de éxito/error,
  siguiente día, cambio de URL/versión, configuración inválida e intento concurrente.
  Contrato contrastado con la documentación oficial de la API Gitea; no se ha probado
  un servidor corporativo real ni se añade autenticación de repositorios privados.
- Comprobación real en esta instancia: primer intento HTTP fallido en 0,35 s;
  segundo acceso al resultado diario en 0,015 s, sin nueva petición. Propiedad
  añadida a config.properties con el servidor actual. Seis regresiones específicas
  pasan, incluida reapertura sin refresco ni mensajes de componentes; doctor OK.
- Suite general completada: 210 pruebas OK (467 s), más la nueva regresión de
  reapertura incluida en las seis específicas. git diff --check correcto.

## Actualización offline desde update/

- La carpeta update/ de la instancia admite mito-<versión>-windows-x64.zip y su
  .zip.sha256 adyacente. Se elige la mayor versión posterior; tiene prioridad y no
  consulta GitHub. Nombre, metadata, tamaño, hash y payload se validan; ausencia
  de hash mantiene la autorización humana previa. Un sidecar incorrecto se rechaza.
- Se detecta al abrir el gestor o al pulsar Inicio · Instalados, sin watchers.
  El botón indica Offline y el diálogo muestra la ruta. Errores retiran candidatos
  anteriores. Copia el ZIP fuera de MITO sin borrar el original ni modificar datos.
- Las preparaciones online/offline viven en Temp del AppData Local real del host,
  mito-update-*. Publicación y CMD externo reutilizan el flujo existente.
  update/ queda fuera de Git y del ZIP de distribución.
- Límite explícito: offline sólo admite el mismo bootstrap instalado (comparación
  JSON, no formato del archivo). Si cambia, cancela sin red y conserva la instancia:
  las releases mínimas no incluyen runtimes. Sigue vigente la protección de .git.
- Regresiones: selección numérica/prioridad sin red, copia y publicación local con
  datos conservados, consentimiento, hash incorrecto, nombres/metadata y rechazo
  de cambios de bootstrap sin descargar ni ejecutar su instalador.
- Validación: suite general de 204 pruebas; corregida y revalidada una expectativa
  de ruta corta/larga de Windows. Los cinco casos offline y la detección manual
  al volver a Inicio pasan; doctor y diff --check correctos. Sin commits ni releases.

## Actualización de MITO desde la interfaz

- Worker de arranque consulta latest de danielgube/mito; botón Actualizar MITO sólo
  ante release estable posterior y ZIP Windows x64 válido. Errores quedan en el log.
- Modal con versiones, cierre de aplicaciones/pestañas/Terminal y conservación de
  datos. Digest del asset o archivo .zip.sha256; sin ambos exige autorización previa
  explícita. Hash incorrecto, ZIP inválido o bootstrap sin pins abortan la preparación.
- Descarga, extracción y copia mínima del runtime fuera de la instancia. Si cambia
  el lock del bootstrap, prepara sus herramientas completas allí antes de publicar.
- CMD del host con consola nueva, entorno temporal y breakaway del job; confirma
  arranque antes de cerrar el gestor y MCP. Espera al usuario y comprueba procesos
  bajo la raíz, sin terminarlos. Aplicaciones externas requieren cierre manual.
- Sustitución sólo de código/arranque/identidad/metadata y bootstrap cuando cambia;
  configuración, profiles, componentes y datos se conservan. Copia anterior en
  core/staging/self-update-*/backup y reversión ante fallos normales. Revalidación de
  versión, lock común, rechazo de enlaces y de checkout Git como C:\mito.
- Pruebas nuevas con distribución temporal: preservación de datos, rollback de
  código/runtime, procesos abiertos, versiones, hashes, sidecar, límites y modal.
  Python copiado y CMD reales probados con rutas con espacios, %, ! y &.
- Flujo completo real de actualización por CMD sobre una distribución desechable
  completado, con configuración conservada y backup. Inicio offline y fallo de
  lanzamiento mantienen el gestor utilizable. Textos del diálogo en los 24 idiomas.
- Validación: suite general de 190 casos, con una expectativa antigua del sidebar
  corregida y revalidada. Suite final del actualizador: 18 pruebas OK (199 casos
  distintos entre ambas ejecuciones). Revalidados también los siete tests de bundles,
  los tres de transferencia, `mito.cmd doctor` en los tres profiles y `git diff --check`.
- Consulta real de latest, tanto página web como API: HTTP 404 en esta sesión.
  No se ha descargado una release publicada ni actualizado esta instancia de desarrollo.
  No se ha modificado EAP ni creado releases, commits o pushes.

Límites: consulta al abrir o volver a Inicio, sin credenciales para repositorios privados;
el 404 también puede indicar ausencia de releases públicas. No se identifican todos
los consumidores externos del home, no se fuerza el cierre y no se garantiza rollback
tras apagado abrupto. Preparaciones y backups se conservan para recuperación manual.
El cambio real de bootstrap mediante una release remota queda pendiente de disponer
de ella; la sustitución y restauración de runtimes se prueban con fixtures.

## Argumentos simples de BAT/CMD

- Corregido el transporte genérico que entrecomillaba todos los argumentos: BAT
  conserva esas comillas en `%1`, lo que rompe scripts con comparación literal.
  Los tokens simples se envían sin comillas añadidas; espacios, valores vacíos y
  caracteres especiales mantienen la protección y la expansión única por variables.
- Reproducido con `version` en el Tomcat9 instalado: antes mostraba la ayuda; ahora
  ejecuta la opción y muestra Apache Tomcat/9.0.121. Sin excepciones por aplicación.
- Prueba real del runner con `run`: Tomcat completó el arranque en el puerto 8080;
  después se cerró el árbol de prueba mediante su Job Object. Prueba por CLI y pipes,
  sin inspección visual de Windows Terminal.
- La regresión ampliada descubrió que un argumento vacío llegaba como referencia
  literal a la variable temporal: ahora se representa mediante comillas vacías.
- Validación: suite completa de 181 pruebas, con ese único fallo inicial; corregido
  y revalidados los 51 tests de motor, host y MCP, todos OK. `mito.cmd doctor` y
  `git diff --check` OK. Las restantes 180 pruebas de la suite general pasaron.

## Conservación de salida al terminar una aplicación acoplada

- Se elimina la condición que omitía la pausa con código cero, para componentes
  y referencias del host. La pestaña espera Intro tras terminar el árbol y muestra
  el código del lanzador; no se interpreta como garantía del resultado de sus hijos.
- Reproducción local con Tomcat9 de `dani`: sin argumentos, `catalina.bat` muestra
  ayuda y devuelve cero. Para arrancarlo en la consola, su argumento es `run`.
- Comprobado con el runner CLI real y pipes: sigue vivo después de mostrar la ayuda
  y sólo termina al enviar Intro. No se ha automatizado la inspección visual de WT.
- Validación: 180 pruebas OK (385 s), `mito.cmd doctor` OK y `git diff --check` OK.
  Regresiones de salida cero sin argumentos, error de arranque, código no cero,
  aplicación del host y apertura independiente sin pausa.

## Apertura de aplicaciones acoplada a una pestaña

- «Abrir» muestra un diálogo con modo independiente o acoplado para componentes y
  aplicaciones del host. El modo acoplado usa una pestaña dedicada cuyo título es
  el nombre declarativo de la aplicación o el nombre del ejecutable externo.
- El proceso de la pestaña posee un Job Object kill-on-close, crea la aplicación
  suspendida, la asigna antes de reanudarla y espera hasta que no queden descendientes.
  Cerrar la pestaña termina el árbol sin incorporar Windows Terminal ni otras pestañas.
- El diálogo de accesos directos conserva la etiqueta del icono y añade la selección
  de modo. Abrir y crear acceso ofrecen argumentos tanto para `shortcuts` como para
  `bin` y aplicaciones del host; se añaden después de los fijos del manifiesto. El
  registro guarda `coupled` y `arguments`; las migraciones mantienen esos valores y
  los registros antiguos se consideran independientes y sin argumentos.
- Al terminar, el runner acoplado muestra el código y espera Intro incluso con cero;
  también conserva los errores de arranque. Esto permite leer la ayuda o los errores
  de scripts que no propagan el fallo de sus hijos. BAT/CMD reutiliza
  el transporte seguro por variables para no reexpandir metacaracteres.
- Límite real: el cierre es forzado. Procesos que reutilizan una instancia previa o
  delegan en servicios/brokers externos no quedan bajo el Job Object.
- Verificación: 177 pruebas OK, incluidas regresiones de argumentos en UI, CLI,
  accesos, migración y BAT/CMD, pausa por error y árbol de procesos; `mito.cmd doctor`
  y `git diff --check` OK.

## Diálogo MCP y monitor opcional

- Encabezado y aspa fijos fuera del scroll; Copiar configuración alineado a la
  derecha. Comprobado con un terminal de 110 × 24: scroll real, posiciones estables
  y cierre pulsable desde el final del contenido.
- `mcp.jobs.monitor=true|false` en Configuración global, false por defecto incluso
  en instancias sin la clave. true muestra un botón MCP · Jobs y un diálogo propio;
  guardar false vuelve a ocultarlo. No se mezclan trabajos con la conexión MCP.
- El monitor sólo solicita salida al pulsar Actualizar; estados y lista se refrescan
  sin descargar logs. La IA conserva sus jobs, resultados y cancelación; las
  confirmaciones de descargas sin hash siguen apareciendo con el monitor apagado.
- Regresión de opción válida/inválida, visibilidad, diálogo independiente, petición
  explícita de salida y ocultación posterior.
- Suite general ejecutada: 165 pruebas, con cuatro fallos iniciales. Corregida la
  expectativa del test al guardar la nueva opción y restauradas sólo tres claves MCP
  faltantes en cada bundle checo, neerlandés y polaco, conservando las otras ediciones.
  Revalidación de bundles, configuración y MCP: 11 pruebas OK. Doctor y diff --check OK.

## MCP local implementado en feature/mcp-server

- Botón MCP - enabled/disabled y diálogo Textual con activación, puerto,
  configuración copiable de Codex/Claude Code/VS Code/HTTP. Consulta/cancelación
  de jobs en el monitor opcional separado. Siempre disabled al abrir el gestor; identidad y puerto persistentes.
- Servidor Streamable HTTP en 127.0.0.1, sin autenticación, activado exclusivamente
  desde el gestor. Pestaña MITO MCP en el Windows Terminal asociado; ownership por
  pipe privado y Job Objects. Sin workers/listener/SDK importado mientras está disabled.
- 25 herramientas: profiles, catálogo, planes/componentes, activaciones,
  configuración global, pocketools, comandos y trabajos. Comandos con entorno y
  workspace del profile, stdin inicial, salida, timeout y cancelación de descendientes.
  No constituyen un sandbox: usan los permisos del usuario Windows.
- Confirmación humana por Textual para descargas sin hash, incluidas dependencias;
  la API no permite aprobarlas. El motor libera el lock mientras espera y compara
  de nuevo el plan completo antes de publicar. Instalar y activar son independientes.
- Bootstrap con MCP Python SDK 1.30.0 y cierre transitivo de dependencias fijado por
  SHA-256. Verificado arranque desde un CPython 3.14.7 recién extraído y todas las
  wheels verificadas, sin reutilizar paquetes del runtime de desarrollo. Para
  runtimes existentes, mito.cmd bootstrap instala la nueva dependencia.
- Prueba real con el cliente MCP de Codex Desktop 0.153.4: initialize y descubrimiento
  de las 25 herramientas mediante app-server, con CODEX_HOME temporal, sin ejecutar
  un modelo ni modificar clientes personales. SDK oficial: initialize, list_tools,
  estado, creación de profile, ejecución y apagado con cliente conectado.
- Pruebas de Windows Terminal real: abrir pestaña MITO MCP y cerrar su ventana de
  prueba normalmente (WM_CLOSE) o terminar forzosamente su proceso; en ambos casos
  MCP vuelve a disabled y el descendiente de un comando en ejecución termina.
  Regresión automatizada de muerte abrupta del gestor:
  servidor y descendiente de comando terminan por el Job Object. Timeout, cancelación,
  puerto ocupado, exclusión de segundo gestor y arranque cancelado también verificados.
- Flujo integrado con HTTP local: componente SHA-256 con dependencia sin hash,
  rechazo y aprobación de la confirmación sin descargas anticipadas, instalación,
  activación y comando con variable aportada por el componente. Regresiones de
  revisiones obsoletas, conservación de workspace, argumentos %, !, &, Unicode,
  truncado de salida, registro interrumpido y diálogo/refresco de selección.
- Verificación de la implementación inicial: 164 pruebas OK (371 s), mito.cmd doctor OK en dani, java11
  y java21, y git diff --check sin errores. Endpoint apagado al finalizar.
  Pruebas de empaquetado usan fixtures temporales: incluyen módulos MCP y excluyen
  configuración, jobs y salida locales. No se ha creado una release, commit ni push.

Límites observados y pendientes:

- Claude Code y VS Code tienen instrucciones conforme a sus formatos publicados,
  pero no se ha probado conexión con esos clientes nativos en esta máquina.
  En Codex se ha probado conexión y descubrimiento, no un flujo completo dirigido
  por un modelo. No declarar cubiertos todos los criterios del plan original.
- No se ha automatizado cerrar únicamente la pestaña MCP dejando otras pestañas
  abiertas; las pruebas reales de Terminal usaron una ventana propia de una pestaña.
  El servidor es el proceso de esa pestaña, sin wrapper que pueda dejarlo separado.
- El cierre forzado no garantiza rollback de una publicación o escritura ya iniciada.
  Al reactivar se marcan jobs incompletos como interrupted; no se repiten efectos.
- Cuatro workers, 32 trabajos pendientes, 128 totales por activación. Salida 10 MiB
  por job y 64 MiB por sesión; job_output pagina hasta 64 KiB. El visor Textual
  muestra los primeros 64 KiB por stream. Se conservan hasta dos sesiones anteriores
  de menos de siete días, más la actual; limpieza al activar, sin API de historial.
- Las operaciones de publicación ya iniciadas completan su tramo consistente antes
  de un apagado normal. BAT/CMD argv rechaza comillas y saltos de línea; usar script.
  Los errores propios tienen códigos; errores heredados del motor pueden aparecer
  como OPERATION_FAILED con su mensaje original, sin clasificación específica.

Contrato y uso: [ARCHITECTURE](ARCHITECTURE.md), [USAGE](USAGE.md).
El [plan original](MCP_PLAN.md) conserva decisiones y criterios previstos.

## Interfaz localizada: idiomas oficiales de la Unión Europea

- `mito.language` global con valores finitos `system` y los 24 idiomas oficiales
  de la Unión Europea;
  predeterminado `system`, presentado como Automático (Windows). Sin propiedad o
  en automático, GetUserDefaultUILanguage lee el idioma de interfaz de Windows:
  las variantes regionales usan su idioma oficial; otros idiomas y fallos usan
  inglés. La selección manual tiene prioridad. Configuración siempre ofrece
  la fila y un Select, incluso en instancias
  anteriores sin la propiedad. Cancelar no escribe; guardar aplica el idioma al
  volver a abrir el gestor y muestra un aviso. Configuración de profile sigue
  editando sólo env.
- Bundles UTF-8 en core/app/mito/locales, incluidos explícitamente por el empaquetador.
  Plantillas españolas como claves, con interpolación posterior de argumentos.
  Un idioma inválido al leer vuelve a inglés; el editor impide guardarlo. Una
  traducción ausente o con parámetros incompatibles busca en inglés y, si también
  falta, conserva el texto fuente.
- Traducidos Inicio, catálogo (controles propios), configuración, profiles,
  importación/exportación, componentes, accesos, host, pocketools y autenticación;
  también avisos, confirmaciones, ayudas de teclas y mensajes propios de operaciones.
  Los workers consultan el idioma de su gestor, sin modificar un idioma global.
- Los diálogos nativos de archivos/carpetas y autenticación traducen sólo sus
  etiquetas fijas con escape de literales PowerShell. Rutas y credenciales conservan
  sus canales separados. No se ejecuta código de bundles o catálogos para traducir.
- Límites: descripciones/notes de Scoop, info de pocketools y salidas externas
  permanecen literales; CLI y bootstrap siguen en español. No se modifican estados
  de compatibilidad, IDs, metadatos, activaciones, rutas ni entornos del usuario.
- Regresiones: cobertura y parámetros de bundles, fallback, validación/persistencia,
  cancelar/guardar/reabrir, diálogos en inglés, workers, textos de catálogos intactos,
  etiquetas nativas y recursos de idioma en el ZIP temporal de las pruebas.
  Detección de variantes regionales españolas/inglesas, idiomas desconocidos,
  fallo de API, configuración ausente y prioridad de selección manual.
- Comprobación real: Windows devuelve español y el gestor lo resuelve como español
  con `mito.language=system`. Doctor OK en dani, java11 y java21.
- Validación final: 147 pruebas OK con la detección automática incluida;
  `git diff --check` correcto. Sin commits, push ni publicaciones.

## Corrección del contrato: un componente, una versión, rutas estables

- La identidad es bucket/app. El motor mantiene una única versión por componente
  en core/apps/<bucket>/<app>; versión y huella permanecen en .mito.json. Profiles,
  activación, shims y accesos utilizan bucket/app. Componentes diferentes pueden
  coexistir aunque pertenezcan a la misma familia o declaren los mismos comandos.
- persist + shortcuts conserva copias independientes en
  core/profile-apps/<profile>/<bucket>/<app> y datos propios bajo home/profiles.
  Actualizar sustituye también las copias existentes de profiles inactivos o
  eliminados, sin cambiar rutas ni datos. No hay versiones independientes por profile.
- Instalar desde catálogo, importar profiles y resolver dependencias usan la misma
  sustitución. Todo el plan se descarga y valida en staging antes de mover binarios.
  Una huella diferente sustituye incluso con igual número de versión. Actualizar
  desde Inicio sigue exigiendo una versión numéricamente posterior. Se mantiene la
  confirmación previa de descargas sin hash, también al sustituir dependencias.
- Comprueba archivos en uso antes y después de descargar. Conserva árboles antiguos
  hasta publicar copias, profiles y lanzadores. Ante errores previos a la limpieza
  revierte la sustitución. No revierte efectos externos de pre_install ni garantiza
  recuperación automática tras terminación abrupta; staging queda para diagnóstico.
  Un fallo de limpieza final informa de la ruta pendiente y deja la versión nueva.
- migrate-storage convierte explícitamente el formato anterior sin descargar.
  Conserva versiones, datos, activaciones y accesos registrados; rechaza ambigüedad
  o contenido adicional antes de mover. Las rutas guardadas en IDEs externos deben
  corregirse una vez; no se editan configuraciones de terceros.
- Instancia local convertida: seis componentes y cuatro copias por profile.
  Tomcat queda en C:\mito\core\apps\versions\tomcat9. No se han actualizado versiones.
- Las secciones siguientes son historia: cualquier referencia a versiones paralelas,
  IDs con huella o rutas versión-huella queda sustituida por este contrato. La capacidad
  anterior procedía de un malentendido, no de una exigencia de Scoop.
- Validación: suite completa de 136 pruebas OK; después, siete pruebas de almacenamiento
  OK (incluida la regresión adicional de reversión de copias) y una nueva prueba de
  revisión de importación OK: 138 casos distintos verificados. Doctor OK en dani,
  java11 y java21; git diff --check correcto. Inventario local: seis bases estables,
  cuatro copias estables y ninguna carpeta de instalación versión-huella.
  Sin commits, push ni releases.

## Accesos estables y eventos de activación

- Nuevos accesos de componentes: profile - bucket-app.lnk, sin MITO ni versión.
  Comando run --profile PROFILE --app bucket/app; registro e icono derivados de
  profile + bucket/app, sin huella de instalación. Los nombres de accesos ya
  existentes no se renombran automáticamente. No se ha modificado el lanzamiento.
- La migración durante Actualizar convierte accesos antiguos fijados a una
  instalación al nombre estable; en siguientes actualizaciones ya no los modifica.
- El resolver existente no abre componentes inactivos ni coincidencias ambiguas
  entre varias versiones activas. No se activa nada automáticamente desde un acceso.
- Detectada una vía de desactivación en UI: eventos de casillas del profile anterior
  podían editar el recién seleccionado antes de reconstruir filas. Las casillas
  conservan profile y valor mostrado; sólo un cambio real coincidente con el widget
  puede editar ese mismo profile. Se ignoran eventos de montaje, obsoletos o ajenos.
- Regresión de actualización con dos profiles activos y uno inactivo, seguida de
  eventos pendientes al cambiar entre profiles. Verifica activaciones conservadas.
  Regresión de acceso estable comprueba mismo nombre/registro tras cambiar versión
  y resolución del nuevo ejecutable con el comando original.
- Validación: suite completa de 130 pruebas OK, mito.cmd doctor OK y
  git diff --check sin errores. Sin cambiar activaciones ni accesos del usuario
  durante estas comprobaciones; los casos de UI y archivos usan fixtures temporales.

## Sustitución real de componentes

- Actualizar sustituye la instalación elegida: elimina la base antigua y sus copias
  por profile, conservando otras versiones seleccionadas deliberadamente.
- Prepara las copias nuevas para profiles activos y para los que conservaban una
  copia antigua inactiva, sin activar estos últimos. Reutiliza persist por profile
  y persist compartida. También completa las copias si la versión nueva ya existía.
- Migra accesos registrados existentes de profiles vigentes, manteniendo nombre de
  archivo y etiqueta (el nombre puede conservar el número de versión anterior).
  No crea enlaces adicionales; registros de profiles eliminados se dejan intactos.
- Comprueba archivos bloqueados con la API Windows y pide cerrar la aplicación.
  Cambios en persist o en la combinación persist + shortcuts requieren migración
  explícita y se rechazan antes de instalar.
- Fallos previos a la limpieza revierten profiles, accesos, movimientos y copias
  nuevas. La descarga ya publicada queda reutilizable. Un fallo en la limpieza final
  se comunica como pendiente en staging; no se anuncia éxito completo. No se garantiza
  recuperación automática ante terminación abrupta ni reversión de pre_install.
- UI serializa reconstrucciones de Inicio, ignora eventos de widgets retirados y
  evita eventos intermedios al restablecer el selector tras una operación.
- Regresiones verifican sustitución con versión nueva preexistente, profiles activos
  e inactivos, junctions reales, datos independientes, archivos bloqueados Windows,
  rollback de profiles/accesos y una sola fila actualizada en Textual.
- Validación: 128 pruebas de la suite completa OK; prueba adicional de instalación
  desde ZIP y persist compartida OK en la suite de 12 pruebas de componentes.
  mito.cmd doctor OK y git diff --check sin errores. No se han modificado los
  binarios ni los accesos personales de VS Code durante estas comprobaciones.

## Comprobación de actualizaciones al arrancar

- El gestor refresca en segundo plano únicamente los JSON de componentes instalados,
  activos o inactivos en cualquier profile; varias versiones comparten una consulta.
- GitHub: JSON individual de HEAD en bucket/ (raíz como alternativa ante 404).
  Fuentes locales: sólo archivos seleccionados. Sin descarga de snapshots completos.
- Conserva las demás entradas del catálogo y los manifiestos de instalaciones;
  errores por componente se registran y mantienen la versión local disponible.
- Actualizar usa variant success, igual que Instalar, y aparece tras el refresco.
  La instalación sigue siendo explícita, con las confirmaciones de hash existentes.
- Comprobación real: cuatro manifiestos consultados en unos tres segundos;
  extras/vscode instalado 1.136.1 detecta 1.137.0 Compatible. No se instaló la actualización.
- Regresiones: deduplicación, componentes inactivos, conservación de profiles/payloads,
  entradas ajenas, fallos parciales/offline y detección automática en la UI.
- Validación: suite completa de 120 pruebas OK, más la nueva prueba de arranque
  ejecutada aparte OK; mito.cmd doctor OK y git diff --check sin errores.
- Límites: consulta una vez por apertura; necesita acceso al origen. Sin origen,
  conexión o lock disponible conserva el catálogo y deja diagnóstico, sin reintento
  periódico. No actualiza aplicaciones externas del host.

- ACCIONES incorpora Abrir Home: abre home de esta instancia (antes home/AppData)
  en el Explorador de archivos, independientemente del profile seleccionado.
  Doctor OK; suite de 118 pruebas ejecutada, con la única expectativa antigua
  de botones actualizada y repetida correctamente, incluido el clic y su destino.

## Enlaces de datos de aplicaciones del host

- Corrección de junctions manuales: el estado también inspecciona el AppData
  portable, sin seguir enlaces, y reconoce coincidencias por nombre y destino
  relativo en el AppData del host aunque no estén registradas. Desenlazar y
  Eliminar pueden retirarlas; enlazar al mismo destino reutiliza la junction.
  Comprobación de solo lectura en la instancia: Data de Opera Software devuelve
  link con la junction manual existente, sin modificar el enlace ni sus datos.
  Validación: 118 pruebas correctas y doctor OK, incluidas detección sin escribir,
  reutilización idempotente, retirada conservando datos y estado en la interfaz.

- Corrección: botones compactos Cache/Data · link/unlink y fila única. Pulsarlos
  sin enlaces vuelve a buscar candidatos antes de ofrecer la selección manual;
  el selector parte del AppData Local/Roaming real de la categoría. Si se rechazan
  los candidatos se ofrece elegir otra carpeta. Búsqueda real con opera.exe
  comprobada: encuentra Opera Software en Local y Roaming, sin modificar datos.
  Validación de la corrección: 115 pruebas correctas y doctor OK; regresión de
  búsqueda al pulsar Cache, inicio del selector por categoría y ancho de la fila.

- Búsqueda por nombre del ejecutable en Roaming y después Local, sin distinción
  de mayúsculas, conservando candidatos padres y omitiendo junctions y el árbol
  de instalación. Confirmación por candidato y segunda confirmación si hay que
  borrar una carpeta de MITO. Sin reglas específicas para Opera u otros productos.
- Host cache / Host data siempre visibles con estado enlazado/desenlazado.
  Selector manual y revisión de rutas al enlazar; confirmación al desenlazar.
  Filas externas de dos líneas de controles para alojar las nuevas acciones.
- Junctions compartidas entre profiles, registradas en core/state. Eliminar la app
  retira sus enlaces y conserva los datos y ejecutable del host. Se bloquean
  solapamientos y enlaces ajenos; fallo de creación restaura la carpeta local.
- Límites: no se copian los datos al desenlazar, las rutas del host no son portables
  a otro PC y no se cierran procesos. Local puede contener también datos importantes;
  Roaming no garantiza incluir toda la sesión. El buscador es una ayuda por nombre,
  no una detección de todos los almacenes de datos de cada producto.
- Validación: 114 pruebas correctas, incluidas junctions reales en carpetas de
  prueba y flujos Textual automático/manual, cancelación, sustitución y desenlace;
  mito.cmd doctor correcto y git diff --check sin errores. Selector nativo simulado
  en las pruebas de UI; no se han enlazado ni borrado datos personales del host.

## Aplicaciones externas con entorno MITO

- Filas externas con fondo y texto anaranjados; nombres alineados con las etiquetas
  de los componentes activables, reservando el espacio del checkbox sin mostrarlo.
  Alineación comprobada en la interfaz renderizada. Validación: 106 pruebas y doctor
  correctos; el selector ignora eventos pendientes cuando su widget ya no está en pantalla.
- Fila inferior de Inicio dividida entre Buscar e instalar y Agregar aplicación del
  host. Selector nativo .exe/.bat/.cmd/.ps1; referencias en core/state, sin copiar
  ejecutables ni añadir campos a profiles. Filas externas compartidas con Abrir,
  Acceso directo, Info y Eliminar (confirmación, conserva ejecutable/datos).
- Conforme a la aclaración del usuario, se ejecutan con engine.environment(profile)
  y su workspace, incluido APPDATA de MITO. Accesos conservan profile y etiqueta
  Pillow. Ninguna reconstrucción de entorno Windows del host permanece en el código.
- Scripts CMD/BAT/PowerShell probados con rutas con espacios, %, !, &, ^ y apóstrofes.
  Pruebas de entorno verifican APPDATA/LOCALAPPDATA, PATH y variables por profile.
- Validación: 106 pruebas correctas y doctor correcto. Flujo de UI probado con
  selección de archivo simulada; scripts de prueba ejecutados realmente en procesos hijos.

## Pocketools compartidas

- pocketool.<nombre> en configuración y diálogo Pocketools con Actualizar por paquete.
  pocketool.json requiere info de texto. Raíces válidas en PATH compartido, sin recorrer
  subcarpetas ni gestionar dependencias. Sólo se incorporan al abrir un nuevo entorno.
- Git HTTP/HTTPS: clone sin checkout y exportación validada, sin hooks/filtros/submódulos.
  Git debe estar disponible en host o componentes instalados; usa proxy de MITO y
  autenticación de Git disponible, sin prompts. No LFS ni ramas seleccionables.
- Admite origen local para desarrollo. Valida antes de reemplazar; conserva la copia
  anterior ante fallo. Actualizar sustituye cambios locales. pocketools/ ignorado en Git
  y excluido del ZIP mínimo de release.
- Ejemplo creado en C:/pocketool-example, configurado como pocketool.example y sincronizado
  en pocketools/example. holamundo.cmd y holamundo.ps1 ejecutados correctamente desde PATH;
  rutas verificadas en java11 y java21. Sin crear commits ni publicar repositorios.
- Validación: 100 pruebas correctas y doctor correcto. Clone/archive real comprobado
  con un repositorio Git local existente y rechazo al faltarle pocketool.json; transporte
  HTTP comprobado con simulación de Git. Ninguna pocketool remota real instalada.

- Panel de log ampliado de 6 a 8 líneas de altura, manteniendo el botón Copiar.

## Editores de configuración global y de profile

- Filas compactas de tres líneas, sin margen vertical entre propiedades, en ambos
  editores; controles y claves mantienen el centrado vertical.
- ACCIONES incluye Configuración para config.properties; Gestionar profile incluye
  Configuración para su objeto env. Un editor común con cabecera y cierre por cruz/Escape.
- Claves administradas fijas, booleanos con Select y valores libres con Input.
  Variables libres sin prefijo env. visible; fuentes de bucket y campos del portal
  añadibles por nombre. Cada fila tiene Eliminar; sólo Guardar escribe.
- Conserva comentarios, líneas desconocidas y propiedades existentes; valida claves,
  variables reservadas y valores administrados. Guardado atómico bajo lock con
  detección de ediciones externas. Nunca modifica apps/workspace al editar env.
- No modifica entornos abiertos; nuevo entorno al abrir pestaña/aplicación.
- Validación: 95 pruebas y doctor correctos. Corregido evento pendiente del selector
  al desmontarse la interfaz; las pruebas esperan también a completar el refresco.

## Ejecución genérica de pre_install

- Por petición explícita se sustituye la omisión de pre_install por ejecución
  PowerShell después de extraer y antes de validar/publicar. post_install sigue omitido.
- Contexto Scoop básico y Expand-7zipArchive genérico sobre el extractor privado;
  sin importar Scoop ni añadir excepciones por componente. Helpers no implementados
  producen error. El script tiene permisos del usuario: no es un sandbox y sus
  efectos externos no se revierten si falla la instalación.
- Las bases aisladas usan persist temporal vacío; las compartidas reciben su persist
  común. No se ejecutan scripts al activar profiles ni se mezclan datos de profiles.
- Regresiones con extracción anidada equivalente al manifiesto de Altus, fallo sin
  publicar, override de arquitectura, rutas de home y persist compartido/aislado.
  No se ha descargado ni arrancado Altus real; instalaciones previas requieren reinstalar.
- Validación: 89 pruebas correctas, las cuatro regresiones de pre_install repetidas
  tras fijar el helper a python.exe (también al abrir MITO mediante pythonw), doctor
  correcto y diff sin errores de whitespace.

## Presentación de los diálogos de gestión e información

- Cabecera común centrada, verde y en negrita, con cruz roja para cerrar;
  Escape conserva el mismo comportamiento. Textual usa el tamaño de fuente
  de la terminal: la jerarquía visual se consigue con color, negrita y espacio.
- Gestión separa título, nombre e input con líneas de espacio y coloca
  Exportar/Importar en una misma fila.
- Validación de los diálogos: 86 pruebas y doctor correctos.

## Gestión de profiles en diálogo y versión visible

- Sidebar reducido a selector/Gestionar profile y acciones Inicio/Internet.
  Retirado el texto del home y movidos creación/exportación/importación/acceso/borrado.
- Confirmación de eliminación con Cancelar por defecto y Escape; último profile
  deshabilitado y protegido también por el motor.
- Cabecera muestra __version__, sin duplicar el número en UI.
- Capturas del README actualizadas. Validación: 86 pruebas correctas (incluidas
  cancelación de borrado, último profile y gestión desde diálogo) y doctor correcto.

## Selector nativo al importar profiles

- Importar abre OpenFileDialog de Windows, filtrado a JSON y un único archivo.
- Examinar permite repetir la selección; cancelar no modifica la ruta ni importa.
- El diálogo corre en un worker; se mantiene entrada manual ante fallos o cancelación.
  La ruta inicial viaja por stdin, nunca interpolada en código PowerShell.
- Validación: 85 pruebas correctas y doctor correcto; selector nativo simulado en
  la prueba de UI para verificar selección y cancelación sin abrir ventanas de prueba.

## Exportar e importar selecciones de profiles

- Botones Exportar/Importar y CLI profile export/import. Archivo con sólo apps
  bucket/app activas, sin env/workspace/manifiestos/datos del origen.
- Revisión del plan con versiones locales, dependencias y estado instalado/pendiente.
  Nuevo nombre elegido por destinatario; profile se publica sólo tras instalar todo.
- Reutiliza instalaciones actuales y conserva confirmación para descargas sin hash.
- Exportación no sobrescribe archivos. Importación rechaza campos extra y profiles
  existentes. exports/ está excluido de Git. No se exportan versiones históricas.
- Validación: suite de 85 pruebas, con una prueba de red ajustada para desplazar
  el sidebar antes del clic; esa prueba y el flujo exportar/importar pasan al repetir.
  Las otras 84 pasaron en la suite. Doctor y comprobación de diff correctos.

## Log copiable y errores en rojo

- Los errores del worker se escriben como Rich Text rojo/negrita, sin interpretar
  markup en los mensajes. Se conserva el texto original para copiarlo.
- Botón Copiar log junto al registro, disponible durante operaciones y sin profile.
  Copia toda la sesión con saltos de línea al portapapeles nativo mediante stdin,
  sin insertar texto del log en comandos PowerShell. Notifica éxito o fallo.
- Prueba de UI verifica color, texto literal, saltos de línea y copia mientras busy.
- Validación: 79 pruebas correctas y doctor correcto. La prueba de copia simula
  el proceso de portapapeles para no sobrescribir el contenido del usuario.

## Confirmación previa de descargas sin hash

- Popup único para instalar/actualizar, incluidas dependencias pendientes sin hash.
- Cancelar por defecto y Escape; rechazo antes de cualquier descarga o publicación.
- CLI confirma con [s/N]; llamadas al motor sin callback no autorizan estas descargas.
- Prueba de UI cubre cancelar y aceptar; motor cubre dependencia sin hash y ausencia
  de efectos al cancelar. No se pregunta por payloads ya instalados.
- Validación: 78 pruebas correctas, doctor correcto y diff sin errores de whitespace.

## Hashes opcionales y algoritmos adicionales

- MD5/SHA-1 admitidos; hashes de longitud fija disponibles en hashlib con prefijo.
- Hash ausente, vacío, null o skip descarga sin comprobación. Listas de hashes
  permiten entradas sin verificación; omisión global cubre todas las URL.
- Los hashes declarados se validan, y discrepancias descartan la descarga.
  HTTP/HTTPS, validaciones de extracción y hashes fijados del bootstrap se conservan.
- extras/abdownloadmanager clasifica como Compatible. Suite de 75 pruebas correcta,
  seguida de 3 pruebas de transferencia con nueva regresión de hashes mixtos.
  Doctor correcto; no se ha instalado la aplicación.

## Extracción RAR con 7-Zip

- Catálogo admite .rar y el motor usa el extractor privado, incluido un .exe
  renombrado por el fragmento Scoop #/archivo.rar. Nunca ejecuta el autoextraíble.
- Se reutilizan los límites, listado previo y rechazo de rutas/enlaces/cifrado.
- Instalación real de extras/activaid 1.3.1 correcta, sin activación automática.
  Validación: 74 pruebas correctas, doctor correcto y diff sin errores de whitespace.

## Aliases bin con espacios y signos

- Separada command_name de identifier; catálogo, bins y shims comparten validación.
- Los lanzadores entrecomillan aliases, escapan % y desactivan expansión retardada.
- Prueba con CMD real verifica 7+ Taskbar Tweaker y caracteres &/%/! literales;
  identificadores internos y rutas inválidas mantienen sus restricciones.
- extras/7tt clasifica como Compatible. Suite de 73 pruebas correcta, seguida de
  las 23 del motor tras cubrir aliases que empiezan por guion; doctor correcto.

## README breve para GitHub

- README reducido a presentación, inicio rápido, componentes base y límites.
- Captura actual de Inicio exportada por Textual en docs/images/mito-inicio.svg,
  referenciada con ruta relativa y fuera de las exclusiones de Git.
- Documentación detallada conservada en spec/USAGE.md. Sin commit ni push.
- Logo original restaurado como cabecera y captura de búsqueda/instalación añadida
  en docs/images/mito-catalogo.svg, con búsqueda real de java 21 lts.

## ZIP con compresión no soportada por Python

- Fallback genérico a 7-Zip integrado ante NotImplementedError de zipfile.
  Mantiene validación previa ZIP y listado/validación del extractor privado.
- Regresión con ZIP Deflate64 real y rechazo de traversal antes del fallback.
- Reinstalación real de extras/64gram 1.2.8 correcta, sin activación automática.
- Validación: 72 pruebas correctas, doctor correcto y diff sin errores de whitespace.

## Omisión universal de pre_install

- pre_install ya no bloquea instalaciones; se omite igual que post_install y se
  informa en catálogo, Info y log. No se ejecutan scripts ni se añaden excepciones.
- Installer, innosetup y psmodule siguen bloqueados. Algunos componentes pueden
  necesitar preparación omitida y fallar al arrancar; persist continúa siendo genérico.
- Validación: 70 pruebas correctas y doctor correcto; extras/64gram del catálogo
  local clasifica como Compatible. No se ha instalado ni probado su arranque.

## Proxy, estado de red e interceptor de aplicaciones

- Configuración de proxy y portal POST en config.properties, desactivada por defecto.
- Indicador de Inicio con comprobación breve; diálogo Textual cancelable y contraseña
  oculta. Accesos existentes usan un diálogo Windows; cancelar lanza la aplicación.
- Credenciales sólo en memoria, sin argumentos/logs/archivos. POST no sigue redirects,
  formulario mismo origen, cookies efímeras y comprobación final con sesión nueva.
- Descargas y entorno de hijos usan el proxy; EAP sólo consultado, no modificado.
- Límite: portales que autorizan la red del equipo; no HTTP 407/NTLM/SSO/CAPTCHA.
  El primer CPython admite proxy y, desde la detección del bootstrap, login de portal
  en consola (ver sección siguiente). No se ha probado contra un portal corporativo real.
- Validación: 70 pruebas correctas, prueba específica posterior de cancelar y
  autenticar en el diálogo, doctor correcto y comprobación real de Internet accesible.
  Portal HTTP local simulado verifica POST/campos ocultos, rechazo de redirecciones
  como prueba de Internet y ausencia de credenciales en archivos.
- proxy_authentication_url guardada por el diagnóstico es una redirección con token de
  sesión (p. ej. magic) que caduca: el portal responde 400 y el login fallaba sin POST.
  Ahora cada intento pide la URL de comprobación por HTTP y usa la redirección que
  aterriza en el mismo origen que la URL configurada (hasta 5 saltos); si no la hay,
  usa la guardada. La página del portal sigue redirecciones del mismo origen. El
  formulario y su action se resuelven respecto a la página final.
- Fallos de login quedan en logs (network.authenticate) con estado HTTP y origen,
  sin URL, token ni credenciales. Regresiones en core/tests/test_network.py.
  Pendiente: validación del usuario contra el portal corporativo real.
- El botón INTERNET se recomprueba con set_interval cada network.poll_seconds (20 por
  defecto, mínimo 5, 0 desactiva; leído al montar la UI). Sólo cambia etiqueta y variant
  del botón cuando difieren (error = rojo sin Internet), sin refrescar la interfaz.
  La comprobación periódica no diagnostica, no abre diálogos, no registra avisos y se
  omite mientras hay otro worker de red o un diálogo abierto. Regresión en test_ui.py.
- Rendimiento de shims (git en pestaña cmd tardaba ~4,5 s): cada `mito run` recreaba
  todas las junctions persist con un powershell.exe por entrada (~3,4 s) y reintentaba
  hasta 20 eventos PostHog pendientes, esperando su flush al salir (tras el proxy
  corporativo PostHog responde 403 y la cola nunca se vacía). Ahora persistence.prepare
  omite enlaces intactos (junction con el mismo destino o hard link al mismo archivo) y
  los rehace si cambian (reubicación), y resume() sólo corre en el gestor (ui). Medido:
  git en el entorno del profile ~0,47 s. Regresiones en test_isolation.py y test_cli.py.
- Portal sin autenticar: las descargas siguen redirecciones y guardaban su página HTML,
  visible como «Hash incorrecto» o «Expecting value» en manifiestos y en la comprobación
  de MITO. transfer.download (con o sin hash) y self_update.check_remote detectan ahora
  una página web (marcado HTML al inicio, o text/html que empieza por «<») cuando no se
  pidió un .htm/.html, la descartan y avisan de portal o bloqueo del proxy con el host
  final. Un hash realmente distinto deja en el log download.hash con host, bytes,
  Content-Type y algoritmo. Mensaje nuevo en los 24 idiomas. Regresiones en
  test_transfer.py. Límite: no autentica ni reintenta solo; el usuario pulsa INTERNET.
- Instalación lenta con EDR corporativo: corretto8-jdk (729 archivos, ~100 MB) tardó
  134 s en copy-payload frente a 4 s de extracción; renombrar copias de archivos ya
  conocidos cuesta ~7 ms por archivo, así que el coste es el antivirus esperando por
  cada rename de binarios nuevos. engine.move_whole publica ahora el árbol extraído con
  un único rename de carpeta si el destino no existe o está vacío; si hay contenido que
  fusionar o el rename falla, sigue la fusión por archivo. Regresión en
  test_move_whole.py. Pendiente: medir el efecto real en la próxima instalación grande
  (el antivirus puede seguir escaneando al validar o en el primer arranque).
- Tras move_whole, pdfcraft (26 archivos) siguió tardando 5,7 s en copy-payload (~0,2 s
  por archivo): justo tras extraer, el antivirus retiene los archivos nuevos y Windows
  deniega el rename de carpeta, mientras que la publicación final (mismo tipo de rename,
  segundos después) tarda milisegundos. move_whole reintenta ahora el rename ante
  PermissionError con espera exponencial (0,25 s a 2 s) durante min(30 s, 0,15 s por
  archivo), menos de lo que costaría la fusión por archivo, y después cae a ella.
  Cada instalación registra install.move-whole (moved/per-file, archivos, intentos,
  motivo) en logs/mito.log para verificar el efecto. No verificado aún en real.
- Medido con graalvm-oracle-25jdk (566 archivos): move_whole movió al primer intento,
  pero el rename quedó bloqueado 58 s (~0,1 s por archivo, frente a ~0,2 s por archivo
  en la fusión). El antivirus no deniega el rename: lo retiene hasta analizar todo, y
  ese coste no es evitable desde MITO. Para que no parezca colgado, el motor emite
  engine.LongStep (línea de log con active=True/False) alrededor de copy-payload; la
  UI abre LongStepDialog centrado si dura más de 2 s, con tiempo transcurrido, sin
  botones y sin mencionar el antivirus, y lo cierra al llegar la línea final (también
  si hay error). La CLI sólo imprime las líneas. Test en test_ui.py.
- Proxy corporativo con inspección TLS (CA «MINISTERIO DE DEFENSA / NI-NAVEGACION»)
  sirve para temurin8-jdk 8u512-b09 (ZIP) un XML BlockList de Azure de 1867 bytes en
  lugar de 106 490 535 bytes, también en navegador y curl; otros assets del mismo
  release llegan bien. Manifiesto correcto (sha256 igual al digest de GitHub). No se
  añade excepción: el hash lo bloquea. Errores de integridad (hash o página web) son
  DownloadIntegrityError: el mensaje incluye URL pedida sin query ni fragmento, bytes
  recibidos y host final. La UI lo explica en DownloadIntegrityDialog, un diálogo modal
  centrado que culpa al proxy/antivirus (o al portal si llegó HTML), indica que no se ha
  instalado nada y deja URL, bytes y host en un TextArea de sólo lectura para copiarlos.
  Regresión en test_ui.py.

## Suite: duración y fallos al ejecutarla desde una pestaña

- Primera ejecución completa vía `mito release`: 377 pruebas en 381 s, 1 fallo y
  1 error, ambos de las pruebas: el fixture de remove-in-use no tenía archivos, y
  test_verified_install exigía MITO_PROFILE ausente (la pestaña ya la define). Ahora
  se crea el archivo bloqueado y se comprueba que el motor no cambia os.environ.
- IsolatedAsyncioTestCase crea su bucle con asyncio en modo depuración (verificado:
  get_debug() == True). Las 7 clases de UI heredan de tests/async_support.AsyncTestCase,
  que lo desactiva en asyncSetUp. Segunda ejecución: 219 s (antes 381 s), pero 4
  pruebas de test_ui fallaron: comprobaban tras un único pilot.pause() resultados que
  procesa un worker o un montaje asíncrono, y sólo pasaban por la lentitud del modo
  depuración. async_support.settle (pausa, workers, pausa) sustituye esas esperas.
- Pendiente: tercera ejecución y `--durations` en el equipo de trabajo, donde el
  antivirus encarece cada proceso PowerShell y cada archivo temporal de las pruebas.

## Git de release desde pestañas de MITO

- `mito release` daba WinError 2: publish.git ejecutaba 'git' sin shell y en una
  pestaña sólo existe el shim git.cmd. Ahora usa pocketools.git_executable (host o
  bin de un componente instalado) y añade la última línea de stderr al error.
- Red corporativa: Git del componente necesita http.sslBackend=schannel y
  schannelCheckRevoke=false para github.com en home/.gitconfig (CRL bloqueadas).
- Pendiente: `mito release` real y test_publish, no ejecutados.

## Directorio de trabajo de los comandos de consola

- Fallo real: `git init` tecleado en C:\mito-main desde una pestaña creó el repositorio
  en workspaces\default. Engine.launch usaba cwd=workspace para todo, también para
  `run --bin` de los shims. Afectaba a cualquier comando de cualquier componente.
- Ahora `--bin` hereda os.getcwd() (fallback al workspace); aplicaciones, accesos,
  pestañas y MCP conservan el workspace. test_engine actualizado al nuevo contrato.
- Pendiente: comprobar en una pestaña `cd /d C:\otro && git init` y la suite
  test_engine, no ejecutadas.

## Rendimiento de instalación con antivirus y lock de comprobaciones

- main/git 2.56.1 (9622 archivos) en el equipo de trabajo: descarga 3 s, extracción
  19 s, copia al payload 13 s y 79 s entre «Validando» e «Instalado». La validación
  es trivial; el tiempo era el rename del payload, retenido por el antivirus hasta
  analizar los archivos nuevos (medido: rename de una copia recién escrita 6,2 s,
  el mismo 5 s después 0,0 s).
- Engine._install_payload mueve con os.replace (fallback copy2) desde expanded-N, que
  es temporal y del mismo staging: la mitad de escrituras y de análisis. Fase
  «publish» en el log con aviso al usuario. El rename atómico se mantiene.
- storage.mutation: leer el byte bloqueado por otro handle lanzaba PermissionError
  (Errno 13) antes de intentar el lock. Ahora se trata como ocupado; wait>0 reintenta.
  update_checks.daily espera hasta 15 s porque la UI lanza en paralelo las
  comprobaciones de componentes y de MITO.
- Pendiente: medir de nuevo la instalación de git y regresiones de test_engine
  (move, mutation wait), no ejecutadas.

## Traslado y copia de la instancia

- core/state/instance.json guarda la raíz; relocation.check repara al arrancar en
  otra ruta: persistencias materializadas apartadas a core/staging (sin borrar),
  .lnk registrados reescritos (traslado) u olvidados (copia, la ruta anterior sigue
  siendo una instancia) y aviso de Host data/cache materializados.
- Ya portables antes del cambio: entorno, shims relativos, settings del Terminal
  (prepare en cada lanzamiento), junctions de persistencia (prepare_payload al
  construir el entorno), runtime con ._pth relativo.
- Límites: .lnk no creados por MITO y rutas absolutas guardadas por las propias
  aplicaciones no se modifican; instancias anteriores sin registro no detectan su
  primer traslado (sólo crean el registro). Una copia y su original comparten los
  accesos existentes hasta que la copia cree los suyos.
- Pendiente: prueba real copiando y moviendo una instancia con Explorador, y la
  regresión core/tests/test_relocation.py, no ejecutadas.

## Versiones concretas de componentes

- Catálogo → Versiones… (componente compatible, instalado o no): histórico Git del
  bucket (GitHub) y versión escrita a mano vía autoupdate. Retención en .mito.json
  (pinned), marcada en Inicio y liberable desde Info.
- El histórico se consulta por tandas: al abrir sólo las páginas (50 commits) necesarias
  para 20 versiones; «Cargar 20 más» amplía en segundo plano (máx. 4 páginas por tanda).
  Una versión escrita a mano que no aparezca en lo cargado se busca más atrás antes de
  recurrir a autoupdate. Tope total: 10 páginas (500 commits). Pendiente de validar en UI.
- Comprobado manualmente: ScoopInstaller/Extras tiene 78 commits de mattermost.json;
  versiones 4.2.0–6.4.0, sin 5.1.0 (salta de 5.0.4 a 5.1.1). 5.1.0 sólo vía
  autoupdate; su hash sale de la página de release de GitHub si aún lo publica.
- Límites: sólo buckets GitHub para histórico; API sin token (60 peticiones/h); no
  se emulan checkver, jsonpath/xpath ni otros modos de autoupdate.hash; el manifiesto
  actual puede no encajar con versiones antiguas; URLs retiradas fallan al descargar.
  Instalar por CLI/MCP no fija versiones; un componente retenido conserva su
  manifiesto en esos planes.
- Exportar profile escribe `apps` y, si los hay, `pinned` {bucket/app: versión} con los
  componentes retenidos. Importar resuelve esas versiones (histórico Git, después
  autoupdate) y las instala retenidas en el mismo plan; si una no se puede resolver, la
  importación falla sin crear el profile. Los no retenidos siguen el catálogo local.
  Archivos antiguos sólo con `apps` siguen siendo válidos.
- El archivo nunca lleva integraciones con el host (aplicaciones del host, «En host»,
  «Abrir con»): son estado de la instancia en core/state, no del profile.
- Los botones En host, Cache y Data muestran estado como Extensiones: ✓ enlazado,
  ✗ sin enlace. Antes «unlink» indicaba que no había enlace y se leía como una
  integración existente (p. ej. tras importar un profile en otra máquina).
- Pendiente: prueba real en la UI (Mattermost 5.1.x) y regresiones test_versions y
  test_components.test_install_version_pins_until_released_and_survives_dependents,
  no ejecutadas.

## Eliminar componentes en uso

- components.remove ejecuta check_unused sobre la instalación y sus copias por profile
  antes de moverlas a staging; un PermissionError restante (directorio actual de un
  proceso) se traduce al mismo mensaje tras restaurar lo movido. Sin cerrar procesos.
- Caso real: extras/mattermost seguía abierto en la bandeja con 9 procesos.
- Pendiente: regresión test_components.test_remove_in_use_reports_open_application_and_keeps_payload
  y comprobación real, no ejecutadas.

## Carpetas de usuario del home portable

- User Shell Folders suele declarar `%USERPROFILE%\Downloads`, etc. Con USERPROFILE
  en home, SHGetKnownFolderPath falla si la carpeta no existe; extras/mattermost 6.4.0
  abortaba con «Failed to get 'downloads' path».
- Engine.initialize crea en home Desktop, Documents, Downloads, Music, Pictures,
  Videos, Favorites, Links, Contacts, Searches, Saved Games y AppData/LocalLow,
  vacías y genéricas. Rutas absolutas del host (OneDrive) no cambian.
- Pendiente: abrir Mattermost tras el cambio y la regresión
  test_engine.test_portable_home_has_windows_known_user_folders, no ejecutadas.

## Profile default en instancias sin profiles

- El ZIP de release incluye profiles/default.json; un checkout del fuente no
  (profiles/ está en .gitignore) y dejaba Inicio sin profile ni casillas activables.
- Engine.ensure_default_profile crea `default` (workspace default, sin apps ni env)
  sólo si no existe ninguno, al arrancar mito.cmd, ui o start. No afecta a CLI, MCP
  ni tests que crean sus propios profiles. Eliminar el último profile sigue prohibido.
- Pendiente: comprobación real en la instancia del equipo de trabajo y la regresión
  test_engine.test_default_profile_only_when_instance_has_none, no ejecutadas.

## Detección de proxy en el primer arranque

- bootstrap.ps1 detecta el proxy de Windows (HTTPS_PROXY, manual, PAC, WPAD) si
  config.properties no declara http_proxy/https_proxy, sondea la comprobación de
  Internet y clasifica directa, HTTP 407 (con esquemas) o portal por redirección.
- Propone y guarda con confirmación http_proxy, https_proxy, no_proxy (desde
  ProxyOverride: `<local>` sólo como localhost/127.0.0.1/::1; comodines intermedios
  descartados) y, con portal, su URL y campos de usuario/contraseña detectados.
- Login de portal en consola antes de descargar, con contraseña SecureString,
  máximo tres intentos y las reglas de Network.authenticate.
- Límites: un solo proxy fijo aunque el PAC varíe por destino; Python no usa
  NTLM/Negotiate ni Basic 407; portales sin redirección (200 interceptado) se
  clasifican como bloqueados sin URL propuesta. -Approved usa el proxy sin guardar.
- Equipo real con PAC (proxy 10.x:80, respuesta 204 sin credenciales) consultado
  manualmente antes del cambio. Pendiente: primer arranque real con la detección y
  la regresión core/tests/test_bootstrap_proxy.py (proxy y portal locales simulados),
  no ejecutadas.

## Autoconfiguración de red desde el botón Internet

- Sin Internet y con red local, network.diagnose prueba la ruta de config.properties,
  la de Windows (GetSystemWebProxy vía PowerShell: manual, PAC, WPAD) y la directa si
  había proxy configurado. Elige por este orden: ok, portal, 407, bloqueado, inalcanzable.
- Sólo propone claves para ok o portal: proxy y no_proxy si cambia la ruta; con portal,
  URL y campos HTML (si no hay URL configurada) y enable_proxy_authentication.
  NetworkSetupDialog confirma; network.apply guarda con Configuration.
- Las credenciales del portal se piden en ProxyDialog para la sesión; nunca se guardan.
- Límites: los del bootstrap (un proxy fijo aunque el PAC varíe, sin 407/NTLM en
  Python, portales sin redirección vistos como bloqueados). Volver a la oficina tras
  guardar la conexión directa requiere pulsar el botón de nuevo.
- Pendiente: core/tests/test_network_setup.py (proxy y portal locales simulados) y
  la ajustada test_ui.test_network_status_and_cancel_dialog, no ejecutadas; ni prueba
  real contra el proxy o portal corporativo.

## Variables globales en config.properties

- env.NOMBRE=valor está disponible en todos los entornos de profiles.
- Prioridad: componentes < globales < profile. Validación reservada compartida,
  valores literales y limpieza de activación anterior; lectura en cada lanzamiento.
- Ejemplo comentado en config.properties; no se han añadido variables activas.
- Validación: 66 pruebas correctas, doctor correcto en los tres profiles y diff
  sin errores de whitespace.

## Profiles mínimos y variables de entorno

- Profiles con workspace, lista ordenada apps y env; sin schema ni manifiestos duplicados.
- Eliminados lector schema 1/2 y recuperación de inventario desde locks antiguos.
- Convertidos los profiles locales conservando selecciones; no hay migrador en runtime.
- env literal se aplica después de componentes, se limpia entre procesos de profiles
  y respeta variables reservadas del home, PATH e internas. Sin excepciones por app.
- Instalación, actualización, eliminación, accesos y shims resuelven IDs del inventario.
- El empaquetador genera un profile default mínimo; no se ha generado ninguna release.
- Validación: suite de 61 pruebas y después las 20 del motor, incluidas tres nuevas
  regresiones de env y referencias ausentes, correctas. Doctor correcto en java11,
  java21 y java25; diff sin errores de whitespace.

## Información unificada del componente

- Info sustituye a Persistencia en todas las filas de instalaciones.
- Diálogo desplazable con descripción, notas, binarios base, binarios del profile
  cuando hay aislamiento y destinos persistentes; Abrir a la derecha de cada ruta.
- Consultar no prepara copias ni crea datos. Rutas pendientes deshabilitadas;
  archivos abren su carpeta contenedora. Sin excepciones por aplicación.
- Suite completa: 61 pruebas correctas, incluida navegación y apertura de rutas
  del diálogo con Explorador simulado.

## Acceso a persistencia desde Inicio

- Botón Persistencia para cualquier instalación con persist en manifiesto efectivo.
- Una ruta existente abre directamente su carpeta; múltiples entradas o rutas
  pendientes se muestran en un diálogo desplazable con los destinos y botones.
- Respeta aliases de persist, archivos y separación por profile. No crea datos ni
  copias al consultar. Explorador abre carpetas, no ejecuta archivos persistentes.

## Omisión universal de post_install

- post_install ya no bloquea instalaciones; nunca se ejecuta y su omisión se indica
  en detalles y log. Regla general, sin IDs ni políticas específicas.
- pre_install/installer/innosetup/psmodule continúan bloqueados si no están soportados.
- La separación persist + shortcuts por profile permanece intacta.
- Reinstalación real de extras/vscode 1.136.1 completada con post_install omitido;
  instalado en el inventario sin activarlo automáticamente en ningún profile.

## Aislamiento por la combinación persist + shortcuts

- isolation.py crea copias reales y persistencia por profile sólo si el manifiesto
  efectivo declara ambos campos. Ningún nombre de componente interviene.
- Base/inventario/locks en core/apps; copias en core/profile-apps y datos separados
  dentro de home/profiles/<profile>/persist. Preparación al construir el entorno.
- PATH/env_set/lanzamiento/shims resuelven la copia del profile. Sin esa combinación,
  se mantiene el comportamiento compartido. No se ha cambiado la política de hooks.
- Desactivar conserva datos y copias. Eliminar instalación retira todas sus copias
  y conserva persist; actualizar reutiliza datos del profile con binarios nuevos.
- Datos compartidos antiguos conservados, sin migración automática. Las copias
  arrancan con defaults del paquete, sin reutilizar junctions de la base.
- Probados aislamiento real, reactivación, eliminación con persist y traslado de ruta.

## Eliminar y actualizar componentes

- Eliminar en cada fila, con confirmación e indicación de profiles afectados.
  Retira referencias y payload, conserva home/persist/workspaces y otras versiones.
- Actualizar sólo ante versión posterior del catálogo local; deshabilitado si el
  manifiesto no está soportado. Instala antes de reemplazar selecciones de profiles.
- Conserva versión anterior, añade dependencias y no activa componentes antes inactivos.
- Pruebas cubren cancelación/confirmación, botones, comparación numérica, errores de
  descarga/escritura y eliminación real con junction persistente sin borrar sus datos.

## Motor sin excepciones por componente

- Retirada la rama Node que generaba NPM_CONFIG_PREFIX/NPM_CONFIG_CACHE.
- Eliminado core/policies.json y su lectura: ningún componente recibe exenciones
  para saltarse hooks. El empaquetador ya no incluye ese archivo.
- VS Code y Node con hooks pasan a no soportados para nuevas instalaciones.
  Los componentes ya instalados se conservan y se lanzan con las reglas genéricas.
- Las particularidades del bootstrap de MITO no son adaptaciones del catálogo.

## Retirada de la separación de VS Code

- Eliminadas las ramas específicas de VS Code en engine.py y persistence.py.
- No se genera VSCODE_PORTABLE ni se añaden --new-window/workspace al comando.
  Persist data se procesa como cualquier otra entrada del manifiesto.
- Los datos anteriores de home/apps/vscode/<profile> se conservan, sin migración.
- La autorización de instalación del manifiesto revisado sigue en policies.json;
  no implica separar datos por profile ni ejecutar sus hooks PowerShell.

## Descargas HTTP

- Motor y clasificación aceptan HTTP y HTTPS, también en redirecciones entre ambos.
- La verificación SHA-256/SHA-512 permanece intacta; otros esquemas se rechazan.
- extras/eclipse-java pasa a Compatible con su manifiesto original, sin adaptación.
- Descarga HTTP con redirección y hashes comprobada mediante servidor local real.
  No se ha instalado Eclipse ni se han realizado publicaciones o commits.

## Ajuste local: alineación de componentes

- Checkbox con altura de tres líneas y contenido centrado verticalmente, también
  cuando comparte la fila con botones. Se mantienen dimensiones de filas y botones.
- Preferencia permanente: no crear releases ni hacer commit/push sin petición explícita.
- Preview de Inicio actualizado y alineación comprobada con los cuatro componentes.

## Décima iteración: catálogo y filas compactas

- Checkboxes por bucket con selección persistente para la búsqueda.
- Ranking por palabras/números y cobertura, priorizando identificador sobre descripción.
  Validado con el catálogo real: eclipse muestra extras/eclipse-*, java muestra java/*,
  java 21 lts muestra java/liberica21-*-lts-*, python y python/ muestran main/python.
- Notes se muestran literalmente debajo de la descripción en panel desplazable.
- Filas de componentes instalados reducidas a tres líneas.
- Pruebas añadidas para ranking, separadores, filtros, persistencia y notas.

## Novena iteración: publicación automática

- release incrementa el parche, ejecuta pruebas, crea y sube el commit de versión,
  y publica ZIP mínimo + SHA-256 en danielgube/mito. --local sólo empaqueta.
- Credencial de Git Credential Manager comprobada con permiso push en el repositorio.
- Borrador antes de subir assets, verificación de hashes y publicación final.
  Reanudación mediante estado local; no vuelve a incrementar la versión pendiente.
- Se exige main limpio y sincronizado. Sin force push ni sobrescritura de assets.

## Octava iteración: versión automática y distribución mínima

- mito.cmd release ya no recibe un número de versión: lee __version__ del código
  empaquetado mediante AST, valida mayor.menor.parche y no ejecuta ese archivo.
- ZIP de 151.325 bytes (antes 2.690.982): código Python, ICO, scripts/lock/políticas
  de bootstrap, configuración pública, metadata y profile default vacío.
  Excluye documentación, originales gráficos, previews, runtimes y datos locales.
- Artefacto regenerado en releases/mito-0.1.0-windows-x64.zip con SHA-256.
  El anterior se conserva en core/state/previous-release.
- 37 pruebas correctas. Bootstrap probado desde ese ZIP en una carpeta vacía
  core/staging/release-minimal-check: todas las herramientas descargadas y doctor OK.
- Repositorio origin verificado: https://github.com/danielgube/mito.git.
  Estos cambios y el ZIP se han preparado localmente; no se ha publicado una release.

## Séptima iteración: gestión de profiles y releases

- «Acceso directo» sustituye Abrir terminal debajo de Crear profile. Reutiliza el
  diálogo de cuatro caracteres y genera un enlace al gestor MITO en el escritorio.
  start --profile transmite la selección a ui --profile en la pestaña MITO.
- Eliminar profile conserva componentes, home y workspace, archiva su JSON y
  selecciona otro. El último está protegido tanto en el motor como en la interfaz.
- release VERSION [--output RUTA] genera ZIP limpio + SHA-256, sin sobrescribir,
  con programa, branding, bootstrap, buckets públicos y profile default vacío.
- Generada releases/mito-0.1.0-windows-x64.zip. Bootstrap desde ese ZIP probado en
  core/staging/release-0.1.0-check: CPython, Pillow, Textual, 7-Zip y Windows Terminal
  descargados correctamente; doctor OK con un profile default y catálogo vacío.
- 36 pruebas correctas y doctor de la instancia original OK. Enlace Windows real
  comprobado en core/state/shortcut-check con java21 y etiqueta J21. Preview actualizado.
- Los accesos son al gestor con selección inicial, no a una consola de profile.

## Identidad corporativa y barra de tareas

- Nuevo maestro de icono frontal y vectorial: verde cotorra intenso, blanco puro,
  pico naranja y ojos azul eléctrico. Se reduce el detalle para conservar lectura
  a 16–32 píxeles.
- `prepare` sincroniza el ICO de MITO con los dos iconos de contraste externos del
  Windows Terminal privado. El bootstrap realiza la misma sincronización antes de
  publicar su marcador, por lo que el primer arranque de una release ya está
  personalizado. Además genera `YitoTerminal.exe` con el recurso PE de `mito.ico` y
  lo usa para lanzar ventanas sin acceso de profile. El ejecutable Microsoft original
  permanece firmado e intacto; la copia derivada es local y no firmada. Se regenera
  por hashes del original y del ICO, sin procesos auxiliares.
- El acceso del gestor apunta directamente a Windows Terminal para que Windows pueda
  asociar la ventana con el acceso y su icono. `mito.cmd` continúa siendo CMD; el
  icono visible se obtiene mediante el acceso `.lnk`, sin añadir un launcher EXE.

## Sexta iteración: diálogo de etiqueta e icono transparente

- Se retira el campo de etiqueta del menú. Generar acceso directo abre un modal
  con MITO por defecto, máximo 4 caracteres, validación, Generar y Cancelar/Escape.
  Se mantiene el profile y la instalación seleccionados al abrir el diálogo.
- Icono de cotorra mejorado y encuadrado para tamaños pequeños: mito_icon.png RGBA
  y mito.ico en nueve tamaños de 16 a 256. Original conservado en mito-original.ico.
- Edición mediante image_gen integrado; el resultado traía un damero RGB, corregido
  mediante extracción del fondo exterior con Pillow. Alpha real comprobado y
  composición inspeccionada sobre fondo oscuro a 16/32/48/64/256 píxeles.
- 32 pruebas correctas, incluyendo cancelación, validación y etiqueta personalizada.
  Preview de Inicio y del diálogo actualizados en core/state.

## Quinta iteración: sólo accesos de aplicaciones

- Eliminados el botón, el comando shortcuts y la generación de accesos de terminal/profile.
  Retirados los dos enlaces registrados de la raíz. Los profiles se abren en pestañas.
- Crear un profile actualiza el menú del Terminal, sin publicar enlaces.
- Los accesos de apps siguen siendo explícitos desde MIS COMPONENTES al escritorio,
  con profile e instalación fijados. Abrir y generar sólo aparecen en apps activas.
- icons.py adapta el compositor de EAP: lee el ICO o los recursos EXE/DLL como datos,
  conserva las resoluciones y superpone la etiqueta inferior derecha con Pillow.
  Hasta 4 letras/números, MITO por defecto; ICO de MITO como alternativa.
- Validación: 32 pruebas y doctor correctos. ICO real de VS Code verificado en
  ocho tamaños (16–256 px), composición inspeccionada y enlace nativo comprobado
  en core/state/shortcut-check, sin escribir en el escritorio personal.
- Preview de Inicio actualizado en core/state/mito-preview.svg y ejemplo de icono
  en core/state/vscode-shortcut-preview.png. Las iteraciones siguientes son históricas
  y sus decisiones de accesos de profile quedan sustituidas por esta sección.

## Cuarta iteración: maximizado y separación de accesos

- Se sustituye pantalla completa por maximizado en flags y ajustes del Terminal.
- Raíz: sólo accesos de profile. Eliminados los accesos automáticos de aplicaciones
  registrados por la versión anterior; activar e instalar ya no los regeneran.
- Sidebar: «Acceso directo profile» y etiqueta de iconos (máximo 4, defecto MITO).
- MIS COMPONENTES: Abrir y Generar acceso directo únicamente si está activo y el
  manifiesto declara un ejecutable disponible. No aparecen acciones en los Java.
- Acceso de app explícito al escritorio de Windows, con profile e instalación
  actuales. Icono de app extraído si existe y etiqueta compuesta con Pillow.
- El acceso de profile sigue siendo un atajo; no limita el menú de otras pestañas.
- Validación: 31 pruebas, doctor y creación nativa de un .lnk en un escritorio
  de prueba, comprobando destino, profile, instalación e icono Pillow. No se han
  creado accesos de prueba en el escritorio personal del usuario.

## Tercera iteración: identidad y pestañas

- Nombre completo: My Integrated Terminal Orchestrator.
- Logo renombrado y trasladado a core/assets/branding/mito_logo.png. JPG original
  conservado junto al ICO multirresolución y su PNG de vista previa.
- Los iconos de profiles derivan del ICO del loro, con una etiqueta del profile.
- Terminal a pantalla completa, pestañas visibles, ventana reutilizada por raíz,
  colores verde/azul/ámbar, opacidad 94 % y acrílico.
- Menú MITO + PowerShell y CMD por profile. Cada pestaña carga el entorno actual;
  no hay sincronización en caliente de procesos ni servicios auxiliares.
- La UI muestra el acrónimo expandido y avisa de abrir una pestaña nueva para
  cargar los cambios de entorno. La paleta de la interfaz sigue el verde del logo.
- Verificadas pestañas reales PowerShell(java11) y CMD(java21): JAVA_HOME y
  WT_SESSION correctos. Evidencias en core/state/branding-*-proof.json. La política
  global de PowerShell permanece intacta; sólo el script local de comprobación
  utilizó Bypass en su proceso de prueba. Las pestañas interactivas usan NoProfile.
- ICO base y derivados validados en varios tamaños; SVG de Inicio y catálogo
  regenerados. Los ajustes de Terminal se prueban con GUIDs estables, recarga del
  menú, colores, transparencia y selección de shell.

## Segunda iteración: Inicio, activación y Terminal

- Inicio ahora muestra todas las instalaciones, con checkbox independiente por
  profile. Catálogo/búsqueda/instalación vive en una pantalla distinta.
- Schema 2 separa inventario y activación y admite múltiples versiones del mismo
  paquete. Se eliminan las restricciones por JAVA_HOME o comandos repetidos.
- Windows Terminal 1.24.11911.0 y 7-Zip 26.02 se incorporan al bootstrap verificado.
  Las pruebas reales abren sesiones WT distintas y comprueban JAVA_HOME de java11
  y java21 desde el proceso hijo dentro de la pestaña.
- Sólo .lnk de profile/app en la raíz. Los antiguos .cmd generados se conservan en
  core/state/legacy-shortcuts. mito.cmd sigue como entrada general y abre la TUI
  dentro del Terminal privado; `mito.cmd ui` ejecuta la TUI en la terminal actual.
- Extractores ZIP, 7z, TAR comprimido y archivos sueltos; persistencia de carpetas
  y archivos con reconstrucción de enlaces tras traslado; argumentos de bin y
  shortcuts. Políticas revisadas de Node/Node LTS además de VS Code.
- Pruebas reales en core/staging/motor-smoke: Node 24.20.0 desde 7z, npm prefix
  bajo home, Maven 3.9.16 con persist y jq 1.8.2 desde .exe suelto.
- 2.718 de 4.959 manifiestos encajan en el motor actual. Hooks arbitrarios,
  MSI/Inno y módulos PowerShell no están implementados de forma general.
- Biblioteca real: Java 11, 21, 25 y VS Code. Java 25 se instala sin activarse.
- Las capturas vigentes son core/state/mito-preview.svg (Inicio) y
  core/state/mito-catalog-preview.svg (catálogo).
- Validación final: 23 pruebas automatizadas y doctor; bootstrap completo desde
  cero en core/staging/bootstrap-v2-smoke. La prueba del terminal escribe evidencias
  en core/state/terminal-java11-proof.json y terminal-java21-proof.json, con
  WT_SESSION distintos y JAVA_HOME correcto.

La sección siguiente conserva el estado histórico de la primera iteración;
las decisiones anteriores que contradigan esta sección están sustituidas.

MITO 0.1 es una primera base ejecutable creada en C:\mito a partir de las ideas
de EAP. EAP y los repositorios locales de Scoop se han consultado sin modificarlos.
Los archivos vacíos originales `config.properties.txt` y `mito.cmd.txt` se han
conservado; los puntos de entrada reales no tienen la extensión `.txt`.

## Completado

- Bootstrap privado reproducible: CPython 3.14.7, Textual 8.2.8, Pillow 12.3.0 y
  cierre de dependencias fijado por URL y SHA-256.
- Bootstrap probado desde una carpeta sin runtime y sin copiar las wheels.
  El caso de prueba permanece en `core/staging/bootstrap-smoke`, ignorado por Git.
- CLI, interfaz Textual, búsqueda de catálogo, creación de profiles, instalación,
  terminal, lanzamiento de apps y accesos `.cmd` / `.lnk` con iconos Pillow.
- Importados Main (1.641), Extras (2.380), Versions (602) desde C:\scoop y
  Java (336) desde el repositorio oficial: 4.959 manifiestos navegables.
- 1.655 manifiestos clasificados como compatibles por el analizador actual.
  Es una clasificación sintáctica/funcional del motor, no 1.655 apps probadas.
- Profiles `java11` y `java21` creados con Java 11.0.32.1 y Java 21.0.12.1 reales.
- Ambos profiles comparten exactamente el payload de VS Code 1.136.1.
- `java -version` y la CLI de VS Code ejecutados con el entorno de ambos profiles.
- Arranque headless de Textual sobre el catálogo real de 4.959 entradas.
  Vista SVG de diagnóstico: `core/state/mito-preview.svg`.

## Validación

La suite cubre rutas Windows, ZIP traversal, hashes, fallo sin publicación de
profile, arquitectura, hooks rechazados, dependencias circulares o incompatibles,
variables duplicadas, movimiento de raíz, entorno sin apilar profiles, catálogos
ambiguos, locks alterados, aislamiento de datos de VS Code y búsqueda Textual.

Se ha probado el motor y las CLI reales; no se ha automatizado todavía una sesión
gráfica completa con terminal integrada de VS Code. El aislamiento se implementa
con directorios VSCODE_PORTABLE distintos, verificados por pruebas de entorno.

## Siguiente desarrollo

1. Ampliar extracción a 7z/tar y persistencia genérica con rutas que sobrevivan
   al traslado. No resolverlo ejecutando scripts de Scoop sin adaptar.
2. Extraer la política VS Code a un contrato de adaptadores reusable con fixtures,
   manteniendo la revisión explícita al cambiar hooks/manifiestos.
3. Restaurar desde lock, quitar componentes de un profile, eliminar profiles y
   recolectar payloads sin referencias. Diseñar protección de datos para borrados.
4. Vista dedicada de componentes seleccionados y edición del workspace. El
   progreso por bytes y la cancelación cooperativa de instalaciones ya existen
   (OperationDialog); falta conectarla a la cancelación de jobs MCP.
5. Ampliar pruebas gráficas con dos VS Code simultáneos y Java de cada terminal.
6. Proxies/certificados corporativos explícitos, exportación/importación,
   actualización del core y, después, Pocketools.

No hay repositorio Git inicializado ni publicación remota creada por esta tarea.
## Color de las etiquetas de accesos

- El diálogo de accesos de MITO, componentes y aplicaciones del host muestra una
  cuadrícula de ocho colores para el fondo de las letras blancas: negro, rojo, azul,
  naranja, rosa, verde, marrón y morado. `shortcut.badgeColor` fija el predeterminado;
  `naranja` conserva la apariencia previa. CLI acepta `--color` en `shortcut` y
  `app-shortcut`. La selección se guarda con cada acceso y participa en la huella
  del ICO generado; los registros anteriores se interpretan como naranja.
- Cambio pendiente de validación interactiva. No se han ejecutado pruebas ni doctor.
- Tras comprobarse que las filas horizontales comprimían los botones, el selector
  usa un Grid de cuatro columnas y dos filas con celdas de tamaño fijo. La
  visualización corregida sigue pendiente de comprobación interactiva.
- La suite ejecutada por el usuario detectó dos fallos: `ShortcutDialog()` sin motor
  intentaba leer `engine.root`, y faltaban dos mensajes nuevos en los bundles.
  Corregida la lectura del valor por defecto sin motor y añadidos ambos mensajes a
  los 24 idiomas. Esta corrección espera una nueva ejecución de la suite por el usuario.

## Iconos al borde y Pocketools simplificado (28 de septiembre de 2026)

- Regenerados ambos ICO y sus previews, con nueve tamaños de 16 a 256 px.
  El recorte por alpha > 48 ocupa todo el eje mayor sin deformar el dibujo.
  MITO conserva su diseño y proporciones; el aumento es pequeño respecto al previo.
- Pocketools muestra una tuerca naranja grande con un pequeño «>» azul eléctrico
  en el centro, sin octágono posterior. Maestro generado con image_gen integrado.
- build_branding.py --icons-only permite regenerar los ICO sólo con Pillow,
  sin modificar maestros ni variantes promocionales. La ejecución completa conserva
  sus dependencias NumPy/Matplotlib y termina aplicando el mismo encuadre a los ICO.
- Generación realizada; no se han ejecutado tests, doctor ni la aplicación.
  Validación visual en Windows y de etiquetas superpuestas pendiente del usuario.
  Para comprobar: explorer.exe D:\mito\core\assets\branding y seleccionar iconos grandes.

## Capturas del README actualizadas a MITO (28 de septiembre de 2026)

- Regenerados docs/images/mito-inicio.svg y mito-catalogo.svg desde Textual
  con MITO 0.2.8 y «My Integrated Terminal Orchestrator». Los enlaces del README
  mantienen sus rutas. Inicio muestra los siete componentes instalados, cuatro
  activos en default; Catálogo conserva la búsqueda java 21 lts.
- docs/tools/capture_readme.py reproduce la captura a 170 × 58 celdas, en español.
  Oculta aplicaciones del host por privacidad. La captura final desactiva hooks de
  red, actualizaciones, identidad y preparación de terminal y escritura de filtros.
  Requiere poder leer metadatos de core/apps: el sandbox inicialmente los ocultó;
  la generación definitiva se hizo con permiso de lectura ampliado.
- SVG renderizados a PNG temporal e inspeccionados visualmente. No se ejecutaron
  suites ni doctor. Pendiente de aprobación visual del usuario en el README.
- Comprobación: Start-Process .\docs\images\mito-inicio.svg y
  Start-Process .\docs\images\mito-catalogo.svg desde PowerShell en la raíz.

## Capturas sin decoración de ventana (28 de septiembre de 2026)

- Eliminados de ambos SVG del README la barra de título externa, los tres
  botones tipo macOS y el borde redondeado que añadía Rich. Se conserva únicamente
  la interfaz de MITO, incluida su propia cabecera, sin alterar contenido ni estado.
- capture_readme.py aplica el mismo recorte en futuras exportaciones: conserva
  estilos, definiciones y grupo de terminal y ajusta el viewBox a ese contenido.
- Modificados directamente los SVG existentes, sin volver a ejecutar MITO.
  Revisados mediante renderizado PNG; aprobación visual del usuario pendiente.
  Comprobación: Start-Process .\docs\images\mito-inicio.svg y
  Start-Process .\docs\images\mito-catalogo.svg.

## Datos en host: junctions inversas hacia AppData de MITO (19 de noviembre de 2026)

- Nuevo host_exposure.py y botón «En host · link/unlink» en cada componente con
  ejecutable declarado. Crea junctions desde %APPDATA%/%LOCALAPPDATA% reales del
  host hacia home/AppData de MITO, para que una app arrancada por el host (inicio
  automático de Windows, asociaciones, otro programa) use los mismos datos.
  Caso motivador: Mattermost con «arrancar con el sistema» abría sin datos.
- Genérico: candidatos por nombre de los ejecutables de shortcuts/bin en
  home/AppData/{Roaming,Local}, sin reglas por producto. Sin candidatos se indica
  que hay que abrir antes la aplicación desde MITO para que cree sus datos.
- Carpeta existente del host: nunca se borra; con confirmación se aparta como
  <nombre>.mito-backup-<fecha> y se restaura al desenlazar. Registro en
  core/state/host-exposed-links.json. Eliminar el componente retira las junctions.
  relocation las reapunta tras mover la instancia y las abandona tras copiarla.
- Límites: sólo datos; al arrancar fuera de MITO no hay PATH, variables, workspace
  ni dependencias del profile. Las apps que dependan de Java u otros runtimes de
  MITO no funcionarán así. Ejecutar a la vez la copia del host y la de MITO depende
  del bloqueo de instancia única de cada app. Hay que cerrar la app antes de
  enlazar o desenlazar. Es la única escritura de MITO en el AppData del host y es
  explícita. Las rutas son absolutas: al llevar la instancia a otro PC hay que
  volver a enlazar.
- Mensajes nuevos traducidos en los 24 catálogos. Regresiones añadidas en
  core/tests/test_host_exposure.py (junctions reales en carpetas temporales).
  No se han ejecutado tests ni doctor: validación pendiente del usuario.

## «Abrir con» para componentes de MITO (19 de noviembre de 2026)

- Nuevo open_with.py y botón «Abrir con · link/unlink» en componentes activos con
  ejecutable. Registra en HKCU un ProgID MITO.<hash> por profile y componente
  cuyo comando es `mito run`, y lo añade a OpenWithProgids de las extensiones que
  indica el usuario. Aparece en «Abrir con» del menú contextual con el entorno,
  workspace y copia por profile. No cambia la aplicación predeterminada: si se
  quiere, el usuario la elige en el diálogo de Windows con «Usar siempre».
- Registro en core/state/open-with.json. Se retira al pulsar unlink, al eliminar
  el componente o el profile; relocation reescribe comandos tras mover la
  instancia y abandona las entradas tras copiarla.
- Límites: el arranque pasa por pythonw y tarda algo más que el ejecutable
  directo. Que Windows muestre el nombre y el icono de las claves Application en
  lugar de «Python» está pendiente de comprobar en este equipo. Desactivar el
  componente no retira la entrada; al usarla, el lanzador da error. Las entradas
  apuntan a la ruta absoluta de la instancia. Cortex XDR puede vigilar escrituras
  en HKCU\Software\Classes.
- Mensajes nuevos traducidos en los 24 catálogos. Regresiones en
  core/tests/test_open_with.py sobre una rama temporal de HKCU. No se han
  ejecutado tests ni doctor: validación pendiente del usuario.
- Corrección: el botón «Abrir con» abre siempre el diálogo de gestión, también
  con extensiones ya registradas (antes sólo permitía quitar). Muestra una tabla
  por entrada, un desplegable de extensiones habituales y texto libre; se puede
  añadir y quitar antes de Guardar. Guardar sin extensiones retira la entrada.
  open_with.apply aplica sólo los cambios. Regresiones de apply y del diálogo
  Textual añadidas en test_open_with.py, sin ejecutar.
- Presets en el diálogo «Abrir con»: Word, Excel, PowerPoint, PDF / eBook, 7-Zip,
  VLC, IrfanView y Notepad++ añaden de una vez sus extensiones habituales, sin
  duplicar las ya presentes. Son sugerencias de la UI (open_with.PRESETS), no
  políticas del motor; se combinan con la elección suelta y el texto libre.
  Regresión del diálogo y validez de todos los presets añadida, sin ejecutar.
- El botón de la fila pasa de «Abrir con · link/unlink» a «Extensiones ✓» (con
  extensiones asociadas en el profile) o «Extensiones ✗» (sin ellas), con ancho
  propio de 20 columnas para las traducciones largas. El diálogo mantiene el título
  «Abrir con» porque describe el menú de Windows donde aparecen.
- Corrección de las regresiones de «Abrir con» tras la ejecución del usuario
  (400 pruebas, 3 fallos y 1 error, todos en test_open_with.py). Los valores de
  OpenWithProgids son REG_NONE vacíos y winreg devuelve None como dato aunque
  existan: el test ahora comprueba el tipo, sin cambiar el código. El diálogo no
  cabía en el terminal de 80×24 de run_test: ahora es desplazable (max-height 100%)
  y los tests usan 120×50 y Button.press. Pendiente de repetir la ejecución.
## «Protocolos»: esquemas URL reclamados por profile (24 de noviembre de 2026)

- Problema: un inicio de sesión en el navegador que vuelve por un esquema propio
  (claude://, vscode://…) llega a la copia del host, porque Windows ejecuta el
  comando de HKCU/HKLM\Software\Classes\<esquema>. Caso observado: Claude del host
  (Squirrel, por usuario) registra `…\AnthropicClaude\app-<versión>\claude.exe -- "%1"`
  en HKCU y lo reescribe al arrancar, como hacen las apps Electron.
- Nuevo protocols.py, protocols_ui.py y botón «Protocolos ✓/✗» junto a
  «Extensiones» en componentes activos con ejecutable. El diálogo muestra cada
  esquema con su comando actual y estado (De este profile, Otro profile de MITO,
  Ejecutable de MITO sin entorno, Otra app, No registrado). Candidatos genéricos
  por nombre de ejecutable declarado; también se pueden escribir a mano.
- Reclamar escribe la clave en HKCU con `pythonw -m mito run --profile … --app …
  -- "%1"`, nunca el ejecutable directo, y guarda en core/state/protocols.json una
  copia del árbol previo. Sin resincronización automática: si otra app lo recupera,
  Guardar con el esquema marcado lo vuelve a reclamar y refresca la copia.
- Liberar (desmarcar, desactivar o eliminar el componente, eliminar el profile)
  restaura la copia sólo si la clave sigue con el comando de MITO. A diferencia de
  «Abrir con», desactivar sí libera: el esquema es exclusivo y bloquearía al host.
  relocation reescribe el comando tras mover y lo abandona tras copiar.
- AGENTS.md: excepción explícita a «no tocar ProgIDs ajenos» sólo para esta acción.
- Límites: un esquema pertenece a un solo profile. Mientras esté reclamado, los
  enlaces abren MITO aunque se use la app del host, hasta que esta lo recupere al
  arrancar. Que la instancia abierta reciba la URL depende de la instancia única de
  la app (Electron la reenvía si coincide la carpeta de datos); sin probar con
  Claude. Si la app de MITO se registra a sí misma, el diálogo lo muestra como
  «Ejecutable de MITO sin entorno» y hay que volver a reclamarlo. Esquemas sólo
  en HKLM: se crea la clave en HKCU y al liberar se borra, con lo que vuelve a
  regir HKLM. El escaneo de candidatos recorre las clases de HKCU y HKLM al abrir
  el diálogo.
- Mensajes traducidos en los 24 catálogos. Regresiones en core/tests/test_protocols.py
  sobre una rama temporal de HKCU, sin ejecutar: validación pendiente del usuario.
- Ajuste tras la primera prueba del usuario: la Claude de MITO (extras/claude) se
  había registrado sola en claude:// apuntando a core\apps\extras\claude\claude.exe
  (estado «Ejecutable de MITO sin entorno»), por lo que el enlace arrancaba el
  binario con el entorno del host. Las filas en ese estado se proponen marcadas y
  la ayuda indica el orden: abrir la app desde MITO y reclamar después, porque la
  app se vuelve a registrar al arrancar.
- Botones más cortos en las filas: «Generar acceso directo» pasa a «Acc. directo»
  (16 columnas) y «En host · ✓/✗» a «Host · ✓/✗» (12 columnas, como Cache/Data).
- Rediseño tras la prueba del usuario (funcionó al reclamar con la Claude de MITO
  ya abierta, pero el ✓ y la resincronización manual confundían): el botón pasa a
  «Protocolos» sin marca. El diálogo tiene un botón Reclamar/Liberar por esquema;
  mientras está abierto, protocols.Session comprueba cada 5 s y recupera el
  esquema si otra app lo sobrescribe («Recuperado a las HH:MM:SS»), conservando
  la primera copia. Cerrar el diálogo libera y restaura lo anterior. Se retiran
  la marca de filas «sin entorno», apply, relocate y los enganches al desactivar,
  eliminar componente/profile y trasladar. Un reclamo que quedó por cerrar MITO
  con el diálogo abierto se libera al volver a abrir el diálogo.
- Límite: hay que dejar el diálogo abierto hasta aceptar el enlace en el
  navegador; si se cierra antes, el enlace vuelve a la app que estuviera antes.
  Regresiones de Session y del diálogo (con un doble) actualizadas, sin ejecutar.

## Descubrimiento de aplicaciones del host (24 de noviembre de 2026)

- «Agregar aplicación del host» ya no abre directamente el selector de archivos:
  muestra las aplicaciones encontradas en los accesos .lnk del menú Inicio y del
  escritorio del host (usuario y comunes), con filtro de texto, Agregar y
  Examinar… (el selector nativo de siempre).
- host_apps.discover es genérico y ligero: rutas por SHGetFolderPath (APPDATA
  puede ser el de MITO), destinos resueltos en una sola llamada a PowerShell
  (relocation.link_targets), sólo .exe/.bat/.cmd/.ps1 existentes, fuera de la
  raíz de MITO, sin desinstaladores (unins*, *uninstall*, *desinstal*) ni apps ya
  agregadas, deduplicado por ruta. No lee el registro ni recorre Program Files.
- Límites: apps sin acceso en Inicio/escritorio (portables, MSIX/Store, que usan
  accesos sin TargetPath de archivo) no aparecen y se agregan con Examinar. La
  resolución tarda lo que tarde PowerShell con muchos accesos; corre en el worker.
- Regresiones en core/tests/test_host_apps.py (HostDiscoveryTests) con carpetas y
  resolución simuladas, sin ejecutar. Textos traducidos en los 24 catálogos.
- Corrección: la suite de `mito release` se colgaba. test_ui y test_host_data_ui
  pulsan «Agregar aplicación del host» con el selector simulado, pero el worker
  abría el diálogo de descubrimiento con los accesos reales y esperaba sin fin
  (además de una F en test_host_data_ui). Ahora, si no se descubre nada, se va
  directo al selector nativo, y esos tests simulan discover con []. Test del
  diálogo (filtro, Agregar, Examinar) añadido en test_host_apps.py, sin ejecutar.
