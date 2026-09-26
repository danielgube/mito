"""Explicit application shortcuts on the Windows desktop; icons stay in core."""
from .i18n import tr
import subprocess
import hashlib
from pathlib import Path
from .storage import YitoError, identifier, inside, read_json, write_json


def badge_text(engine, value=None):
    if value is None and engine is not None:
        config = engine.root / 'config.properties'
        if config.exists():
            for line in config.read_text(encoding='utf-8-sig').splitlines():
                if line.startswith('shortcut.badgeText='):
                    value = line.split('=', 1)[1]
    value = (value or 'MITO').strip().upper() or 'MITO'
    if len(value) > 4 or not value.isalnum():
        raise YitoError(tr('La etiqueta admite de 1 a 4 letras o números'))
    return value


def badge_color(engine, value=None):
    from .icons import BADGE_COLORS, DEFAULT_BADGE_COLOR
    if value is None and engine is not None:
        config = engine.root / 'config.properties'
        if config.exists():
            for line in config.read_text(encoding='utf-8-sig').splitlines():
                if line.startswith('shortcut.badgeColor='):
                    value = line.split('=', 1)[1]
    value = (value or DEFAULT_BADGE_COLOR).strip().lower()
    if value not in BADGE_COLORS:
        raise YitoError(tr('Elige un color de la paleta del icono'))
    return value


def application_icon(engine, name, badge=None, base=None, color=None):
    from .icons import create_mito_shortcut_icon
    badge = badge_text(engine, badge)
    color = badge_color(engine, color)
    fallback = engine.root / 'core/assets/branding/mito.ico'
    directory = engine.root / 'core/state/icons'
    try:
        return create_mito_shortcut_icon(base or fallback, directory, identifier(name), badge, color)
    except YitoError:
        if base is None or base == fallback:
            raise
        return create_mito_shortcut_icon(fallback, directory, identifier(name), badge, color)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def component_icon_source(engine, lock, profile, shortcut=None):
    """Resolve the icon of the same entry selected for launching this component."""
    from .catalog import effective
    from .isolation import payload_path
    executable, arguments = engine.application_target(lock) if shortcut is None else engine.application_target(lock, shortcut)
    payload = payload_path(engine.root, lock, profile)
    from .arguments import split
    for shortcut in effective(lock['manifest']).get('shortcuts', []):
        if shortcut[0] == executable and (split(shortcut[2]) if len(shortcut) > 2 else []) == arguments:
            if len(shortcut) > 3 and shortcut[3]:
                icon = engine.component_path(payload, shortcut[3])
                if icon.is_file():
                    return icon
            break
    return engine.component_path(payload, executable)


def write_link(path, argv, root, icon):
    commands = ['$ws = New-Object -ComObject WScript.Shell',
        f'$lnk = $ws.CreateShortcut({literal(path)})',
        f'$lnk.TargetPath = {literal(argv[0])}',
        f'$lnk.Arguments = {literal(subprocess.list2cmdline(argv[1:]))}',
        f'$lnk.WorkingDirectory = {literal(root)}',
        f'$lnk.IconLocation = {literal(icon)}', '$lnk.Save()']
    subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', ';\n'.join(commands)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def retarget_link(path, old_targets, old_arguments, argv):
    """Update only a matching generated link; preserve its icon and shell properties."""
    commands = ["$ErrorActionPreference = 'Stop'", '$ws = New-Object -ComObject WScript.Shell',
        f'$lnk = $ws.CreateShortcut({literal(path)})',
        '$targets = @(' + ', '.join(literal(target) for target in old_targets) + ')',
        f'$arguments = {literal(subprocess.list2cmdline(argv[1:]))}',
        # If a previous attempt saved the .lnk but not its registry, finish safely.
        f'if ($lnk.TargetPath -ieq {literal(argv[0])} -and $lnk.Arguments -ceq $arguments) {{ "updated"; exit }}',
        f'if ($targets -notcontains $lnk.TargetPath -or $lnk.Arguments -cne {literal(subprocess.list2cmdline(old_arguments))}) {{ exit }}',
        f'$lnk.TargetPath = {literal(argv[0])}',
        '$lnk.Arguments = $arguments', '$lnk.Save()', '"updated"']
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', ';\n'.join(commands)],
                            check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.stdout.strip() == 'updated'


def desktop_path():
    """Windows Known Folder API respects OneDrive and redirected desktops."""
    import ctypes
    import uuid
    folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('B4BFCC3A-DB2C-424C-B029-7FE99A87C641').bytes_le)
    shell = ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) != 0:
        raise YitoError(tr('No se pudo localizar el escritorio de Windows'))
    try:
        return Path(ctypes.wstring_at(pointer.value))
    finally:
        ole = ctypes.WinDLL('ole32')
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree(pointer)


def application_link(engine, profile, installation, badge=None, coupled=False, arguments=(), shortcut=None, color=None):
    name = identifier(profile)
    data = engine.profile(name)
    if installation not in data['apps']:
        raise YitoError(tr('El componente debe estar activo en el profile'))
    lock = engine.selected(data)[installation]
    badge = badge_text(engine, badge)
    color = badge_color(engine, color)
    app = lock['key']
    tag = hashlib.sha256(f'{name}/{app}'.encode()).hexdigest()[:16]
    if shortcut is not None:
        engine.application_target(lock, shortcut)
        tag = hashlib.sha256(f'{name}/{app}/{shortcut}'.encode()).hexdigest()[:16]
    icon_name = f'app-{tag}-{hashlib.sha256(badge.encode()).hexdigest()[:8]}'
    target = component_icon_source(engine, lock, name, shortcut)
    icon = application_icon(engine, icon_name, badge, target, color=color)
    destination = desktop_path()
    destination.mkdir(parents=True, exist_ok=True)
    registry = engine.root / 'core/state/application-shortcuts' / f'{tag}.json'
    old = read_json(registry) if registry.exists() else {}
    stem = f'{name} - {app.replace("/", "-")}'
    if shortcut is not None:
        # Labels can contain Scoop start-menu subdirectories, never desktop paths.
        label = ''.join('_' if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in shortcut).strip(' .')[:80]
        stem += f' - {label}'
    path = destination / f'{stem}.lnk'
    index = 2
    while path.exists() and str(path) != old.get('path'):
        path = destination / f'{stem} ({index}).lnk'
        index += 1
    argv = [str(engine.root/'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', 'run']
    if coupled:
        argv.append('--coupled')
    argv += ['--profile', name, '--app', app]
    if shortcut is not None:
        argv += ['--shortcut', shortcut]
    if arguments:
        argv += ['--', *arguments]
    write_link(path, argv, engine.root, icon)
    write_json(registry, {'path': str(path), 'profile': name, 'app': app, 'badge': badge, 'color': color,
        'coupled': coupled, 'arguments': list(arguments), 'shortcut': shortcut})
    return path


def migrate_application_links(engine, old_id, new_id, lock, backups, log):
    """Retarget only registered existing links, preserving filename and badge."""
    folder = engine.root/'core/state/application-shortcuts'
    for registry in sorted(folder.glob('*.json')):
        data = read_json(registry)
        if data.get('app', data.get('installation')) != old_id:
            continue
        profile = data['profile']
        if profile not in engine.profiles():
            log(tr('Acceso de profile eliminado conservado sin modificar: {p1}', p1=(profile)))
            continue
        path = Path(data['path'])
        if not path.is_file():
            continue
        if path.suffix.lower() != '.lnk' or path.is_symlink():
            raise YitoError(tr('Acceso registrado no válido'))
        app = lock['key']
        tag = hashlib.sha256(f'{profile}/{app}'.encode()).hexdigest()[:16]
        shortcut = data.get('shortcut')
        if shortcut is not None:
            engine.application_target(lock, shortcut)
            tag = hashlib.sha256(f'{profile}/{app}/{shortcut}'.encode()).hexdigest()[:16]
        new_registry = folder/f'{tag}.json'
        # An independently created link for the new version remains registered.
        destination_registry = registry if new_registry.exists() else new_registry
        for item in (path, registry, destination_registry):
            backups.setdefault(item, item.read_bytes() if item.exists() else None)
        badge = badge_text(engine, data.get('badge'))
        color = badge_color(engine, data.get('color', 'naranja'))
        icon = application_icon(engine, f'app-{tag}-{hashlib.sha256(badge.encode()).hexdigest()[:8]}', badge, component_icon_source(engine, lock, profile, shortcut), color=color)
        argv = [str(engine.root/'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', 'run']
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
        write_link(path, argv, engine.root, icon)
        updated = dict(data, app=app)
        updated.pop('installation', None)
        write_json(destination_registry, updated)
        if destination_registry != registry:
            registry.unlink()


def mito_link(engine, profile, badge=None, color=None):
    name = identifier(profile)
    engine.profile(name)
    badge = badge_text(engine, badge)
    color = badge_color(engine, color)
    icon = application_icon(engine, f'mito-{name}', badge, color=color)
    destination = desktop_path()
    destination.mkdir(parents=True, exist_ok=True)
    registry = engine.root / 'core/state/mito-shortcuts' / f'{name}.json'
    old = read_json(registry) if registry.exists() else {}
    stem = f'MITO - {name}'
    path = destination / f'{stem}.lnk'
    index = 2
    while path.exists() and str(path) != old.get('path'):
        path = destination / f'{stem} ({index}).lnk'
        index += 1
    # Launch the private Terminal directly so Windows can associate its taskbar
    # window with this branded shortcut. The profile still only selects the
    # manager's initial state; Terminal runs the private Python child itself.
    from .terminal import command, prepare
    prepare(engine)
    argv = command(engine, selected_profile=name)
    write_link(path, argv, engine.root, icon)
    write_json(registry, {'path': str(path), 'profile': name, 'badge': badge, 'color': color})
    return path
