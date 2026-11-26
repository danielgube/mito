"""Archivos de configuración elegidos manualmente en la tabla `files` de un profile.

MITO no sugiere ni clasifica rutas: no hay listas por producto ni por extensión.
El usuario decide qué viaja; el límite de tamaño es la única barrera frente a
librerías, cachés o binarios. Nunca se siguen junctions ni symlinks.
"""
from .i18n import tr
import os
from pathlib import Path
import stat
import tempfile
import zipfile
from .storage import YitoError, identifier, mutation, portable_path

ROOTS = ('home', 'workspace', 'persist')
INTERNAL_HOME = ('profiles', 'persist', 'tmp')
MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 1000
MANIFEST = 'mito-profile.json'
REPARSE_POINT = 0x400


def component_key(value):
    if not isinstance(value, str) or len(value.split('/')) != 2:
        raise YitoError(tr('Cada componente debe tener formato bucket/app'))
    bucket, name = value.split('/')
    return identifier(bucket) + '/' + identifier(name)


def relative_path(path):
    """Ruta relativa normalizada con '/', validada como portable."""
    if not isinstance(path, str) or not path or '\\' in path:
        raise YitoError(tr('Entrada de archivos no válida: {p1}', p1=(f'{path!r}')))
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise YitoError(tr('Entrada de archivos no válida: {p1}', p1=(path)))
    portable_path(path)
    return path


def normalize(value):
    if not isinstance(value, dict) or value.get('root') not in ROOTS or not set(value) <= {'root', 'app', 'path'}:
        raise YitoError(tr('Entrada de archivos no válida: {p1}', p1=(f'{value!r}')))
    root = value['root']
    if (root == 'persist') != ('app' in value):
        raise YitoError(tr('Entrada de archivos no válida: {p1}', p1=(f'{value!r}')))
    path = relative_path(value.get('path'))
    if root == 'home' and path.split('/')[0].casefold() in INTERNAL_HOME:
        raise YitoError(tr('Ruta interna de MITO no admitida: {p1}', p1=(path)))
    entry = {'root': root}
    if root == 'persist':
        entry['app'] = component_key(value['app'])
    entry['path'] = path
    return entry


def entries(values):
    """Valida la tabla completa: sin duplicados ni entradas contenidas en otra."""
    if not isinstance(values, list):
        raise YitoError(tr('Entrada de archivos no válida: {p1}', p1=(f'{values!r}')))
    result = [normalize(value) for value in values]
    for index, entry in enumerate(result):
        for other in result[index + 1:]:
            if (entry['root'], entry.get('app')) != (other['root'], other.get('app')):
                continue
            first, second = entry['path'].casefold(), other['path'].casefold()
            if first == second or first.startswith(second + '/') or second.startswith(first + '/'):
                raise YitoError(tr('Entrada repetida o contenida en otra: {p1}', p1=(label(other))))
    return result


def label(entry):
    if entry['root'] == 'persist':
        return f'persist · {entry["app"]} · {entry["path"]}'
    return f'{entry["root"]} · {entry["path"]}'


def member_prefix(entry):
    return 'files/persist/' + entry['app'] + '/' if entry['root'] == 'persist' else 'files/' + entry['root'] + '/'


def join(base, path):
    return Path(base).joinpath(*path.split('/'))


def reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & REPARSE_POINT) or stat.S_ISLNK(info.st_mode)


def crosses_link(base, path):
    """True si la ruta (o un tramo bajo su raíz) es junction o symlink."""
    current = Path(base)
    for part in path.split('/'):
        current = current / part
        try:
            if reparse(os.lstat(current)):
                return True
        except FileNotFoundError:
            return False
    return False


def bases(engine, profile, data, locks):
    """Raíces del profile: persist de componentes activos, workspace y home."""
    from .catalog import effective
    from .isolation import persist_root
    from .storage import inside
    result = [('persist', key, persist_root(engine.root, lock, profile))
              for key, lock in locks.items() if effective(lock['manifest']).get('persist')]
    result.append(('workspace', None, inside(engine.root / 'workspaces', data['workspace'])))
    result.append(('home', None, engine.root / 'home'))
    return result


def base_of(engine, profile, data, locks, entry):
    if entry['root'] == 'home':
        return engine.root / 'home'
    if entry['root'] == 'workspace':
        from .storage import inside
        return inside(engine.root / 'workspaces', data['workspace'])
    from .isolation import persist_root
    lock = next((lock for lock in locks.values() if lock['key'] == entry['app']), None)
    if lock is None:
        raise YitoError(tr('{p0} no está seleccionado en {p2}', p0=(entry['app']), p2=(profile)))
    return persist_root(engine.root, lock, profile)


def translate(engine, profile, raw):
    """Traduce una ruta real a una entrada: persist activo, workspace y después home."""
    data = engine.profile(profile)
    path = Path(os.path.abspath(str(raw).strip().strip('"')))
    for root, app, base in bases(engine, profile, data, engine.selected(data)):
        try:
            relative = path.relative_to(Path(os.path.abspath(base)))
        except ValueError:
            continue
        if not relative.parts:
            continue
        entry = {'root': root, 'path': relative.as_posix()}
        if app:
            entry['app'] = app
        return normalize(entry)
    raise YitoError(tr('La ruta debe estar en home, en el workspace o en el persist de un componente activo del profile'))


def gather(target, path, skipped):
    """(ruta relativa, archivo, tamaño) de archivos regulares, sin seguir enlaces."""
    info = os.lstat(target)
    if stat.S_ISREG(info.st_mode):
        yield path, Path(target), info.st_size
        return
    if not stat.S_ISDIR(info.st_mode):
        return
    with os.scandir(target) as items:
        children = sorted(items, key=lambda item: item.name)
    for item in children:
        child = path + '/' + item.name
        info = item.stat(follow_symlinks=False)
        if reparse(info):
            skipped.append(child)
        elif stat.S_ISDIR(info.st_mode):
            yield from gather(item.path, child, skipped)
        elif stat.S_ISREG(info.st_mode):
            yield child, Path(item.path), info.st_size


def too_large():
    return YitoError(tr('Los archivos de configuración superan el máximo de 2 MiB y 1000 archivos'))


def size(engine, profile, data, locks, entry):
    """Tamaño actual de una entrada, None si no existe; se detiene al superar el límite."""
    base = base_of(engine, profile, data, locks, entry)
    target = join(base, entry['path'])
    if crosses_link(base, entry['path']) or not os.path.lexists(target):
        return None
    total = count = 0
    for _, _, length in gather(target, entry['path'], []):
        total, count = total + length, count + 1
        if total > MAX_BYTES or count > MAX_FILES:
            break
    return total


def pack(engine, profile, data, locks, log):
    """Entradas presentes y miembros (nombre en el ZIP, archivo) dentro de los límites."""
    present, members, total = [], [], 0
    for entry in entries(data.get('files', [])):
        base = base_of(engine, profile, data, locks, entry)
        if crosses_link(base, entry['path']):
            raise YitoError(tr('La entrada {p1} atraviesa un enlace; no se admite', p1=(label(entry))))
        target = join(base, entry['path'])
        if not os.path.lexists(target):
            log(tr('No existe {p1}; se omite', p1=(label(entry))))
            continue
        skipped = []
        for path, source, length in gather(target, entry['path'], skipped):
            total += length
            members.append((member_prefix(entry) + path, source))
            if total > MAX_BYTES or len(members) > MAX_FILES:
                raise too_large()
        for path in skipped:
            log(tr('Enlace omitido: {p1}', p1=(path)))
        present.append(entry)
    return present, members


def write_archive(destination, manifest, members):
    import json
    destination = Path(destination)
    stream = destination.open('xb')
    try:
        with stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo(MANIFEST, (1980, 1, 1, 0, 0, 0)),
                             json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', zipfile.ZIP_DEFLATED)
            total = 0
            for name, source in members:
                content = source.read_bytes()
                total += len(content)
                if total > MAX_BYTES:
                    raise too_large()
                archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), content, zipfile.ZIP_DEFLATED)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def invalid(detail):
    return YitoError(tr('Archivo de profile no válido: {p1}', p1=(detail)))


def read_archive(source):
    """({manifest}, {(root, app, ruta): bytes}) validado por completo antes de instalar."""
    import json
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1:
            raise too_large()
        seen, manifest_info, files = set(), None, []
        for info in infos:
            name = info.filename
            if (info.is_dir() or info.flag_bits & 0x1 or '\\' in name or name.startswith('/')
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or any(part in ('', '.', '..') for part in name.split('/'))):
                raise invalid(name)
            if name.casefold() in seen:
                raise invalid(name)
            seen.add(name.casefold())
            if name == MANIFEST:
                manifest_info = info
            elif name.startswith('files/'):
                files.append(info)
            else:
                raise invalid(name)
        if manifest_info is None or manifest_info.file_size > 1024 * 1024:
            raise invalid(MANIFEST)
        try:
            manifest = json.loads(archive.read(manifest_info).decode('utf-8-sig'))
        except ValueError:
            raise invalid(MANIFEST) from None
        if (not isinstance(manifest, dict) or manifest.get('format') != 1
                or not set(manifest) <= {'format', 'apps', 'pinned', 'files'}):
            raise invalid(MANIFEST)
        declared = entries(manifest.get('files', []))
        contents, total = {}, 0
        for info in files:
            root, _, tail = info.filename[len('files/'):].partition('/')
            app = None
            if root == 'persist':
                bucket, _, rest = tail.partition('/')
                name, _, tail = rest.partition('/')
                app = component_key(f'{bucket}/{name}')
            if root not in ROOTS or not tail:
                raise invalid(info.filename)
            relative_path(tail)
            if not any(entry['root'] == root and entry.get('app') == app
                       and (tail == entry['path'] or tail.startswith(entry['path'] + '/')) for entry in declared):
                raise invalid(info.filename)
            total += info.file_size
            if total > MAX_BYTES:
                raise too_large()
            content = archive.read(info)
            if len(content) != info.file_size:
                raise invalid(info.filename)
            contents[(root, app, tail)] = content
    manifest['files'] = declared
    return manifest, contents


def plan(engine, name, records, contents):
    """Destino real y estado de cada archivo para la revisión previa a descargar."""
    from .isolation import isolated, persist_root
    workspace = engine.root / 'workspaces' / identifier(name)
    own_workspace = not workspace.exists()
    locks = {record['key']: record for record in records.values()}
    items = []
    for (root, app, path), content in sorted(contents.items(), key=lambda item: (item[0][0], item[0][1] or '', item[0][2])):
        if root == 'home':
            base, own = engine.root / 'home', False
        elif root == 'workspace':
            base, own = workspace, own_workspace
        else:
            if app not in locks:
                raise invalid(f'{app} · {path}')
            base, own = persist_root(engine.root, locks[app], name), isolated(locks[app])
        if crosses_link(base, path):
            raise YitoError(tr('La entrada {p1} atraviesa un enlace; no se admite', p1=(path)))
        target = join(base, path)
        if not os.path.lexists(target):
            state = 'new'
        elif not target.is_file():
            raise YitoError(tr('{p1} existe y no es un archivo', p1=(target)))
        else:
            state = 'same' if target.read_bytes() == content else 'different'
        items.append({'root': root, 'app': app, 'path': path, 'base': base, 'target': target,
                      'state': state, 'own': own, 'content': content})
    return items


def overwrite_in_place(target, content):
    # Same file identity: persist hardlinks keep pointing at the new content.
    with open(target, 'r+b') as stream:
        stream.write(content)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def write(engine, name, records, items, overwrite, log):
    """Caller holds the mutation lock. Revierte todo lo escrito si algo falla."""
    from .isolation import prepare_payload
    locks = {record['key']: record for record in records.values()}
    for app in dict.fromkeys(item['app'] for item in items if item['root'] == 'persist'):
        # Same preparation as activating: persist defaults exist before writing.
        prepare_payload(engine.root, locks[app], name)
    created, backups, folders, written = [], [], [], 0
    try:
        for item in items:
            target, content = item['target'], item['content']
            if crosses_link(item['base'], item['path']):
                raise YitoError(tr('La entrada {p1} atraviesa un enlace; no se admite', p1=(item['path'])))
            if os.path.lexists(target):
                if not target.is_file():
                    raise YitoError(tr('{p1} existe y no es un archivo', p1=(target)))
                current = target.read_bytes()
                if current == content:
                    continue
                if not item['own'] and (item['state'] != 'different' or str(target) not in overwrite):
                    log(tr('Se conserva {p1}: ya existe con otro contenido', p1=(target)))
                    continue
                backups.append((target, current))
                overwrite_in_place(target, content)
            else:
                missing = []
                parent = target.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for folder in reversed(missing):
                    folder.mkdir()
                    folders.append(folder)
                fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                created.append(target)
            written += 1
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        for target, content in backups:
            overwrite_in_place(target, content)
        for folder in reversed(folders):
            try:
                folder.rmdir()
            except OSError:
                pass
        raise
    if written:
        log(tr('{p1} archivos de configuración escritos', p1=(written)))


def save(engine, profile, values):
    """Guarda la tabla validada conservando el resto del profile."""
    with mutation(engine.root):
        data = engine.profile(profile)
        validated = entries(values)
        if validated:
            data['files'] = validated
        else:
            data.pop('files', None)
        engine.save_profile(profile, data)
    return validated
