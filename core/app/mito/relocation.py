"""Instancia movida o copiada: repara lo que guarda rutas absolutas, sin procesos auxiliares.

El entorno, los shims, los ajustes del Terminal y los enlaces persistentes ya se
reconstruyen al abrir pestañas o aplicaciones. Quedan fuera de esa reconstrucción:

- Persistencias materializadas: Explorer y la mayoría de copiadores siguen los
  junctions y escriben una copia real del destino dentro del payload. Esa carpeta
  impediría recrear el enlace; se aparta a core/staging, nunca se borra.
- Accesos del escritorio registrados en core/state: tras un traslado se reescriben
  hacia la nueva ruta; tras una copia pertenecen al original y se dejan de registrar
  aquí, para que esta instancia no los sobrescriba.
- Enlaces Host data/cache materializados: se avisa; la UI ya los muestra sin enlazar.
- Datos en host: tras un traslado las junctions del AppData del host se reapuntan;
  tras una copia siguen siendo del original y esta instancia deja de registrarlas.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import uuid

from .i18n import tr
from .storage import YitoError, inside, mutation, read_json, write_json

MARKER = 'core/state/instance.json'
NOTICE = 'core/state/relocation-notice.json'


def save_notice(root, messages):
    path = root / NOTICE
    previous = read_json(path) if path.exists() else []
    write_json(path, [*previous, *messages] if isinstance(previous, list) else list(messages))


def pop_notice(root):
    """Mensajes pendientes para el log de la UI; se muestran una sola vez."""
    path = root / NOTICE
    if not path.exists():
        return []
    try:
        messages = read_json(path)
    except (OSError, ValueError):
        messages = []
    path.unlink(missing_ok=True)
    return [str(message) for message in messages] if isinstance(messages, list) else []


def same_path(first, second):
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(os.path.normpath(str(second)))


def inside_root(path, root):
    try:
        return Path(os.path.normcase(os.path.normpath(str(path)))).is_relative_to(os.path.normcase(os.path.normpath(str(root))))
    except (TypeError, ValueError):
        return False


def previous_root(root):
    marker = root / MARKER
    if not marker.exists():
        return None
    try:
        value = read_json(marker).get('root')
    except (OSError, ValueError, AttributeError):
        return None
    return Path(value) if isinstance(value, str) and value else None


def remember(root):
    write_json(root / MARKER, {'root': str(root)})


def is_instance(path):
    return (path / 'core/app/mito').is_dir() and (path / 'mito.cmd').is_file()


def check(engine, log=print):
    """Llamar al arrancar. Devuelve None si la ruta no cambió; si cambió, el resumen."""
    root = engine.root
    old = previous_root(root)
    if old is not None and same_path(old, root):
        return None
    if old is None:
        # Primer arranque o instancia anterior a este registro: nada que reparar.
        remember(root)
        return None
    copied = is_instance(old)
    with mutation(root):
        summary = {'from': str(old), 'to': str(root), 'copied': copied,
                   'persist': set_aside_materialized_persist(root, log),
                   'shortcuts': repair_shortcuts(engine, old, copied, log),
                   'host_links': materialized_host_links(root),
                   'host_exposed': exposed_host_links(root, old, copied, log)}
        remember(root)
    if copied:
        log(tr('MITO se ha copiado desde {p1}. Esta copia usa sus propias rutas; los accesos del escritorio siguen siendo del original.', p1=old))
    else:
        log(tr('MITO se ha movido desde {p1}. Rutas y accesos del escritorio actualizados.', p1=old))
    for path in summary['host_links']:
        log(tr('Host data/cache copiado como carpeta normal en {p1}. Vuelve a enlazarlo desde Inicio si quieres seguir compartiéndolo con el host.', p1=path))
    return summary


def persist_payloads(root):
    yield from (marker.parent for marker in (root / 'core/apps').glob('*/*/.mito-persist.json'))
    yield from (marker.parent for marker in (root / 'core/profile-apps').glob('*/*/*/.mito-persist.json'))


def set_aside_materialized_persist(root, log=print):
    """Carpetas reales donde el motor había creado un junction persistente."""
    moved = []
    stage = None
    for payload in persist_payloads(root):
        try:
            managed = read_json(payload / '.mito-persist.json')
        except (OSError, ValueError):
            continue
        for relative in managed:
            source = inside(payload, relative)
            if not source.is_dir() or source.is_junction() or source.is_symlink():
                continue
            if stage is None:
                stage = inside(root, 'core/staging/relocated-persist-' + uuid.uuid4().hex)
            backup = stage / payload.relative_to(root) / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            source.rename(backup)
            moved.append(str(backup))
    if moved:
        log(tr('Copias de persistencia apartadas en {p1}. Tus datos siguen en home; puedes borrar esa carpeta cuando compruebes las aplicaciones.', p1=stage))
    return moved


def materialized_host_links(root):
    from .host_data import KINDS, registry
    result = []
    try:
        records = registry(root)
    except (OSError, ValueError):
        return result
    for kinds in records.values():
        for kind, items in kinds.items():
            if kind not in KINDS or not isinstance(items, list):
                continue
            for item in items:
                path = root / 'home/AppData' / KINDS[kind] / str(item.get('relative', ''))
                if item.get('relative') and path.is_dir() and not path.is_junction():
                    result.append(str(path))
    return result


def exposed_host_links(root, old, copied, log=print):
    """Junctions del AppData del host hacia esta instancia (Datos en host)."""
    from .host_exposure import relocate
    try:
        return relocate(root, old, copied, log)
    except (OSError, ValueError, YitoError) as exc:
        log(tr('No se pudieron retirar los enlaces del host: {p1}', p1=exc))
        return 0


def link_targets(paths):
    """TargetPath de varios .lnk en una sola llamada; rutas por stdin, nunca como código."""
    if not paths:
        return {}
    script = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "$ws = New-Object -ComObject WScript.Shell; $result = @{}; "
              "foreach ($p in ([Console]::In.ReadToEnd() | ConvertFrom-Json)) { $result[$p] = $ws.CreateShortcut($p).TargetPath }; "
              "$result | ConvertTo-Json -Compress")
    output = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                            input=json.dumps([str(p) for p in paths]), capture_output=True, text=True, encoding='utf-8',
                            check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
    return json.loads(output) if output else {}


def repair_shortcuts(engine, old, copied, log=print):
    """Reescribe (traslado) u olvida (copia) los accesos registrados que apuntan a la ruta anterior."""
    root = engine.root
    registries = [(kind, path) for kind in ('application-shortcuts', 'mito-shortcuts')
                  for path in sorted((root / 'core/state' / kind).glob('*.json'))]
    entries = []
    for kind, registry in registries:
        try:
            data = read_json(registry)
            link = Path(data['path'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if link.suffix.lower() == '.lnk' and link.is_file() and not link.is_symlink():
            entries.append((kind, registry, data, link))
    if not entries:
        return {'updated': 0, 'released': 0}
    try:
        targets = link_targets([link for _, _, _, link in entries])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log(tr('No se pudieron revisar los accesos del escritorio: {p1}', p1=exc))
        return {'updated': 0, 'released': 0}
    updated = released = 0
    for kind, registry, data, link in entries:
        target = targets.get(str(link)) or ''
        if not inside_root(target, old):
            continue
        if copied:
            # El acceso sigue abriendo la instancia original: no es de esta copia.
            registry.unlink(missing_ok=True)
            released += 1
            continue
        try:
            if kind == 'mito-shortcuts':
                relink_manager(engine, link, data)
            else:
                relink_application(engine, link, data)
            updated += 1
        except (OSError, ValueError, KeyError, YitoError, subprocess.SubprocessError) as exc:
            log(tr('No se pudo actualizar el acceso {p1}: {p3}', p1=link.name, p3=exc))
    return {'updated': updated, 'released': released}


def relink_manager(engine, link, data):
    from .shortcuts import application_icon, badge_color, badge_text, write_link
    from .terminal import command, prepare
    name = data['profile']
    engine.profile(name)
    badge = badge_text(engine, data.get('badge'))
    color = badge_color(engine, data.get('color'))
    icon = application_icon(engine, f'mito-{name}', badge, color=color)
    prepare(engine)
    write_link(link, command(engine, selected_profile=name), engine.root, icon)


def relink_application(engine, link, data):
    import hashlib
    from .shortcuts import application_icon, badge_color, badge_text, component_icon_source, write_link
    profile, app, shortcut = data['profile'], data['app'], data.get('shortcut')
    lock = engine.selected(engine.profile(profile)).get(app)
    if lock is None:
        raise YitoError(tr('El componente debe estar activo en el profile'))
    tag = hashlib.sha256((f'{profile}/{app}' if shortcut is None else f'{profile}/{app}/{shortcut}').encode()).hexdigest()[:16]
    badge = badge_text(engine, data.get('badge'))
    color = badge_color(engine, data.get('color'))
    icon = application_icon(engine, f'app-{tag}-{hashlib.sha256(badge.encode()).hexdigest()[:8]}', badge,
                            component_icon_source(engine, lock, profile, shortcut), color=color)
    argv = [str(engine.root / 'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', 'run']
    if data.get('coupled', False):
        argv.append('--coupled')
    argv += ['--profile', profile, '--app', app]
    if shortcut is not None:
        argv += ['--shortcut', shortcut]
    arguments = data.get('arguments', [])
    if not isinstance(arguments, list) or any(not isinstance(value, str) for value in arguments):
        raise YitoError(tr('Acceso registrado no válido'))
    if arguments:
        argv += ['--', *arguments]
    write_link(link, argv, engine.root, icon)
