# Changelog

Cambios relevantes de MITO (My Integrated Terminal Orchestrator).
El historial se reconstruye a partir de los commits locales de versión y sus
cambios asociados; las fechas corresponden a esos commits, no a una comprobación
de publicación remota. Las decisiones vigentes están en
[Arquitectura](spec/ARCHITECTURE.md) y los límites en
[Continuidad](spec/CONTINUITY.md).

## Sin publicar

### Corregido

- `mito release` fallaba con «[WinError 2]» en las pestañas de MITO: llamaba a `git`
  sin shell y allí es un shim `.cmd`. Ahora resuelve `git.exe` del host o de un
  componente instalado, y los errores de Git incluyen su causa.
- Los comandos de los componentes (`git`, `node`, `java`…) se ejecutaban siempre en el
  workspace del profile, ignorando la carpeta actual: `git init` en `C:\proyecto`
  creaba el repositorio en `workspaces\<profile>`. Ahora trabajan en el directorio
  desde el que se invocan; aplicaciones y accesos siguen abriendo en el workspace.
- La instalación mueve los archivos extraídos al payload en lugar de copiarlos: evita
  escribir dos veces cada archivo y que el antivirus vuelva a analizarlos antes de
  publicar. El log indica la fase «Publicando», que el antivirus puede retrasar.
- La comprobación de actualizaciones de MITO fallaba con «[Errno 13] Permission
  denied» al coincidir con la de componentes: el lock trataba un byte bloqueado como
  error de permisos. Ahora espera brevemente y, sin espera, da el mensaje de lock ocupado.
- Una instancia sin profiles, como un checkout del código fuente, crea el profile
  `default` vacío al abrir el gestor (`mito.cmd`, `ui`, `start`). Hasta ahora sólo
  lo incluía el ZIP de release y los componentes instalados no podían activarse.
- El home portable crea las carpetas de usuario conocidas de Windows (Desktop,
  Documents, Downloads, Music, Pictures, Videos, etc.). Sin ellas, aplicaciones
  Electron como Mattermost abortaban con «Failed to get 'downloads' path».
- Eliminar un componente comprueba antes si sus archivos están en uso, igual que
  Actualizar, y pide cerrar la aplicación (también desde la bandeja) en lugar de
  mostrar «[WinError 5] Acceso denegado». No se modifica nada si falla.

- El catálogo identifica componentes instalados y deshabilita Instalar, con
  independencia de su activación por profile o versión instalada.

- El log de instalación deja de mantener «Descargando» durante la extracción y
  preparación: muestra fases, CAB actuales y recuentos de archivos.

### Añadido

- «En host · link/unlink» en MIS COMPONENTES: enlaza el AppData real del host con
  los datos de la app en MITO, para que una app arrancada fuera de MITO (por ejemplo,
  con el inicio automático de Windows) use los mismos datos. Sólo comparte datos, no
  el entorno del profile; las carpetas existentes del host se apartan y se restauran.
- El botón de Internet de Inicio configura la red cuando no hay conexión. Prueba la
  ruta configurada, el proxy de Windows (manual, PAC o WPAD) y la conexión directa, y
  propone guardar el proxy o el portal detectados. Si el portal lo exige, pide usuario
  y contraseña sólo para la sesión; nunca se guardan.
- MITO detecta al arrancar que su carpeta se ha movido o copiado: reescribe sus
  accesos del escritorio tras un traslado, deja de reclamar los del original en una
  copia y aparta (sin borrar) las persistencias que el copiador convirtió en carpetas.
- **Versiones…** en el catálogo instala una versión concreta de un componente: desde
  el histórico Git del bucket (manifiesto y hash reales) o generada con `autoupdate`
  como `scoop install app@versión`. La versión elegida sustituye a la instalada y
  queda retenida frente a Actualizar hasta pulsar **Liberar** en Info.
- Extracción MSI mediante lectura de tablas y CAB internos, sin ejecutar la
  instalación ni acciones del paquete. Conserva instalación en staging, inventario,
  activación por profile, actualización y eliminación genéricos. Medios externos,
  archivos sin comprimir y CAB divididos permanecen fuera de alcance.
- El primer arranque detecta el proxy de Windows (manual, script PAC o WPAD) cuando
  `config.properties` no declara proxy, clasifica la conexión (directa, HTTP 407 o
  portal) y ofrece guardar `http_proxy`, `https_proxy`, `no_proxy` y el portal
  detectado. Si hay portal, permite autenticarse en consola antes de descargar.

### Documentación

- Auditoría de pruebas de cara a 1.0.0, con cobertura funcional, huecos priorizados
  y propuesta de aceptación de la distribución en
  [Revisión de pruebas](spec/TEST_READINESS_1.0.md).
- Incorporado este historial de cambios.

La versión del código continúa en 0.1.5. El salto a 1.0.0 todavía no se ha realizado.

## 0.2.10 — 2026-11-19

### Commits incluidos

- Proxy en el bootstrap, versiones de componentes, traslado de instancia y correcciones ([655e17b](https://github.com/danielgube/mito/commit/655e17b74f19de0b53465d62088633b2fef2e99c))
- Release: resolver git\.exe en lugar del shim de la pestaña ([fae84ff](https://github.com/danielgube/mito/commit/fae84ffb8204fca40f42ee8eccc2c0a8cfd08dc8))
- Pruebas: corregir dos fallos y desactivar la depuración de asyncio en la UI ([a3c47d7](https://github.com/danielgube/mito/commit/a3c47d709998a752bb2babb089761d21e105e676))
- tests hotfixeds and internet improved automation ([eff92c0](https://github.com/danielgube/mito/commit/eff92c0671f1473d0cd1a9416eef8ebeeb9d9eb7))
- tests hotfixeds 2 ([bc7ee8e](https://github.com/danielgube/mito/commit/bc7ee8e5510af76a2bfd3342160518cc935cd43c))

[Comparar cambios](https://github.com/danielgube/mito/compare/f3e940abe37b65f1f4f5b494ae26eeb325a370dd...bc7ee8e5510af76a2bfd3342160518cc935cd43c)

## 0.2.9 — 2026-09-28

### Commits incluidos

- initial upload ([b826c6c](https://github.com/danielgube/mito/commit/b826c6cb91f0a14ddd9b6b0491a0da25326a96fe))
- snapshoots upgraded and updated ([58d668d](https://github.com/danielgube/mito/commit/58d668d6ebfe4a8d7adcc446a191f3f08f32e7a8))
- readme and test hotfixed ([37c39d6](https://github.com/danielgube/mito/commit/37c39d60d1ef1905df13fa39d9efc91207d4eaf2))

## 0.2.8 — 2026-09-25

### Commits incluidos

- added background\-color for shorcuts ([0941aa4](https://github.com/danielgube/mito/commit/0941aa422e8e4ed8a1957da2d2cd567b89a5ddf7))
- pocketool features improved and icon fix ([0e6ffc9](https://github.com/danielgube/mito/commit/0e6ffc9144dc8df165d9d52af0c0f0275da6a415))
- pocketools improved with multiple features ([41179a1](https://github.com/danielgube/mito/commit/41179a14e1e824c1f09a111ee9b17157785eed17))

[Comparar cambios](https://github.com/danielgube/mito/compare/b2339ea10dd68f18e160897942f32b0dd59dbac4...41179a14e1e824c1f09a111ee9b17157785eed17)

## 0.2.7 — 2026-09-23

### Commits incluidos

- improved pre\_installs a wix options ([1e101d4](https://github.com/danielgube/mito/commit/1e101d4f951bfec190d8a3f9a57b80fde9ef2bfa))
- hotfix test colors and updates ([1614081](https://github.com/danielgube/mito/commit/1614081c8ea37d7b31f21b73c1ebcb13351657c5))

[Comparar cambios](https://github.com/danielgube/mito/compare/20e05c485f82027079bf21bc14e51dace3b4fd14...1614081c8ea37d7b31f21b73c1ebcb13351657c5)

## 0.2.6 — 2026-09-21

### Commits incluidos

- improved post\-installations and interface ([ab2a79d](https://github.com/danielgube/mito/commit/ab2a79db4e4f41ae29e55f530399d19006e2818c))
- hotfix test sand clock ([29f17c0](https://github.com/danielgube/mito/commit/29f17c0233691b0d5f77a2ec2079d004dc9f4b27))

[Comparar cambios](https://github.com/danielgube/mito/compare/dc74a35734c22a7a4e1bc4197bdc0c05751b0129...29f17c0233691b0d5f77a2ec2079d004dc9f4b27)

## 0.2.5 — 2026-09-18

### Commits incluidos

- gateways improved system ([2e517ff](https://github.com/danielgube/mito/commit/2e517ff43b33a7aa6656ab0fedd3141cc39e241e))

[Comparar cambios](https://github.com/danielgube/mito/compare/b669dd5dc18c8bf0e1b20ebe05051ec2cce75282...2e517ff43b33a7aa6656ab0fedd3141cc39e241e)

## 0.2.4 — 2026-09-18

### Commits incluidos

- bootstrap improved ([e57e9a8](https://github.com/danielgube/mito/commit/e57e9a8c43f9047c8892b2f6da3c7ad4f55fc509))
- ssl unsecured signed proxy certificate added ([8c89193](https://github.com/danielgube/mito/commit/8c8919313e1f33a327ee1707b86a3c9aedcae496))
- hotfix ssl certificates enterprise failiture ([0b9ed86](https://github.com/danielgube/mito/commit/0b9ed86d446f65975a707902c0f730077c0ea1f1))
- improved tests ([6b5cf9b](https://github.com/danielgube/mito/commit/6b5cf9b6a893dcec1eb76e955bc45de7d53574ec))
- utf 8 hotfix ([266c05f](https://github.com/danielgube/mito/commit/266c05f27509ed5470b4d1f2daf73dbb5fca6f60))

[Comparar cambios](https://github.com/danielgube/mito/compare/40bac6dd2cf371f58d6e65297658fcb43a51a195...266c05f27509ed5470b4d1f2daf73dbb5fca6f60)

## 0.2.3 — 2026-09-17

### Commits incluidos

- logs improved and more affordable interface ([a68b4d0](https://github.com/danielgube/mito/commit/a68b4d07bd8cc4236eeeb6da93a3218c6e75efa7))

[Comparar cambios](https://github.com/danielgube/mito/compare/beb217126d8eb56a0fa07c519fbe965a45740421...a68b4d07bd8cc4236eeeb6da93a3218c6e75efa7)

## 0.2.2 — 2026-09-17

### Commits incluidos

- log system and remote issues added ([ecabba4](https://github.com/danielgube/mito/commit/ecabba4e9f72c53237b81e876fed3eaa79623b68))

[Comparar cambios](https://github.com/danielgube/mito/compare/19cd2d38cbd02b3a3394db45494d4e5438e1f146...ecabba4e9f72c53237b81e876fed3eaa79623b68)

## 0.2.1 — 2026-09-16

### Commits incluidos

- improved mito releases system ([eb7a58a](https://github.com/danielgube/mito/commit/eb7a58a4791bfdf82ab75d1225bf896673a01fbd))

[Comparar cambios](https://github.com/danielgube/mito/compare/e938a2fc208d2087625340ce9d100604f776d956...eb7a58a4791bfdf82ab75d1225bf896673a01fbd)

## 0.1.5 — 2026-09-14

### Añadido

- Actualización de MITO desde la interfaz, con preparación fuera de la instancia,
  comprobación de procesos abiertos, copia de seguridad y reversión ante fallos
  normales de sustitución.
- Actualizaciones offline desde `update/`, con prioridad sobre la consulta remota,
  verificación del ZIP y consentimiento cuando no se declara hash.
- URL de actualización configurable para endpoints HTTP/HTTPS con formato de
  release compatible con GitHub/Gitea.
- Caché diaria persistente de comprobaciones de MITO y componentes, incluidos
  errores; cambios de configuración o inventario invalidan la caché correspondiente.

### Mejorado

- Clasificación de manifiestos sin consultas al filesystem del host, lectura de
  catálogos directamente desde ZIP y caché acotada de términos de búsqueda.
- README orientado a primeros pasos, manual completo en `MANUAL.md` y capturas
  vectoriales en color de Inicio y Catálogo.

### Límites

- Actualizar offline requiere el mismo bootstrap instalado; el ZIP mínimo no
  contiene runtimes. No se actualizan checkouts Git ni se ofrece recuperación
  automática tras una interrupción abrupta.

## 0.1.4 — 2026-09-13

### Añadido

- Aplicaciones del host como referencias externas, con lanzamiento y accesos
  usando el entorno y workspace del profile.
- Enlace y desenlace optativos de datos/cache del host mediante junctions,
  selección automática o manual y confirmación antes de sustituir datos locales.
- Servidor MCP local de activación manual, herramientas de gestión y ejecución
  de trabajos con salida, timeout, cancelación y confirmaciones humanas.
- Monitor opcional de trabajos MCP separado del diálogo de conexión.
- Apertura independiente o acoplada a una pestaña, con Job Object para controlar
  descendientes, argumentos de usuario y accesos que conservan modo y argumentos.
- Interfaz en los 24 idiomas oficiales de la Unión Europea, selección manual o
  detección del idioma de Windows y fallback de traducciones.
- Pocketools compartidas desde Git HTTP/HTTPS o carpetas locales.

### Mejorado

- Una única versión instalada por `bucket/app`, rutas estables y activaciones sin
  fijar versión; migración explícita del almacenamiento anterior.
- Actualización de bases y copias por profile, incluidas las inactivas, conservando
  persistencia y accesos; reversión ante fallos previos a la limpieza final.
- Nuevos accesos de componentes con nombre `profile - bucket-app`.

### Corregido

- Reconocimiento y retirada segura de junctions manuales de datos/cache del host.
- Transporte de argumentos BAT/CMD con espacios, metacaracteres y valores vacíos;
  los subcomandos simples llegan sin comillas adicionales.
- Conservación de la salida de aplicaciones acopladas: se espera Intro también
  cuando el lanzador devuelve cero o falla el arranque.

## 0.1.3 — 2026-09-09

### Añadido

- Aislamiento genérico de binarios y datos por profile para manifiestos que
  combinan `persist` y `shortcuts`, con reconstrucción al trasladar la instancia.
- Gestión de actualización/eliminación de componentes y detalles de sus rutas
  binarias y persistentes.
- Variables globales, proxy corporativo y portal de autenticación.
- Exportación/importación de selecciones de componentes de un profile sin
  transferir datos, variables ni manifiestos externos.
- Edición de configuración global y variables del profile desde la interfaz,
  con validación y detección de modificaciones externas.
- Confirmación previa de descargas sin hash, incluidas dependencias; soporte de
  MD5/SHA-1 y hashes ausentes, vacíos, `null` o `skip`.
- Ejecución genérica de `pre_install` mediante PowerShell sin perfil y helpers
  limitados, incluida extracción de archivos anidados.

### Cambiado

- Descargas y redirecciones HTTP/HTTPS; fallback al 7-Zip privado para ZIP con
  métodos de compresión no soportados por Python.
- Retiradas políticas y excepciones de componentes: entorno, persistencia y
  lanzamiento se derivan del manifiesto.
- `post_install` se omite con aviso; los pasos restantes no soportados siguen
  bloqueados. Los hooks admitidos no son un sandbox.

## 0.1.2 — 2026-09-07

### Mejorado

- Búsqueda de catálogo con ranking por palabras/números, prioridad de identificador
  y filtros persistentes por bucket.
- Presentación de notas del manifiesto y filas compactas de componentes.

## 0.1.1 — 2026-09-07

### Añadido

- Publicación automática mediante Git Credential Manager: incremento de parche,
  comprobación de checkout limpio/sincronizado, pruebas y publicación mediante
  borrador con assets verificados.
- Reanudación de publicaciones pendientes sin volver a incrementar la versión.

### Cambiado

- `release --local` detecta la versión del código sin ejecutarlo ni incrementarla
  y genera ZIP mínimo más SHA-256, sin runtimes ni datos locales.

## 0.1.0 — 2026-09-07

Base inicial registrada en Git; esta entrada no acredita una release remota.

### Añadido

- Motor Python propio para catálogos Scoop, instalación, activación y entornos
  portátiles por profile, con home único por instancia.
- Bootstrap privado con CPython, Pillow, Textual, 7-Zip y Windows Terminal.
- CLI e interfaz Textual con inventario, catálogo y activación por profile.
- Extracción de paquetes, verificación de hashes y persistencia mediante enlaces.
- Windows Terminal personalizado, pestañas PowerShell/CMD por profile y accesos
  explícitos con iconos multirresolución y etiqueta superpuesta.
- Gestión de profiles, empaquetado mínimo y diagnóstico mediante `doctor`.
