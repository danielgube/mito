"""External executables launched with the selected MITO profile environment."""
from .i18n import tr
import hashlib
import json
from pathlib import Path
import subprocess

from .storage import YitoError, inside, identifier, mutation, read_json, write_json


EXTENSIONS = {'.exe', '.bat', '.cmd', '.ps1'}


def inventory(root):
    path = root/'core/state/host-applications.json'
    return read_json(path) if path.exists() else {}


def add(root, filename):
    path = Path(filename)
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise YitoError(tr('Selecciona un archivo .exe, .bat, .cmd o .ps1 existente'))
    path = path.resolve()
    identity = hashlib.sha256(str(path).casefold().encode()).hexdigest()[:16]
    with mutation(root):
        applications = inventory(root)
        applications[identity] = str(path)
        write_json(root/'core/state/host-applications.json', applications)
    return identity


# CSIDL folders with the host's own shortcuts: Start menu (user, all users) and
# desktop (user, all users). Read through the shell, not APPDATA/USERPROFILE,
# which point into MITO's home when the manager runs with a profile environment.
SHORTCUT_FOLDERS = (2, 23, 16, 25)


def shortcut_folders():
    import ctypes
    folders = []
    for code in SHORTCUT_FOLDERS:
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.shell32.SHGetFolderPathW(None, code, None, 0, buffer) and buffer.value:
            folders.append(Path(buffer.value))
    return folders


def uninstaller(path):
    name = Path(path).stem.casefold()
    return name.startswith('unins') or 'uninstall' in name or 'desinstal' in name


def discover(root, folders=None, resolve=None):
    """[(label, executable)] from the host's Start menu and desktop shortcuts.

    Lightweight and generic: only .lnk files, resolved in one call; keeps
    existing .exe/.bat/.cmd/.ps1 targets outside MITO, skips uninstallers and
    applications already added. The label is the shortcut name."""
    if resolve is None:
        from .relocation import link_targets as resolve
    links = []
    for folder in shortcut_folders() if folders is None else folders:
        if folder.is_dir():
            links += [path for path in folder.rglob('*.lnk') if path.is_file()]
    if not links:
        return []
    targets = resolve(links)
    known = {str(Path(value)).casefold() for value in inventory(root).values()}
    mito = str(Path(root).resolve()).casefold()
    found = {}
    for link in sorted(links, key=lambda path: path.stem.casefold()):
        value = targets.get(str(link)) or ''
        if not value:
            continue
        path = Path(value)
        key = str(path).casefold()
        if (key in found or key in known or key.startswith(mito + '\\') or path.suffix.lower() not in EXTENSIONS
                or uninstaller(path) or uninstaller(link) or not path.is_file()):
            continue
        found[key] = (link.stem, str(path))
    return sorted(found.values(), key=lambda item: item[0].casefold())


def target(root, identity):
    applications = inventory(root)
    if identity not in applications:
        raise YitoError(tr('La aplicación del host ya no está registrada en MITO'))
    return Path(applications[identity])


def remove(root, identity):
    with mutation(root):
        from .host_data import KINDS, unlink_locked
        for kind in KINDS:
            unlink_locked(root, identity, kind)
        applications = inventory(root)
        applications.pop(identity, None)
        write_json(root/'core/state/host-applications.json', applications)


def launch(engine, profile, identity, arguments=(), coupled=False, owned=False):
    path = target(engine.root, identity)
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise YitoError(tr('El archivo de la aplicación del host ya no está disponible'))
    env = engine.environment(profile)
    system = Path(next(value for key, value in env.items() if key.upper() == 'SYSTEMROOT'))/'System32'
    workspace = inside(engine.root/'workspaces', engine.profile(profile)['workspace'])
    workspace.mkdir(parents=True, exist_ok=True)
    if coupled:
        if owned:
            raise YitoError(tr('Modo de lanzamiento no válido'))
        from .terminal import launch_host
        return launch_host(engine, profile, identity, path.stem, arguments)
    flags = 0
    if path.suffix.lower() in ('.bat', '.cmd'):
        from .jobs import batch_command
        command = batch_command(path, arguments, env)
        flags = subprocess.CREATE_NEW_CONSOLE
    elif path.suffix.lower() == '.ps1':
        command = [str(system/'WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile',
            '-ExecutionPolicy', 'Bypass', '-File', str(path), *arguments]
        flags = subprocess.CREATE_NEW_CONSOLE
    else:
        command = [str(path), *arguments]
    if owned:
        from .processes import ProcessJob
        group = ProcessJob()
        try:
            process = group.spawn(command, cwd=workspace, env=env, creationflags=0)
            result = process.wait()
            group.wait_empty()
            return result
        finally:
            group.close()
    return subprocess.Popen(command, cwd=workspace, env=env, creationflags=flags)


def choose_file():
    script = r'''
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'MITO - Agregar aplicación del host'
$dialog.Filter = 'Aplicaciones y scripts (*.exe;*.bat;*.cmd;*.ps1)|*.exe;*.bat;*.cmd;*.ps1'
$dialog.InitialDirectory = [Environment]::GetFolderPath('UserProfile')
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -InputObject $dialog.FileName -Compress
    }
} finally { $dialog.Dispose() }
'''
    from .native_ui import localize_script
    script = localize_script(script, 'MITO - Agregar aplicación del host',
        'Aplicaciones y scripts (*.exe;*.bat;*.cmd;*.ps1)|*.exe;*.bat;*.cmd;*.ps1')
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', script],
                            capture_output=True, text=True,
                            encoding='utf-8', check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.stdout.strip() else None


def shortcut(engine, profile, identity, badge=None, coupled=False, arguments=(), color=None):
    from .shortcuts import application_icon, badge_text, badge_color, desktop_path, write_link
    with mutation(engine.root):
        profile = identifier(profile)
        engine.profile(profile)
        path = target(engine.root, identity)
        if not path.is_file():
            raise YitoError(tr('El archivo de la aplicación del host ya no está disponible'))
        badge = badge_text(engine, badge)
        color = badge_color(engine, color)
        tag = hashlib.sha256(f'{profile}/{identity}'.encode()).hexdigest()[:16]
        icon = application_icon(engine, f'host-{tag}-{hashlib.sha256(badge.encode()).hexdigest()[:8]}', badge, path, color=color)
        desktop = desktop_path()
        desktop.mkdir(parents=True, exist_ok=True)
        registry = engine.root/'core/state/host-shortcuts'/f'{tag}.json'
        previous = read_json(registry) if registry.exists() else {}
        stem = f'MITO - {profile} - Host - {path.stem}'
        link, index = desktop/f'{stem}.lnk', 2
        while link.exists() and str(link) != previous.get('path'):
            link = desktop/f'{stem} ({index}).lnk'
            index += 1
        argv = [str(engine.root/'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8',
                '-m', 'mito', 'host-run', identity, '--profile', profile]
        if coupled:
            argv.append('--coupled')
        if arguments:
            argv += ['--', *arguments]
        write_link(link, argv, path.parent, icon)
        write_json(registry, {'path': str(link), 'badge': badge, 'color': color, 'profile': profile,
            'application': identity, 'coupled': coupled, 'arguments': list(arguments)})
        return link
