"""User-owned Pocketool sources. Saving never executes a command."""
import hashlib
import os
from pathlib import Path
import re
import tempfile

from .i18n import tr
from .storage import YitoError, identifier, inside, mutation, write_json
from .pocketools import info, sources, update, source_path, validate_local_tree

MAX_SCRIPT = 1024 * 1024


def script_template(engine, kind='cmd', profile=None):
    """Document managed environment only; never snapshot user variables or secrets."""
    if profile is None:
        values = {'MITO_PROFILE': '<profile>', 'MITO_ROOT': str(engine.root),
                  'MITO_WORKSPACE': str(engine.root / 'workspaces' / '<workspace>'),
                  'HOME': str(engine.root / 'home')}
    else:
        values = engine.context_variables(profile)
    prefix = '::' if kind == 'cmd' else '# '
    header = '@echo off\nchcp 65001 >nul\n\n' if kind == 'cmd' else '# PowerShell\n\n'
    lines = [tr('Valores de referencia; las variables se calculan con el profile al ejecutar.')]
    lines += [f'{key}={value}' for key, value in values.items()]
    lines.append(tr('Usa %MITO_WORKSPACE% en CMD o $env:MITO_WORKSPACE en PowerShell.'))
    return header + '\n'.join(prefix + line for line in lines) + '\n\n'


def command_filename(name, kind):
    if kind not in ('cmd', 'ps1') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise YitoError(tr('Usa un nombre sin extensión: letras, números, guion o guion bajo.'))
    identifier(name)  # Reserved Windows device names.
    return name + '.' + kind


def checked_folder(engine, value):
    folder = Path(value)
    if not folder.is_absolute():
        raise YitoError(tr('Selecciona una carpeta con ruta absoluta.'))
    for parent in (folder, *folder.parents):
        if parent.is_symlink() or parent.is_junction():
            raise YitoError(tr('La pocketool contiene enlaces no admitidos'))
    folder = folder.resolve()
    if any(part.casefold() == '.git' for part in folder.parts):
        raise YitoError(tr('Elige una carpeta de trabajo, fuera de las copias y archivos internos de MITO.'))
    root = engine.root.resolve()
    if folder.is_relative_to(root / 'pocketools'):
        if folder.parent != root / 'pocketools':
            raise YitoError(tr('La carpeta local debe coincidir con el nombre del paquete.'))
        identifier(folder.name)
    if root.is_relative_to(folder) or any(folder.is_relative_to(root / name)
            for name in ('core', 'profiles', 'logs', 'update', '.git')):
        raise YitoError(tr('Elige una carpeta de trabajo, fuera de las copias y archivos internos de MITO.'))
    return folder


def source_folder(engine, name):
    source = sources(engine.root).get(identifier(name), '')
    if not source or source.startswith(('http://', 'https://')):
        raise YitoError(tr('Este paquete necesita una carpeta de trabajo local para editarlo.'))
    folder = checked_folder(engine, source_path(engine.root, source))
    if folder.is_relative_to(engine.root / 'pocketools') and folder.name.casefold() != identifier(name):
        raise YitoError(tr('La carpeta local debe coincidir con el nombre del paquete.'))
    info(folder)
    return folder


def is_repository(folder):
    return (folder / '.git').exists()


def commands(folder):
    """Only root CMD/PowerShell entries are exposed; support files remain opaque."""
    if not folder.is_dir():
        return []
    from .pocketool_execution import generated_launcher
    return sorted((p.name for p in folder.iterdir()
        if p.suffix.lower() in ('.cmd', '.ps1') and p.is_file()
        and not p.is_symlink() and not p.is_junction() and not generated_launcher(p)), key=str.casefold)


def script_path(folder, filename):
    if not isinstance(filename, str) or not filename or Path(filename).name != filename or Path(filename).suffix.lower() not in ('.cmd', '.ps1'):
        raise YitoError(tr('Nombre de comando no válido: {p1}', p1=filename))
    path = inside(folder, filename)
    if path.is_symlink() or path.is_junction():
        raise YitoError(tr('La pocketool contiene enlaces no admitidos'))
    return path


def revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def read_script(engine, name, filename):
    path = script_path(source_folder(engine, name), filename)
    if not path.is_file() or path.stat().st_size > MAX_SCRIPT:
        raise YitoError(tr('El editor admite scripts de hasta 1 MiB.'))
    data = path.read_bytes()
    # Never silently damage an existing ANSI script. The user can edit it externally.
    try:
        text = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError as exc:
        raise YitoError(tr('El editor requiere UTF-8. Convierte el archivo o edítalo desde su carpeta.')) from exc
    return text, hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.mito-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register(engine, name, folder, description, mode='existing', url='', replace_source=None):
    """Create/adopt a source, then configure and publish it. Existing content is kept."""
    from .configuration import Configuration
    from .pocketool_git import clone, initialize, repository
    name = identifier(name)
    folder = checked_folder(engine, folder)
    live = folder.parent == engine.root / 'pocketools'
    if live and folder.name.casefold() != name:
        raise YitoError(tr('La carpeta local debe coincidir con el nombre del paquete.'))
    if mode not in ('existing', 'new', 'git', 'clone', 'init'):
        raise YitoError(tr('Modo de creación no válido.'))
    config = Configuration(engine)
    configured = sources(engine.root)
    if name in configured and configured[name] != replace_source:
        raise YitoError(tr('Pocketool repetida: {p1}', p1=name))
    if replace_source is not None and configured.get(name) != replace_source:
        raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
    # Never alias a source as two packages (case insensitive on Windows).
    for key, value in configured.items():
        if key != name and not value.startswith(('http://', 'https://')) and source_path(engine.root, value).resolve() == folder:
            raise YitoError(tr('Esta carpeta ya pertenece al paquete {p1}.', p1=key))
    marker = folder / 'pocketool.json'
    if not marker.exists() and not description.strip() and mode != 'clone':
        raise YitoError(tr('Introduce una descripción para el paquete.'))
    with mutation(engine.root, 'pocketool-author.lock'):
        if mode == 'clone':
            clone(engine, url, folder)
        elif mode in ('new', 'git'):
            if folder.exists():
                raise YitoError(tr('La carpeta ya existe. Elige usar una carpeta existente.'))
            folder.mkdir(parents=True)
            if mode == 'git':
                initialize(engine, folder, url)
        elif not folder.is_dir():
            raise YitoError(tr('Selecciona una carpeta existente'))
        if mode == 'init' and not is_repository(folder):
            initialize(engine, folder, url)
        if is_repository(folder):
            git = repository(engine, folder)
            if url and mode != 'clone':
                from .network import validate_url
                validate_url(url)
                remotes = git.text(['remote']).splitlines()
                if 'origin' in remotes:
                    if git.text(['remote', 'get-url', 'origin']) != url:
                        raise YitoError(tr('La URL no coincide con origin. Cambia el remoto desde Git.'))
                else:
                    git.run(['remote', 'add', 'origin', url])
        if marker.exists():
            info(folder)  # Preserve existing info and extension fields.
        else:
            if not description.strip():
                raise YitoError(tr('Introduce una descripción para el paquete.'))
            write_json(marker, {'info': description.strip()})
        # Configuration.save performs an optimistic check and its own mutation lock.
        values = {k: v for k, v in config.values.items()
                  if not (k.startswith('pocketool.') and identifier(k[10:]) == name)}
        local_source = 'pocketools/' + name if live else str(folder)
        values['pocketool.' + name] = local_source
        config.save(values.items())
    update(engine, name, log=lambda _: None, expected_source=local_source)
    return name


def create_package(engine, name, description, git=False, url=''):
    name = identifier(name)
    folder = inside(engine.root / 'pocketools', name)
    if name in sources(engine.root):
        raise YitoError(tr('Pocketool repetida: {p1}', p1=name))
    # Empty folders left by a manual mkdir or an interrupted creation can be reused.
    mode = 'git' if git else 'new'
    if folder.is_dir() and not any(folder.iterdir()):
        mode = 'init' if git else 'existing'
    elif folder.exists():
        raise YitoError(tr('La carpeta ya contiene archivos. Usa Comandos si el paquete está registrado o elige otro nombre.'))
    return register(engine, name, folder, description, mode, url if git else '')


def edit_local_copy(engine, name):
    """Keep an installed snapshot's files and attach the remote Git history for editing."""
    from .pocketool_git import clone, repository
    name = identifier(name)
    source = sources(engine.root).get(name, '')
    if not source.startswith(('http://', 'https://')):
        return source_folder(engine, name)
    folder = checked_folder(engine, engine.root / 'pocketools' / name)
    with mutation(engine.root, 'pocketool-author.lock'):
        if not is_repository(folder):
            with tempfile.TemporaryDirectory(dir=engine.root / 'core/staging', prefix='pocketool-edit-') as tmp:
                checkout = Path(tmp) / 'repo'
                clone(engine, source, checkout)
                with mutation(engine.root):
                    if sources(engine.root).get(name) != source:
                        raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
                    if folder.exists():
                        validate_local_tree(folder)
                        (checkout / '.git').rename(folder / '.git')
                    else:
                        validate_local_tree(checkout)
                        folder.parent.mkdir(parents=True, exist_ok=True)
                        checkout.rename(folder)
        if repository(engine, folder).text(['remote', 'get-url', 'origin']) != source:
            raise YitoError(tr('La URL no coincide con origin. Cambia el remoto desde Git.'))
    register(engine, name, folder, '', replace_source=source)
    return folder


def save_script(engine, package, name, kind, text, original=None, expected=None, expected_folder=None):
    """Optimistic edits protect external changes. Publish failure keeps the source."""
    filename = command_filename(name, kind)
    if not text.strip() or '\0' in text:
        raise YitoError(tr('Introduce el contenido del script, sin caracteres nulos.'))
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    data = normalized.replace('\n', '\r\n').encode('utf-8')
    if kind == 'ps1':
        data = b'\xef\xbb\xbf' + data  # Windows PowerShell 5.1 recognizes UTF-8 with BOM.
    if len(data) > MAX_SCRIPT:
        raise YitoError(tr('El editor admite scripts de hasta 1 MiB.'))
    with mutation(engine.root, 'pocketool-author.lock'):
        configured_source = sources(engine.root).get(identifier(package))
        folder = source_folder(engine, package)
        if (expected_folder is not None and folder != expected_folder) or source_path(engine.root, configured_source).resolve() != folder:
            raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
        target = script_path(folder, filename)
        old = script_path(folder, original) if original else None
        if old and revision(old) != expected:
            raise YitoError(tr('El script cambió fuera del editor. Vuelve a abrirlo antes de guardar.'))
        from .pocketool_execution import generated_launcher
        replacing_wrapper = old is not None and old.suffix.lower() == '.ps1' and target == old.with_suffix('.cmd') and target.is_file() and generated_launcher(target)
        if target.exists() and (old is None or target != old) and not replacing_wrapper:
            raise YitoError(tr('Ya existe el comando {p1}.', p1=filename))
        for existing in commands(folder):
            if Path(existing).stem.casefold() == name.casefold() and (old is None or folder / existing != old):
                raise YitoError(tr('Ya existe el comando {p1}.', p1=existing))
        atomic_bytes(target, data)
        if old and old != target:
            old.unlink()
        result = {'filename': filename, 'revision': hashlib.sha256(data).hexdigest(), 'publish_error': None}
    try:
        update(engine, package, log=lambda _: None, expected_source=configured_source)
    except (YitoError, OSError, ValueError) as exc:
        result['publish_error'] = str(exc)
    return result


def open_source(engine, name):
    import subprocess
    subprocess.Popen(['explorer.exe', str(source_folder(engine, name))])
