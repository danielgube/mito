"""One private Terminal window, with MITO and fresh profile activations."""
from .i18n import tr
import hashlib
import subprocess
import uuid
from .storage import YitoError, read_json, write_json

NAMESPACE = uuid.UUID('87bca5b6-a16a-40f6-8eb6-d20a946776c2')
COLORS = {'manager': '#368448', 'powershell': '#317ca4', 'cmd': '#9a7735'}
SCHEMES = {'manager': ('MITO Forest', '#102820'), 'powershell': ('MITO PowerShell', '#102332'), 'cmd': ('MITO CMD', '#282319')}


def brand_window_icons(root):
    """Replace the unpackaged Terminal window icons without patching its EXE."""
    folder = root / 'core/tools/windows-terminal'
    source = root / 'core/assets/branding/mito.ico'
    if not (folder / 'WindowsTerminal.exe').is_file() or not source.is_file():
        return
    icon_bytes = source.read_bytes()
    images = folder / 'Images'
    images.mkdir(exist_ok=True)
    for name in ('terminal_contrast-black.ico', 'terminal_contrast-white.ico'):
        target = images / name
        if not target.exists() or target.read_bytes() != icon_bytes:
            target.write_bytes(icon_bytes)
    from .terminal_branding import ensure_branded_executable
    return ensure_branded_executable(root)


def guid(profile=None, shell_type='cmd'):
    return '{' + str(uuid.uuid5(NAMESPACE, f'{profile}/{shell_type}' if profile else 'manager')) + '}'


def child_command(engine, profile=None, shell_type='cmd'):
    child = [str(engine.root / 'core/tools/python/python.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito']
    if profile:
        if shell_type != 'cmd':
            child += ['shell', '--type', shell_type, '--profile', profile]
        else:
            child += ['shell', '--profile', profile]
    else:
        child += ['ui']
    return child


def command(engine, profile=None, shell_type='cmd', selected_profile=None):
    original = engine.root / 'core/tools/windows-terminal/WindowsTerminal.exe'
    if not original.is_file():
        raise YitoError(tr('Falta Windows Terminal portable. Ejecuta mito.cmd bootstrap.'))
    branded = original.with_name('YitoTerminal.exe')
    executable = branded if branded.is_file() else original
    if shell_type not in ('cmd', 'powershell'):
        raise YitoError(tr('Shell no soportada'))
    window = 'mito-' + hashlib.sha256(str(engine.root).casefold().encode()).hexdigest()[:12]
    title = f'{"PowerShell" if shell_type == "powershell" else "CMD"} ({profile})' if profile else 'MITO'
    child = child_command(engine, profile, shell_type)
    if selected_profile is not None:
        engine.profile(selected_profile)
        child += ['--profile', selected_profile]
    return [str(executable), '--maximized', '-w', window, 'new-tab', '--profile', guid(profile, shell_type), '--title', title, '--startingDirectory', str(engine.root), *child]


def scheme(mode):
    name, background = SCHEMES[mode]
    return {'name': name, 'background': background, 'foreground': '#e7efdf',
        'cursorColor': '#b6e568', 'selectionBackground': '#456451',
        'black': '#14241e', 'red': '#e27878', 'green': '#8ccf58', 'yellow': '#e4c465',
        'blue': '#69acce', 'purple': '#be94ca', 'cyan': '#63c6b3', 'white': '#d5dfd5',
        'brightBlack': '#72877b', 'brightRed': '#ffa09a', 'brightGreen': '#b5ef75',
        'brightYellow': '#ffe38a', 'brightBlue': '#8fcfee', 'brightPurple': '#d7b4e3',
        'brightCyan': '#91edda', 'brightWhite': '#f5faef'}


def prepare(engine, selected_profile=None):
    folder = engine.root / 'core/tools/windows-terminal'
    if not (folder / 'WindowsTerminal.exe').is_file():
        return
    from .network import properties
    names = engine.profiles()
    selection_path = engine.root / 'core/state/terminal-profile.json'
    previous = read_json(selection_path) if selection_path.exists() else None
    active = selected_profile if selected_profile in names else previous
    if active not in names:
        active = names[0] if names else None
    if active != previous:
        write_json(selection_path, active)
    mode = properties(engine.root).get('terminal.defaultShell', 'cmd')
    if mode not in ('cmd', 'powershell', 'mito'):
        mode = 'cmd'
    default_guid = guid(active, mode) if active and mode != 'mito' else guid()
    (folder / '.portable').touch(exist_ok=True)
    base_icon = engine.root / 'core/assets/branding/mito.ico'
    brand_window_icons(engine.root)
    path = folder / 'settings/settings.json'
    settings = read_json(path) if path.exists() else {}
    backup = engine.root / 'core/state/terminal-settings-before-branding.json'
    if settings and not backup.exists():
        write_json(backup, settings)
    base_icon = str(base_icon)
    entries = []
    def entry(profile=None, mode='manager'):
        shell_type = mode if profile else 'cmd'
        name = f'{"PowerShell" if mode == "powershell" else "CMD"} ({profile})' if profile else 'MITO'
        return {'guid': guid(profile, shell_type), 'name': name, 'tabTitle': name,
            'commandline': subprocess.list2cmdline(child_command(engine, profile, shell_type)),
            'startingDirectory': str(engine.root), 'icon': base_icon,
            'colorScheme': SCHEMES[mode][0], 'tabColor': COLORS[mode], 'hidden': False}
    for profile in engine.profiles():
        entries += [entry(profile, 'powershell'), entry(profile, 'cmd')]
    entries.append(entry())
    # Keep the default first so + and the native Ctrl+Shift+1 agree.
    entries.sort(key=lambda item: item['guid'] != default_guid)
    menu = [{'type': 'profile', 'profile': e['guid']} for e in entries]
    # Terminal supplies these built-in entries even with dynamic sources disabled.
    entries += [{'guid': value, 'hidden': True} for value in ('{61c54bbd-c2c6-5271-96e7-009a87ff44bf}', '{0caa0dad-35be-5f56-a8ff-afceeeaa6101}', '{87bca5b6-a16a-40f6-8eb6-d20a946776c2}')]
    defaults = settings.get('profiles', {}).get('defaults', {})
    defaults.update({'font': {'face': 'Cascadia Mono', 'size': 11}, 'padding': '12',
        'opacity': 94, 'useAcrylic': True, 'suppressApplicationTitle': True, 'closeOnExit': 'graceful'})
    old_schemes = [s for s in settings.get('schemes', []) if s.get('name') not in {v[0] for v in SCHEMES.values()}]
    settings.update({'theme': 'dark', 'launchMode': 'maximized', 'showTabsInFullscreen': True,
        'alwaysShowTabs': True, 'copyOnSelect': False, 'defaultProfile': default_guid,
        'firstWindowPreference': 'defaultProfile', 'newTabMenu': menu,
        'disabledProfileSources': ['Windows.Terminal.Wsl', 'Windows.Terminal.Azure', 'Windows.Terminal.PowershellCore', 'Windows.Terminal.SSH', 'Windows.Terminal.VisualStudio'],
        'profiles': {'defaults': defaults, 'list': entries}, 'schemes': old_schemes + [scheme(m) for m in SCHEMES]})
    if not path.exists() or read_json(path) != settings:
        write_json(path, settings)
    from .pocketool_execution import migrate_shortcuts
    migrate_shortcuts(engine)


def launch(engine, profile=None, shell_type='cmd', selected_profile=None):
    prepare(engine, selected_profile=selected_profile)
    subprocess.Popen(command(engine, profile, shell_type, selected_profile), cwd=engine.root)
    return 0


def launch_application(engine, profile, title, child):
    """Open one owned application session in a dedicated tab of this instance."""
    prepare(engine)
    argv = command(engine, profile)
    title_index = argv.index('--title')
    argv[title_index + 1] = title
    module_index = argv.index('-m')
    argv[module_index + 2:] = child
    subprocess.Popen(argv, cwd=engine.root)
    return 0


def launch_component(engine, profile, app, title, arguments=(), shortcut=None):
    child = ['run', '--owned', '--profile', profile, '--app', app]
    if shortcut is not None:
        child += ['--shortcut', shortcut]
    if arguments:
        child += ['--', *arguments]
    return launch_application(engine, profile, title, child)


def launch_host(engine, profile, identity, title, arguments=()):
    child = ['host-run', identity, '--profile', profile, '--owned']
    if arguments:
        child += ['--', *arguments]
    return launch_application(engine, profile, title, child)


def launch_mcp(engine, pipe, owner, session):
    """Open the opt-in server directly in this instance's existing Terminal window."""
    prepare(engine)
    argv = command(engine)
    title = argv.index('--title')
    argv[title+1] = 'MITO MCP'
    index = argv.index('-m')
    argv[index+2:] = ['mcp-server', '--pipe', pipe, '--owner', str(owner), '--session', session]
    subprocess.Popen(argv, cwd=engine.root)
