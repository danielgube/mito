"""Optional repository of exported profiles (`profiles.repository` in config.properties).

The source is a Git HTTP/HTTPS repository or an absolute local folder holding files
exported by MITO (*.mito.json, *.mito.zip). Git is copied as a snapshot to
core/cache/profile-repository without checkout, hooks or submodules; a local folder
is read in place. Nothing is executed: installing an entry is the same import, review
and confirmation as Importar, retained versions included.
"""
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .i18n import tr
from .network import properties, validate_url
from .storage import YitoError, identifier, inside, mutation

KEY = 'profiles.repository'
CACHE = 'core/cache/profile-repository'
SUFFIXES = ('.mito.json', '.mito.zip')
MAX_ENTRIES = 500
MAX_SIZE = 4 * 1024 * 1024


def validate_source(value):
    value = value.strip()
    if value.startswith(('http://', 'https://')):
        validate_url(value)
    elif not Path(value).is_absolute():
        raise YitoError(tr('{p0}: usa una URL Git HTTP/HTTPS o una carpeta local absoluta', p0=KEY))
    return value


def configured(root):
    value = properties(root).get(KEY, '').strip()
    return validate_source(value) if value else None


def suggested_name(filename):
    stem = filename
    for suffix in SUFFIXES:
        if stem.casefold().endswith(suffix):
            stem = stem[:-len(suffix)]
    try:
        return identifier(stem)
    except YitoError:
        return ''


def scan(folder):
    """Exported profiles under folder, without traversing links or .git."""
    from .profile_transfer import read_export
    folder = Path(folder)
    result = []
    for current, directories, files in os.walk(folder, followlinks=False):
        directories[:] = sorted(name for name in directories if name != '.git'
                                and not stat.S_ISLNK(os.lstat(Path(current, name)).st_mode)
                                and not Path(current, name).is_junction())
        for name in sorted(files):
            if not name.casefold().endswith(SUFFIXES):
                continue
            path = Path(current, name)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIZE:
                continue
            item = {'name': suggested_name(name), 'path': path,
                    'relative': path.relative_to(folder).as_posix(), 'apps': [], 'pinned': {},
                    'files': 0, 'error': None}
            try:
                shared = read_export(path)
                item.update(apps=shared['apps'], pinned=shared['pinned'], files=len(shared.get('files', [])))
            except (OSError, ValueError, YitoError) as exc:
                item['error'] = str(exc)
            result.append(item)
            if len(result) >= MAX_ENTRIES:
                return result
    return result


def sync(engine, log=print):
    """Sincroniza el repositorio configurado y devuelve sus profiles."""
    source = configured(engine.root)
    if not source:
        raise YitoError(tr('Configura {p0} en Configuración para usar un repositorio de profiles.', p0=KEY))
    if not source.startswith(('http://', 'https://')):
        if not Path(source).is_dir():
            raise YitoError(tr('No existe la carpeta {p1}', p1=source))
        return scan(source)
    from .pocketools import git_snapshot
    with mutation(engine.root):
        engine.initialize()
        destination = inside(engine.root, CACHE)
        if destination.is_symlink() or destination.is_junction():
            raise YitoError(tr('La carpeta de destino no puede ser un enlace'))
        with tempfile.TemporaryDirectory(dir=engine.root/'core/staging', prefix='profiles-',
                                         ignore_cleanup_errors=True) as temporary:
            stage = Path(temporary)
            payload = stage/'payload'
            log(tr('Sincronizando el repositorio de profiles…'))
            git_snapshot(engine, source, stage, payload)
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = stage/'previous'
            if destination.exists():
                destination.rename(backup)
            try:
                payload.rename(destination)
            except OSError:
                if backup.exists():
                    backup.rename(destination)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
    return scan(destination)
