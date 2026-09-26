# Revisión de pruebas para MITO 1.0.0

Revisión del 14 de septiembre de 2026 sobre `f69bc3b` (versión 0.1.5).

La suite ofrece una base sólida para estabilizar la aplicación: comprueba los
principales contratos del motor y numerosos fallos con conservación de datos.
El número de tests por sí solo no basta para declarar lista la 1.0.0. Recomiendo
cerrar primero los huecos de prioridad alta y hacer una prueba de aceptación de
la distribución en Windows limpio. No hace falta alcanzar un número arbitrario
de pruebas ni prometer compatibilidad con todos los manifiestos de Scoop.

## Método y alcance

- Inventario de 216 métodos de prueba en 28 archivos `core/tests/test_*.py`;
  varios contienen subtests y escenarios múltiples.
- Contraste de README, arquitectura y continuidad con código y aserciones de la
  suite, distinguiendo procesos/archivos reales de sustituciones mediante mocks.
- Revisión del historial Git local hasta 0.1.5 para preparar el changelog.
- No se ha medido cobertura de líneas o ramas: estos números son un inventario,
  no un porcentaje de cobertura. Tampoco se ha ejecutado una matriz de equipos,
  versiones de Windows o catálogos públicos.
- Esta tarea analiza y documenta; no modifica el motor ni añade regresiones.

## Cobertura existente

| Área | Evidencia en `core/tests/` | Valor de la cobertura |
| --- | --- | --- |
| Instalación y dependencias | `test_engine.py`, `test_activation.py`, `test_stable_storage.py` | Hashes, ciclos, incompatibilidad antes de descargar, confirmación de dependencias sin hash, plan completo antes de sustituir, instalación separada de activación. |
| Actualización y eliminación | `test_components.py`, `test_isolation.py` | Reversión por fallos de escritura/preparación, binarios bloqueados, copias activas e inactivas, datos persistentes y otros profiles conservados. |
| Entorno y persistencia | `test_engine.py`, `test_activation.py`, `test_isolation.py`, `test_persistence_locations.py` | Prioridad de variables, ausencia de acumulación del entorno anterior, traslado de raíz, archivos y junctions reales, aislamiento declarativo. |
| Profiles y configuración | `test_profile_transfer.py`, `test_configuration.py`, `test_shortcuts.py` | Exportación sin datos, importación sin sobrescritura, último profile protegido, comentarios conservados y conflicto de edición. |
| Integración con el host | `test_host_apps.py`, `test_host_data.py`, `test_host_data_ui.py` | Entorno/workspace del profile, referencias externas, junctions reales, consentimiento para sustituir datos locales y protección de destinos ajenos. |
| Procesos y accesos | `test_processes.py`, `test_shortcuts.py`, `test_cli.py`, `test_terminal.py` | Job Objects, espera de descendientes, argumentos BAT/CMD, modo acoplado, pausa y accesos estables. Parte de Terminal comprueba comandos/configuración con lanzamiento simulado. |
| MCP | `test_mcp.py`, `test_mcp_ui.py` | SDK y HTTP reales, rechazo de Host/Origin ajenos, caída del gestor, timeout del árbol, revisión obsoleta, deduplicación, confirmación fuera del lock y límite de salida por trabajo. |
| Actualización de MITO | `test_self_update.py`, `test_update_checks.py` | Online/offline, hashes y metadatos, conservación de datos, reversión del runtime, CMD externo real sobre distribución desechable y caché diaria. |
| Catálogo y transporte | `test_search.py`, `test_catalog_performance.py`, `test_transfer.py`, `test_network.py` | Ranking, filtros, ZIP de catálogo sin extracción, HTTP local real, hashes opcionales, proxy y portal de autenticación. |
| Interfaz e idiomas | `test_ui.py`, `test_i18n.py` y archivos `*_ui.py` | Flujos Textual headless, cancelaciones, visibilidad, bundles completos y parámetros de traducción. No equivalen a inspección visual de todos los idiomas. |
| Pocketools y distribución | `test_pocketools.py`, `test_release.py`, `test_publish.py` | Copias locales reales y rechazo de enlaces; Git simulado; ZIP sin datos privados, versión leída sin ejecutar código y reanudación simulada de publicación. |

## Huecos que cerraría antes de publicar 1.0.0

Son ausencias de evidencia automatizada suficiente, no defectos demostrados.
Los casos propuestos deben usar instancias temporales y comprobar resultados
observables, evitando red pública, credenciales reales y publicación de releases.

| Prioridad | Hueco y evidencia actual | Regresión propuesta y criterio de éxito |
| --- | --- | --- |
| Alta | Bootstrap inicial: `test_terminal.py` verifica branding de una herramienta ya preparada; el actualizador prueba pins y sustitución, pero no cubre el arranque completo de `bootstrap.ps1`/`bootstrap.py` ante fallos. | Inyectar hash incorrecto, wheel corrupta, fallo de importación y fallo de publicación final. No debe aparecer un runtime marcado como listo e incompleto; un reintento debe poder completarse sin perder un runtime anterior. Complementar con arranque desde ZIP en Windows limpio. |
| Alta | Exclusión de mutaciones: MCP cubre su lock y la confirmación fuera del lock; falta una prueba directa del lock común entre procesos independientes y tras matar a su propietario. | Un proceso mantiene `mutation`, otro recibe conflicto sin escribir; terminar al propietario y comprobar que un tercero adquiere el lock. Añadir dos operaciones incompatibles sobre una instancia temporal y verificar estado íntegro. |
| Alta | Publicación: sólo tres tests directos; faltan varias ramas que protegen tags, assets, reanudación y el flujo nuevo. | Simular suite fallida, tag de otro commit, ZIP pendiente alterado, asset existente distinto, digest de subida incorrecto y fallo tras subir el primer asset. No publicar el borrador ni sobrescribir assets; reanudar el mismo commit/versión donde proceda. Git y API siempre simulados. |
| Alta | Extracción: hay traversal ZIP, enlace TAR, Deflate64 real y listado RAR simulado; la matriz de límites y formatos está incompleta. | ZIP/TAR/7z con duplicados por mayúsculas, hardlinks, tamaños/entradas excedidos, archivo truncado y fallo del extractor. Usar límites reducidos/metadata sintética, sin archivos gigantes. Comprobar rechazo y ausencia de publicación del componente; no confundir staging parcial con instalación publicada. |

## Ampliaciones siguientes

| Prioridad | Escenario pendiente | Comprobación útil |
| --- | --- | --- |
| Media | Respuestas diarias fuera de orden | Bloquear consulta A, cambiar configuración, completar B y después A; la caché debe conservar B. Los tests actuales cambian claves secuencialmente y anidan una consulta, pero no prueban este orden de finalización. |
| Media | Fallos de escritura atómica | Hacer fallar `os.replace`/escritura en `write_json`: JSON anterior intacto y temporal retirado. Los rollbacks de servicios no sustituyen esta comprobación directa. |
| Media | Portal de autenticación | Formulario con `action` de otro origen y redirecciones POST 307/308: ningún segundo servidor debe recibir usuario/contraseña. Ya se prueba POST HTTP bloqueado y 302, no toda esta matriz. |
| Media | Saturación de MCP | Límites de cola, trabajos y salida por sesión, cuerpos HTTP grandes y stdout/stderr simultáneos por encima del límite; rechazo controlado, drenaje sin bloqueo y cancelación utilizable. El límite de salida por trabajo sí tiene test. |
| Media | Pocketools desde Git | Fixture Git servida localmente por HTTP, con hooks/filtros/submódulos de prueba que escriban un marcador si se ejecutan; sincronización sin marcadores ni `.git`, y versión anterior conservada ante fallo. El test actual sustituye `subprocess.run`. |
| Media | Red interrumpida y disco lleno | Fallo después de un primer bloque de descarga y fallo de copia/escritura por falta de espacio: retirar descarga incompleta, conservar instalación previa y comunicar el error. |
| Baja | Presentación y rendimiento | Inspección en terminal pequeño, escalado y textos largos; medición reproducible de apertura/búsqueda con catálogo sintético grande. Los tests de rendimiento actuales protegen el algoritmo, no fijan un presupuesto de latencia de UI. |

## Aceptación de la distribución

Antes de publicar, ejecutar sobre una copia desechable en Windows x64 limpio:

1. Extraer el ZIP en ruta con espacios y caracteres no ASCII; arrancar sin Python,
   Scoop ni Windows Terminal globales y ejecutar `doctor`.
2. Instalar un componente con dependencia, activarlo en dos profiles y comprobar
   desde pestañas PowerShell y CMD el entorno, workspace y ausencia de acumulación.
3. Actualizar un componente aislado con datos distintos por profile; comprobar
   datos, accesos y copias de un profile inactivo, cerrar y volver a abrir.
4. Abrir una aplicación acoplada que cree descendientes; cerrar su pestaña y
   comprobar que terminan, manteniendo vivas otra pestaña y otra instancia MITO.
5. Actualizar una distribución previa a la candidata, con el mismo bootstrap y
   con bootstrap cambiado en modo online; verificar datos conservados y `doctor`.
   En offline, el cambio de bootstrap debe seguir rechazándose sin red.

La continuidad contiene pruebas manuales históricas de varios de estos flujos.
No se repitieron en esta auditoría y no constituyen una aceptación de la futura
distribución 1.0.0. No se promete recuperación automática tras apagar el equipo
durante una sustitución: ese límite sigue siendo parte del contrato.

## Versión y automatización

`publish.next_version` incrementa siempre el parche de la mayor versión
local/remota. Cambiar `__version__` a `1.0.0` y ejecutar `release` produciría
`1.0.1` si no existe una versión superior. El salto exacto a 1.0.0 necesita una
decisión explícita sobre el flujo de publicación y sus regresiones; esta revisión
no cambia ese contrato ni prepara una release pendiente.

No hay workflows versionados bajo `.github` en el checkout revisado. Conviene
automatizar la suite en Windows con el bootstrap privado y registrar versión,
resultado y tests omitidos. La prueba de aceptación puede ser un paso separado
de la suite cotidiana. Añadir cobertura de ramas ayudaría a descubrir más huecos,
sin convertir un porcentaje global en el criterio único de publicación.

## Verificación de esta revisión

- `core\tools\python\python.exe -B -I -m unittest discover -s core\tests -v`:
  **216 pruebas OK en 442,408 s**, sin fallos ni omisiones.
- `.\mito.cmd doctor`: correcto en los tres profiles de la instancia.
- `git diff --check` correcto; documentos nuevos revisados también para detectar
  espacios al final de línea.
- Log local en `core/state/test-review.log`, excluido de Git. Aparecen avisos de
  temporización de asyncio/Textual y un `ResourceWarning` de limpieza implícita
  del `HTTPError` 404 simulado en `test_update_checks.py`. No fallan tests;
  conviene cerrar explícitamente ese recurso de la fixture.
