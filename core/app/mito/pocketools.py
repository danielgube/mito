"""Shared script packages: validated snapshots, no dependency or hook execution."""
from .i18n import tr
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .network import properties, proxy_environment, validate_url
from .storage import YitoError, identifier, inside, mutation, read_json
from .transfer import extract_zip


def validate_source(source):
    if source.startswith(('http://', 'https://')):
        validate_url(source)
    elif re.fullmatch(r'pocketools/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', source):
        identifier(source.split('/')[1])
    elif not Path(source).is_absolute():
        raise YitoError(tr('Pocketool: usa una URL Git HTTP/HTTPS o una carpeta local absoluta'))
    return source


def source_path(root, source):
    """The single portable local form is pocketools/<package>."""
    validate_source(source)
    if source.startswith(('http://', 'https://')):
        raise YitoError(tr('Este paquete necesita una carpeta de trabajo local para editarlo.'))
    return inside(root, source) if not Path(source).is_absolute() else Path(source)


def is_live_source(root, name, source):
    return not source.startswith(('http://', 'https://')) and (
        source_path(root, source).resolve() == inside(root / 'pocketools', identifier(name)).resolve())


def sources(root):
    result = {}
    for key, value in properties(root).items():
        if key.startswith('pocketool.'):
            name = identifier(key[len('pocketool.'):])
            if name in result:
                raise YitoError(tr('Pocketool repetida: {p1}', p1=(name)))
            result[name] = validate_source(value)
            if not value.startswith(('http://', 'https://')) and not Path(value).is_absolute():
                if value != 'pocketools/' + name:
                    raise YitoError(tr('La carpeta local debe coincidir con el nombre del paquete.'))
    return result


def info(folder):
    marker = folder/'pocketool.json'
    if not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 65536:
        raise YitoError(tr('Falta un pocketool.json válido en la raíz del paquete'))
    try:
        data = read_json(marker)
    except (OSError, ValueError) as exc:
        raise YitoError(tr('pocketool.json no contiene JSON válido')) from exc
    if not isinstance(data, dict) or not isinstance(data.get('info'), str) or not data['info'].strip():
        raise YitoError(tr('pocketool.json requiere un campo info de texto no vacío'))
    return data['info'].strip()


def paths(root):
    result = []
    for name in sources(root):
        folder = inside(root/'pocketools', name)
        try:
            if folder.is_symlink() or folder.is_junction():
                continue
            info(folder)
        except (YitoError, OSError):
            continue
        result.append(str(folder))
    return result


def git_executable(engine):
    found = shutil.which('git.exe')
    if found:
        return found
    # Resolve a tool by its declared commands/PATH, independent of package name.
    from .catalog import effective, array
    for record in engine.installed().values():
        manifest = effective(record['manifest'])
        payload = inside(engine.root, record['path'])
        candidates = [inside(payload, path) for path, alias, _ in engine.bins(manifest) if alias.lower() == 'git']
        candidates += [inside(payload, path)/'git.exe' for path in array(manifest.get('env_add_path'))]
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() == '.exe':
                return str(candidate)
    raise YitoError(tr('Se necesita Git para sincronizar repositorios remotos. Instálalo en MITO o en el host.'))


def git_snapshot(engine, source, stage, payload):
    executable = git_executable(engine)
    environment = proxy_environment(engine.root, dict(os.environ))
    environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        environment.pop(key, None)
    empty = stage/'empty-template'
    empty.mkdir()
    repository = stage/'repository'
    archive = stage/'snapshot.zip'

    def run(arguments):
        try:
            result = subprocess.run([executable, '-c', f'core.hooksPath={empty}', *arguments],
                                    env=environment, cwd=stage, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=180,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise YitoError(tr('Git agotó el tiempo de sincronización')) from exc
        if result.returncode:
            raise YitoError('Git: ' + result.stderr.strip()[-3000:])

    # No checkout: repository attributes cannot trigger host smudge filters.
    run(['clone', '--depth=1', '--single-branch', '--no-checkout', '--no-recurse-submodules',
         f'--template={empty}', '--', source, str(repository)])
    run(['-C', str(repository), 'archive', '--format=zip', f'--output={archive}', 'HEAD'])
    extract_zip(archive, payload)


def validate_local_tree(source):
    source = Path(source).resolve()
    info(source)
    # Check before copying: never traverse junctions or symbolic links.
    for current, directories, files in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name != '.git']
        for name in directories + files:
            path = Path(current)/name
            if path.is_symlink() or path.is_junction():
                raise YitoError(tr('La pocketool contiene enlaces no admitidos'))
            inside(source, path.relative_to(source).as_posix())


def local_snapshot(source, payload):
    source = Path(source).resolve()
    if payload.resolve().is_relative_to(source):
        raise YitoError(tr('La carpeta de origen no puede contener el staging de MITO'))
    validate_local_tree(source)
    shutil.copytree(source, payload, ignore=shutil.ignore_patterns('.git'))


def update(engine, name, log=print, *, expected_source=None):
    name = identifier(name)
    with mutation(engine.root):
        engine.initialize()
        configured = sources(engine.root)
        if name not in configured:
            raise YitoError(tr('Pocketool no configurada: {p1}', p1=(name)))
        if expected_source is not None and configured[name] != expected_source:
            raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
        destination = inside(engine.root/'pocketools', name)
        if destination.is_symlink() or destination.is_junction():
            raise YitoError(tr('La carpeta de destino no puede ser un enlace'))
        from .pocketool_execution import prepare_commands
        if is_live_source(engine.root, name, configured[name]):
            validate_local_tree(destination)
            prepare_commands(destination)
            return info(destination)
        # A local repository must never be destroyed by replacing its snapshot.
        if (destination / '.git').exists():
            raise YitoError(tr('El paquete contiene un repositorio local. Actualízalo desde Git.'))
        with tempfile.TemporaryDirectory(dir=engine.root/'core/staging', prefix='pocketool-', ignore_cleanup_errors=True) as temporary:
            stage = Path(temporary)
            payload = stage/'payload'
            log(tr('Sincronizando pocketool {p1}…', p1=(name)))
            source = configured[name]
            if source.startswith(('http://', 'https://')):
                git_snapshot(engine, source, stage, payload)
            else:
                local_snapshot(source_path(engine.root, source), payload)
            description = info(payload)
            from .pocketool_execution import prepare_commands
            prepare_commands(payload)
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
                try:
                    shutil.rmtree(backup)
                except OSError:
                    log(tr('Aviso: quedan archivos antiguos bloqueados en {p1}', p1=(backup)))
            log(tr('Pocketool {p1} actualizada. Disponible al abrir un nuevo entorno.', p1=(name)))
            return description
