# Desarrollo de MITO

- No crear releases ni hacer git commit o push salvo petición explícita del usuario.

MITO significa My Integrated Terminal Orchestrator. Los recursos de identidad
viven en core/assets/branding; los iconos de apps superponen letras al ICO con Pillow.
Windows Terminal usa una ventana por instancia, maximizada y un menú con
MITO, PowerShell y CMD por profile. El entorno se reconstruye al abrir pestañas;
no añadir sincronización en caliente ni procesos auxiliares sin necesidad.

Lee README.md, spec/ARCHITECTURE.md y spec/CONTINUITY.md antes de modificar contratos.

- Windows x64. Una instancia y un solo home; múltiples homes significan otras rutas.
- Las descargas y redirecciones admiten HTTP y HTTPS; verificar hashes cuando se declaren.
  Admitir MD5/SHA-1 y hash ausente, vacío, null o skip (sin verificación).
  Instalar/actualizar sin hash requiere confirmación previa, incluidas dependencias.
- No añadir excepciones por aplicación en el motor para separar estados o ventanas.
  VS Code usa activación, lanzamiento y persistencia genéricos.
- Motor Python propio. Scoop aporta manifiestos, nunca una instalación global.
- El bootstrap contiene CPython, Pillow, Textual, 7-Zip, innounp y Windows Terminal privados con hashes fijados.
- innosetup: true extrae los .exe con innounp privado, sin ejecutar el instalador.
  Publicar sólo {app}, normalizado a la raíz, con inventario, rutas y tamaños verificados.
  Recuperar nombres declarados ausentes sólo si sus variantes numeradas son idénticas.
- Abrir y Acceso directo permiten elegir entre entradas declaradas disponibles,
  sin exigir persistencia. Conservar la etiqueta seleccionada al lanzar y en el acceso.
  CHM usa hh.exe de Windows con el entorno del profile.
- Ejecutar pre_install con PowerShell sin perfil, después de extraer y antes de publicar.
  Después ejecutar installer.script (texto o lista) o installer.file (.ps1 relativo,
  args como datos), con el mismo contexto y helpers, antes de validar/publicar.
  No combinar ambas formas; otros campos de installer siguen bloqueados.
  Es código de confianza del catálogo, no un sandbox. No importar código Python de catálogos.
  post_install se omite y se indica en detalles/log. Los demás pasos no soportados siguen bloqueados.
  Añadir sólo helpers genéricos; nunca excepciones por componente.
- No añadir adaptadores, listas de excepciones ni políticas por producto. Mejorar
  capacidades genéricas del manifiesto; si falta un paso, marcarlo no soportado.
- Regla genérica: persist + shortcuts implica copia de binarios y persist por profile.
  Sin esa combinación se comparten. Base en core/apps, copias en core/profile-apps,
  datos en home/profiles/<profile>/persist. Sin excepciones por nombre.
- Locks relativos y activación por procesos hijos.
- No generar accesos de terminal ni enlaces en la raíz. El botón Acceso directo
  crea en el escritorio un acceso al gestor MITO con el profile seleccionado,
  usando el diálogo de etiqueta. Las consolas se abren mediante pestañas.
- Eliminar un profile conserva home, workspace y componentes; nunca borrar el último.
- release incrementa el parche y publica en danielgube/mito con Git Credential Manager.
  Requiere main limpio y sincronizado; reanuda publicaciones pendientes.
  --local detecta __version__ sin incrementar ni publicar. Genera un ZIP mínimo
  con código, ICO y bootstrap, sin runtimes, originales gráficos ni datos locales.
- Los accesos de apps se generan explícitamente en el escritorio desde MIS COMPONENTES,
  junto a Abrir. Ambos botones sólo aparecen para componentes activos con ejecutable
  declarado y disponible. El acceso conserva profile y bucket/app, sin fijar versión
  ni huella. Los nuevos accesos de componentes se nombran profile - bucket-app,
  sin prefijo MITO ni versión. Resolver activación y ambigüedad sigue siendo genérico.
- Las aplicaciones agregadas del host conservan su ejecutable externo, pero Abrir y
  sus accesos usan el entorno y workspace del profile de MITO, incluido APPDATA.
  Se guardan referencias en core/state; eliminarlas nunca borra el ejecutable del host.
- «En host» es la inversa explícita: junctions del AppData real del host hacia
  home/AppData de MITO para apps arrancadas por el host. Sólo datos, sin entorno;
  candidatos por nombre de ejecutable declarado; nunca borrar carpetas del host.
- Los iconos usan el compositor Pillow adaptado de EAP: ICO multirresolución y
  etiqueta superpuesta de hasta 4 caracteres, por defecto MITO.
- Inicio muestra todas las instalaciones con checkboxes de activación por profile.
  El catálogo tiene una pantalla separada. Instalar y activar son operaciones distintas.
- Un componente es bucket/app y sólo tiene una versión instalada. Actualizar lo sustituye
  en core/apps/<bucket>/<app>, con ruta estable y versión/huella sólo en metadatos.
  Los profiles activan componentes, nunca versiones. Componentes distintos pueden
  coexistir sin exclusión por JAVA_HOME, comandos o familia; no añadir radio buttons.
  persist + shortcuts mantiene copias en core/profile-apps/<profile>/<bucket>/<app>;
  actualizar sustituye también todas esas copias conservando datos y rutas.
- No modificar EAP ni los repositorios de C:\scoop durante el desarrollo de MITO.
- No escribir datos del usuario, runtime, descargas ni payloads en Git.
- Pocketools compartidas: pocketool.<nombre> configura un Git HTTP/HTTPS o carpeta local.
  Exigir pocketool.json con info; publicar en pocketools/<nombre> y añadir sólo su raíz
  al PATH de todos los profiles. Sin ejecución de scripts, dependencias ni submódulos.
- Validación interactiva: al considerar terminado un cambio, indicar al usuario
  el comando concreto para probarlo y esperar su resultado. El usuario ejecuta
  la aplicación y las pruebas. No lanzar tests, suites ni doctor automáticamente,
  ni consumir turnos esperando o consultando su progreso, salvo petición explícita.
  Añadir regresiones para invariantes del motor cuando corresponda, sin ejecutarlas
  automáticamente. No afirmar que una validación pendiente ha pasado.
  Comandos de referencia, sólo para indicar o ejecutar bajo petición explícita:
  `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -v`
  y `mito.cmd doctor`. Preferir la comprobación concreta del cambio a la suite completa.
- Documentar límites reales y estado verificable en spec/CONTINUITY.md.
