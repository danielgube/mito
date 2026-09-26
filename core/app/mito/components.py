"""Generic installed-component lifecycle, independent of application names."""
from .i18n import tr
import re
import shutil
import uuid
from .storage import YitoError, inside, mutation, read_json, write_json


def validate_update_layout(old, manifest):
    from .catalog import effective
    from .isolation import isolated
    from .persistence import definitions
    if isolated(old) != isolated({'manifest': manifest}) or set(definitions(effective(old['manifest']))) != set(definitions(effective(manifest))):
        raise YitoError(tr('La actualización cambia persist o el aislamiento por profile; requiere una migración de datos explícita.'))


def check_unused(folder):
    """Probe Windows delete access, without traversing persistent junctions."""
    import os
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for parent, dirs, files in os.walk(folder, followlinks=False):
        from pathlib import Path
        dirs[:] = [name for name in dirs if not (Path(parent)/name).is_junction() and not (Path(parent)/name).is_symlink()]
        for name in files:
            path = Path(parent)/name
            handle = kernel.CreateFileW(str(path), 0x10000, 7, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                raise YitoError(tr('Cierra la aplicación antes de actualizar; archivo en uso o sin permiso de eliminación: {p1}', p1=(path)))
            kernel.CloseHandle(handle)


def version_key(value):
    match = re.fullmatch(r'[vV]?(\d+(?:[._-]\d+)*)(.*)', str(value))
    if not match:
        return None
    numbers = list(map(int, re.split(r'[._-]', match[1])))
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    suffix = match[2].lower().lstrip('-._+')
    prerelease = bool(re.match(r'(?:alpha|beta|preview|pre|rc|ea|dev|snapshot)', suffix))
    parts = tuple((1,int(p)) if p.isdigit() else (0,p) for p in re.findall(r'\d+|[a-z]+',suffix))
    return tuple(numbers), not prerelease, parts


def available_update(engine, lock, catalog=None):
    if catalog is None:
        catalog = dict(engine.catalog.all())
    manifest = catalog.get(lock['key'])
    if not manifest:
        return None
    old, new = version_key(lock['version']), version_key(manifest.get('version',''))
    if old is None or new is None or new <= old:
        return None
    return {'version': str(manifest['version']), 'status': engine.catalog.compatibility(lock['key'],manifest)}


def users(engine, installation):
    return [name for name in engine.profiles() if installation in engine.profile(name)['apps']]


def refresh_launchers(engine, names):
    for name in names:
        engine._write_shims(engine.profile(name),name,engine.root/'core/state/shims'/name)
    from .terminal import prepare
    prepare(engine)


def remove(engine, installation, log=print):
    with mutation(engine.root):
        lock = engine.installed().get(installation)
        if not lock:
            raise YitoError(tr('El componente ya no está instalado'))
        payload = inside(engine.root,lock['path'])
        apps = (engine.root/'core/apps').resolve()
        if not payload.resolve().is_relative_to(apps) or len(payload.relative_to(apps).parts) != 2 or payload.is_junction() or payload.is_symlink():
            raise YitoError(tr('Ruta de instalación no válida para eliminar'))
        backup = {name:engine.profile(name) for name in users(engine,installation)}
        stage = inside(engine.root, 'core/staging/remove-' + uuid.uuid4().hex)
        # Include all prepared copies, even for profiles that are now inactive/deleted.
        targets = [payload]
        from .isolation import isolated
        if isolated(lock):
            relative = payload.relative_to(apps).as_posix()
            for profile_dir in (engine.root/'core/profile-apps').glob('*'):
                copy = inside(profile_dir,relative)
                if copy.exists():
                    if not copy.resolve().is_relative_to((engine.root/'core/profile-apps').resolve()) or copy.is_junction() or copy.is_symlink():
                        raise YitoError(tr('Copia de profile fuera de su directorio'))
                    targets.append(copy)
        stage.mkdir()
        moved=[]
        try:
            for index,target in enumerate(targets):
                stored=stage/str(index)
                target.rename(stored)
                moved.append((target,stored))
        except Exception:
            for target,stored in reversed(moved):stored.rename(target)
            stage.rmdir()
            raise
        changed=[]
        try:
            for name,data in backup.items():
                updated=dict(data, apps=[key for key in data['apps'] if key != installation])
                engine.save_profile(name,updated)
                changed.append(name)
        except Exception:
            for target,stored in reversed(moved):stored.rename(target)
            stage.rmdir()
            for name in changed:
                engine.save_profile(name,backup[name])
            raise
        try:
            # Python's Windows rmtree removes junctions without traversing their targets.
            shutil.rmtree(stage)
        except OSError:
            log(tr('Componente retirado; quedan archivos en uso en {p1}. Cierra la aplicación para poder limpiarlos.', p1=(stage)))
        refresh_launchers(engine,backup)
        log(tr('Eliminado {p1} {p3}. Datos persistentes conservados.', p1=(lock['key']), p3=(lock['version'])))


def update(engine, installation, log=print, confirm_unverified=None):
    old = engine.installed().get(installation)
    if not old:
        raise YitoError(tr('El componente ya no está instalado'))
    candidate = available_update(engine,old)
    if not candidate:
        raise YitoError(tr('No hay una versión posterior en el catálogo local'))
    if candidate['status'] != 'Compatible':
        raise YitoError(candidate['status'])
    validate_update_layout(old, engine.catalog.resolve(old['key'])[1])
    engine.install(old['key'], log=log, confirm_unverified=confirm_unverified)
    replacement = engine.installed()[old['key']]
    log(tr('Actualizado {p1}: {p3} → {p5}. Ruta, persistencia y activación conservadas.', p1=(old['key']), p3=(old['version']), p5=(replacement['version'])))
    return old['key']


def publish_plan(engine, records, current, profile, data, log):
    """Caller holds mutation lock. Stage the entire plan before replacing stable paths."""
    from .isolation import isolated, payload_path, prepare_payload
    changed = {key: record for key, record in records.items()
               if key not in current or current[key]['manifest_sha256'] != record['manifest_sha256']}
    profiles = {name: engine.profile(name) for name in engine.profiles()}
    affected = {name for name, value in profiles.items() if set(value['apps']) & (changed.keys() & current.keys())}
    if profile:
        affected.add(profile)
    def dependencies(key, seen=None):
        from .catalog import array, effective
        seen = set() if seen is None else seen
        for name in array(effective(records[key]['manifest']).get('depends')):
            dependency, _ = engine.catalog.resolve(name)
            if dependency not in seen:
                seen.add(dependency)
                dependencies(dependency, seen)
        return seen
    targets = []
    copies = {}
    for key, record in changed.items():
        base = inside(engine.root, record['path'])
        if base.is_symlink() or base.is_junction():
            raise YitoError(tr('Ruta de instalación enlazada no válida'))
        if base.exists():
            if key not in current:
                raise YitoError(tr('Instalación incompleta existente: {p1}', p1=(base)))
            targets.append(base)
        if isolated(record):
            names = {name for name, value in profiles.items() if key in value['apps']}
            for folder in (engine.root/'core/profile-apps').glob('*'):
                target = payload_path(engine.root, record, folder.name)
                if target.exists():
                    if target.is_symlink() or target.is_junction():
                        raise YitoError(tr('Copia de profile no válida'))
                    targets.append(target)
                    names.add(folder.name)
            copies[key] = names
    for target in targets:
        check_unused(target)
    stage = inside(engine.root, 'core/staging/install-' + uuid.uuid4().hex)
    stage.mkdir()
    moved, published, written = [], [], []
    links = {}
    try:
        for index, (key, record) in enumerate(changed.items()):
            staged = stage/'new'/str(index)
            engine._install_payload(key, record['manifest'], staged, log)
            write_json(staged/'.mito.json', {'key':key, 'manifest_sha256':record['manifest_sha256'], 'record':record})
        # Recheck after downloads: another process may have opened a binary.
        for target in targets:
            check_unused(target)
        for index, target in enumerate(targets):
            stored = stage/'old'/str(index)
            stored.parent.mkdir(exist_ok=True)
            target.rename(stored)
            moved.append((target, stored))
        for index, (key, record) in enumerate(changed.items()):
            target = inside(engine.root, record['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            (stage/'new'/str(index)).rename(target)
            published.append(target)
            if isolated(record):
                for name in sorted(copies[key]):
                    copy = payload_path(engine.root, record, name)
                    published.append(copy)
                    prepare_payload(engine.root, record, name)
            else:
                prepare_payload(engine.root, record, profile or 'default')
        for name in sorted(affected):
            value = data if name == profile else dict(profiles[name], apps=list(profiles[name]['apps']))
            # Only dependencies of this profile's replaced components become active.
            if name != profile:
                needed = set()
                for key in set(profiles[name]['apps']) & changed.keys():
                    needed.update(dependencies(key))
                value['apps'].extend(key for key in records if key in needed and key not in value['apps'])
            engine.save_profile(name, value)
            written.append(name)
        from .shortcuts import migrate_application_links
        for key, record in changed.items():
            migrate_application_links(engine, key, key, record, links, log)
        refresh_launchers(engine, affected)
    except Exception:
        for path, content in links.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        for target in reversed(published):
            if target.exists():
                shutil.rmtree(target)
        for target, stored in reversed(moved):
            stored.rename(target)
        for name in written:
            engine.save_profile(name, profiles[name])
        # Staging is confined under this instance; rmtree does not traverse junctions.
        shutil.rmtree(stage)
        refresh_launchers(engine, affected)
        raise
    try:
        shutil.rmtree(stage)
    except OSError as exc:
        raise YitoError(tr('Componente publicado; limpieza pendiente en {p1}: {p3}', p1=(stage), p3=(exc))) from exc
