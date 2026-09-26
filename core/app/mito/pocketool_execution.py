"""Resolve installed commands at launch time and always activate a MITO profile."""
import base64
import json
import os
from pathlib import Path
import subprocess

from .i18n import tr
from .storage import YitoError, identifier, inside, mutation, read_json, write_json
from .pocketools import sources, info

LAUNCHER_HEADER = '@rem MITO generated PowerShell launcher\n'


def launcher_content(filename):
    return (LAUNCHER_HEADER + '@echo off\n'
        '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0' + filename + '" %*\n'
        'exit /b %errorlevel%\n')


def generated_launcher(path):
    if path.suffix.lower() != '.cmd' or path.is_symlink() or path.is_junction():
        return False
    expected = launcher_content(path.with_suffix('.ps1').name).replace('\n', '\r\n').encode('utf-8')
    with path.open('rb') as stream:
        return stream.read(len(expected) + 1) == expected


def prepare_commands(payload):
    # Remove only unchanged generated wrappers left behind by a rename/type change.
    for path in payload.glob('*.cmd'):
        if path.is_file() and not path.with_suffix('.ps1').exists() and generated_launcher(path):
            path.unlink()
    for path in list(payload.glob('*.ps1')):
        from .pocketool_authoring import command_filename
        try:
            command_filename(path.stem, 'ps1')
        except YitoError:
            continue
        if any(path.with_suffix(ext).exists() for ext in ('.cmd', '.bat', '.exe', '.com')):
            continue
        content = launcher_content(path.name)
        path.with_suffix('.cmd').write_text(content, encoding='utf-8', newline='\r\n')


def target(engine, package, filename):
    from .pocketool_authoring import script_path
    package = identifier(package)
    if package not in sources(engine.root):
        raise YitoError(tr('Pocketool no configurada: {p1}', p1=package))
    folder = inside(engine.root / 'pocketools', package)
    if folder.is_symlink() or folder.is_junction():
        raise YitoError(tr('La pocketool contiene enlaces no admitidos'))
    info(folder)
    path = script_path(folder, filename)
    if not path.is_file():
        raise YitoError(tr('No existe {p1}', p1=filename))
    return path


def encoded_arguments(arguments):
    if not isinstance(arguments, (list, tuple)) or any(not isinstance(arg, str) or '\0' in arg for arg in arguments):
        raise YitoError(tr('Argumentos no válidos.'))
    return base64.b64encode(json.dumps(list(arguments), ensure_ascii=True).encode('ascii')).decode('ascii')


def decoded_arguments(value):
    try:
        arguments = json.loads(base64.b64decode(value, validate=True))
        encoded_arguments(arguments)
        return arguments
    except (ValueError, TypeError) as exc:
        raise YitoError(tr('Argumentos no válidos.')) from exc


def terminal_command(engine, profile, package, filename, arguments=()):
    from .terminal import command
    from .pocketool_authoring import command_filename
    profile, package = identifier(profile), identifier(package)
    command_filename(Path(filename).stem, Path(filename).suffix.lower()[1:])
    engine.profile(profile)
    path = target(engine, package, filename)
    if path.suffix.lower() == '.cmd':
        from .jobs import batch_command
        batch_command(path, arguments, {})  # Reject unsupported quotes/newlines before writing a link.
    # WT interprets semicolons even in native argv. Pass user data in one safe token.
    argv = command(engine, profile)
    argv[argv.index('--title') + 1] = f'{package}/{filename} ({profile})'
    index = argv.index('-m')
    argv[index + 2:] = ['pocketool-run', '--owned', '--profile', profile,
        '--package', package, '--entry', filename, '--arguments64', encoded_arguments(arguments)]
    return argv


def launch(engine, profile, package, filename, arguments=()):
    from .terminal import prepare
    prepare(engine)
    subprocess.Popen(terminal_command(engine, profile, package, filename, arguments), cwd=engine.root)
    return 0


def shortcut_command(engine, profile, package, filename, arguments=()):
    # Reuse launch validation, but never associate the Pocketools .lnk icon with
    # the Terminal executable shared by the entire MITO window.
    terminal = terminal_command(engine, profile, package, filename, arguments)
    child = terminal[terminal.index('-m') + 2:]
    child.remove('--owned')
    return [str(engine.root / 'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', *child]


def migrate_shortcuts(engine):
    """Retarget registered legacy links once, keeping their icon and other properties."""
    import logging
    from .shortcuts import retarget_link
    folder = engine.root / 'core/state/pocketool-shortcuts'
    for registry in folder.glob('*.json'):
        try:
            data = read_json(registry)
            if not isinstance(data, dict):
                raise ValueError('Invalid Pocketool shortcut record')
            if data.get('launcher') == 'pythonw':
                continue
            path = Path(data['path'])
            if not path.is_file() or path.suffix.lower() != '.lnk' or path.is_symlink():
                continue
            values = (engine, data['profile'], data['package'], data['command'], data.get('arguments', []))
            old = terminal_command(*values)
            argv = shortcut_command(*values)
            targets = [engine.root / 'core/tools/windows-terminal' / name
                       for name in ('WindowsTerminal.exe', 'YitoTerminal.exe')]
            if retarget_link(path, targets, old[1:], argv):
                write_json(registry, dict(data, launcher='pythonw'))
        except (YitoError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            # A stale, edited or locked shortcut must not stop the manager opening.
            logging.getLogger(__name__).warning('Could not migrate Pocketool shortcut %s', registry, exc_info=True)


def execute(engine, profile, package, filename, arguments=()):
    from .processes import ProcessJob
    path = target(engine, package, filename)
    env, workspace = engine.execution_context(profile)
    if path.suffix.lower() == '.cmd':
        from .jobs import batch_command
        argv = batch_command(path, arguments, env)
    else:
        powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        argv = [str(powershell), '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(path), *arguments]
    job = ProcessJob()
    try:
        process = job.spawn(argv, cwd=workspace, env=env, creationflags=0)
        result = process.wait()
        job.wait_empty()
        return result
    finally:
        job.close()


def shortcut(engine, profile, package, filename, badge=None, color=None, arguments=()):
    import hashlib
    from .shortcuts import application_icon, badge_text, badge_color, desktop_path, write_link
    from .terminal import prepare
    profile, package = identifier(profile), identifier(package)
    with mutation(engine.root):
        prepare(engine)
        argv = shortcut_command(engine, profile, package, filename, arguments)
        badge, color = badge_text(engine, badge), badge_color(engine, color)
        tag = hashlib.sha256(f'{profile}/{package}/{filename}'.encode()).hexdigest()[:16]
        icon = application_icon(engine, 'pocketool-' + tag, badge,
            engine.root / 'core/assets/branding/pocketools.ico', color=color)
        registry = engine.root / 'core/state/pocketool-shortcuts' / (tag + '.json')
        old = read_json(registry) if registry.exists() else {}
        destination = desktop_path()
        destination.mkdir(parents=True, exist_ok=True)
        stem = f'{profile} - {package}-{Path(filename).stem}'
        path = destination / (stem + '.lnk')
        number = 2
        while path.exists() and str(path) != old.get('path'):
            path = destination / f'{stem} ({number}).lnk'
            number += 1
        write_link(path, argv, engine.root, icon)
        write_json(registry, {'path': str(path), 'profile': profile, 'package': package,
            'command': filename, 'badge': badge, 'color': color, 'arguments': list(arguments), 'launcher': 'pythonw'})
        return path
