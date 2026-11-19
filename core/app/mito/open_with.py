"""Explicit «Open with» entries in the Windows shell for MITO components.

Each entry is a per-user ProgID (HKCU\\Software\\Classes\\MITO.<tag>) whose open
command is the same launcher as desktop shortcuts (`mito run --profile --app`),
so the profile environment, workspace and per-profile copies apply. Extensions
are chosen by the user; Scoop manifests do not declare file types. The default
application is never changed: Windows protects that choice and it stays the
user's. Records live in core/state/open-with.json to remove or rewrite entries.
"""
import hashlib
import re
import subprocess

from .i18n import tr
from .storage import YitoError, identifier, mutation, read_json, write_json

REGISTRY = 'core/state/open-with.json'
CLASSES = r'Software\Classes'
EXTENSION = re.compile(r'^\.[a-z0-9][a-z0-9_+-]{0,15}$')
# Suggestions for the dialog only; any valid extension can still be typed.
COMMON = (
    '.pdf', '.epub', '.djvu', '.cbz', '.txt', '.md', '.rtf', '.csv', '.json', '.xml', '.html', '.log',
    '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.odt', '.ods', '.odp',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.tif', '.ico',
    '.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus',
    '.mp4', '.mkv', '.avi', '.mov', '.webm', '.wmv',
    '.zip', '.7z', '.rar', '.tar', '.gz',
    '.py', '.js', '.ts', '.java', '.c', '.cpp', '.cs', '.go', '.rs', '.ps1', '.bat', '.ini', '.yaml', '.toml',
)
# Groups of extensions usually handled by well-known applications. Only dialog
# suggestions: they add extensions to the table and never select a component.
PRESETS = (
    ('Word', ('.docx', '.doc', '.docm', '.dotx', '.dot', '.rtf', '.odt')),
    ('Excel', ('.xlsx', '.xls', '.xlsm', '.xlsb', '.xltx', '.csv', '.ods')),
    ('PowerPoint', ('.pptx', '.ppt', '.pptm', '.ppsx', '.pps', '.potx', '.odp')),
    ('PDF / eBook', ('.pdf', '.epub', '.mobi', '.azw3', '.djvu', '.xps', '.cbz', '.cbr')),
    ('7-Zip', ('.7z', '.zip', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.zst', '.iso', '.cab', '.wim')),
    ('VLC', ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.flv', '.mpg', '.mpeg', '.m4v', '.3gp',
             '.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus', '.wma')),
    ('IrfanView', ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tif', '.tiff', '.ico', '.heic')),
    ('Notepad++', ('.txt', '.log', '.md', '.ini', '.cfg', '.conf', '.json', '.xml', '.yaml', '.yml', '.toml',
                   '.ps1', '.bat', '.cmd', '.py', '.js', '.ts', '.html', '.css')),
)


def winreg():
    import winreg as module
    return module


def registry(root):
    file = root/REGISTRY
    return read_json(file) if file.exists() else {}


def save(root, records):
    write_json(root/REGISTRY, records)


def extensions(text):
    """Parse «pdf, .epub djvu» into ['.pdf', '.epub', '.djvu']."""
    result = []
    for part in re.split(r'[\s,;]+', str(text or '').strip().lower()):
        if not part:
            continue
        value = part if part.startswith('.') else '.' + part
        if not EXTENSION.match(value):
            raise YitoError(tr('Extensión no válida: {p1}', p1=(part)))
        if value not in result:
            result.append(value)
    if not result:
        raise YitoError(tr('Indica al menos una extensión'))
    return result


def progid(profile, app, shortcut=None):
    seed = f'{profile}/{app}' if shortcut is None else f'{profile}/{app}/{shortcut}'
    return 'MITO.' + hashlib.sha256(seed.encode()).hexdigest()[:16]


def display_name(profile, app, shortcut=None):
    name = f'{profile} - {app.replace("/", "-")}'
    return name if shortcut is None else f'{name} - {shortcut}'


def command(engine, profile, app, shortcut=None):
    argv = [str(engine.root/'core/tools/python/pythonw.exe'), '-B', '-I', '-X', 'utf8', '-m', 'mito', 'run',
            '--profile', profile, '--app', app]
    if shortcut is not None:
        argv += ['--shortcut', shortcut]
    return subprocess.list2cmdline(argv) + ' -- "%1"'


def notify():
    # SHCNE_ASSOCCHANGED: Explorer reloads associations without restarting.
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def set_value(key, name, value):
    reg = winreg()
    reg.SetValueEx(key, name, 0, reg.REG_SZ, value)


def write_progid(record, command_line, icon):
    reg = winreg()
    name = display_name(record['profile'], record['app'], record.get('shortcut'))
    base = CLASSES + '\\' + record['progid']
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base) as key:
        set_value(key, '', name)
        set_value(key, 'FriendlyTypeName', name)
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base + r'\DefaultIcon') as key:
        set_value(key, '', f'{icon},0')
    # Shown by the Open with menu instead of the launcher's own name and icon.
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base + r'\Application') as key:
        set_value(key, 'ApplicationName', name)
        set_value(key, 'ApplicationIcon', f'{icon},0')
        set_value(key, 'ApplicationDescription', 'MITO')
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base + r'\shell\open') as key:
        set_value(key, 'FriendlyAppName', name)
    with reg.CreateKey(reg.HKEY_CURRENT_USER, base + r'\shell\open\command') as key:
        set_value(key, '', command_line)
    for extension in record['extensions']:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, CLASSES + '\\' + extension + r'\OpenWithProgids') as key:
            reg.SetValueEx(key, record['progid'], 0, reg.REG_NONE, b'')


def delete_tree(path):
    reg = winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ | reg.KEY_WRITE) as key:
            while True:
                try:
                    child = reg.EnumKey(key, 0)
                except OSError:
                    break
                delete_tree(path + '\\' + child)
        reg.DeleteKey(reg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def remove_progid(record):
    """Remove only MITO's own ProgID and its OpenWithProgids values."""
    reg = winreg()
    if not re.fullmatch(r'MITO\.[0-9a-f]{16}', str(record.get('progid', ''))):
        raise YitoError(tr('Registro de «Abrir con» no válido'))
    for extension in record.get('extensions', []):
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, CLASSES + '\\' + extension + r'\OpenWithProgids',
                             0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, record['progid'])
        except FileNotFoundError:
            pass
    delete_tree(CLASSES + '\\' + record['progid'])


def current_command(record):
    reg = winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, CLASSES + '\\' + record['progid'] + r'\shell\open\command') as key:
            return reg.QueryValueEx(key, '')[0]
    except FileNotFoundError:
        return None


def icon_for(engine, lock, profile, shortcut, badge=None, color=None):
    from .shortcuts import application_icon, badge_text, badge_color, component_icon_source
    badge = badge_text(engine, badge)
    color = badge_color(engine, color)
    tag = progid(profile, lock['key'], shortcut).removeprefix('MITO.')
    name = f'open-{tag}-{hashlib.sha256((badge + color).encode()).hexdigest()[:8]}'
    return application_icon(engine, name, badge, component_icon_source(engine, lock, profile, shortcut), color=color)


def register(engine, profile, installation, text, shortcut=None):
    profile = identifier(profile)
    with mutation(engine.root):
        data = engine.profile(profile)
        if installation not in data['apps']:
            raise YitoError(tr('El componente debe estar activo en el profile'))
        lock = engine.selected(data)[installation]
        if shortcut is not None:
            engine.application_target(lock, shortcut)
        else:
            engine.application_target(lock)
        values = extensions(text)
        app = lock['key']
        record = {'progid': progid(profile, app, shortcut), 'profile': profile, 'installation': installation,
                  'app': app, 'shortcut': shortcut, 'extensions': values}
        records = registry(engine.root)
        previous = records.get(record['progid'])
        icon = icon_for(engine, lock, profile, shortcut)
        try:
            if previous:
                remove_progid(previous)
            write_progid(record, command(engine, profile, app, shortcut), icon)
            records[record['progid']] = record
            save(engine.root, records)
        except Exception:
            remove_progid(record)
            if records.pop(record['progid'], None) is not None:
                save(engine.root, records)
            raise
        finally:
            notify()
        return record


def entries(root, profile, installation):
    return [record for record in registry(root).values()
            if record.get('profile') == profile and record.get('installation') == installation]


def unregister(root, select, log=print):
    """Remove the records matching select(record); keep the others registered."""
    records = registry(root)
    removed = []
    for key, record in list(records.items()):
        if not select(record):
            continue
        try:
            remove_progid(record)
            records.pop(key)
            removed.append(record)
        except (OSError, YitoError) as exc:
            log(tr('No se pudo retirar «Abrir con» {p1}: {p3}', p1=(key), p3=(exc)))
    if removed:
        save(root, records)
        notify()
    return removed


def apply(engine, profile, installation, wanted, log=print):
    """Apply the dialog result {entry: [extensions]}; an empty list removes it."""
    profile = identifier(profile)
    existing = {record.get('shortcut'): record['extensions'] for record in entries(engine.root, profile, installation)}
    for shortcut, values in wanted.items():
        if list(values) == existing.get(shortcut, []):
            continue
        if values:
            record = register(engine, profile, installation, ' '.join(values), shortcut=shortcut)
            log(tr('Registrado en «Abrir con» para {p1}. Puede tardar unos segundos en aparecer en el Explorador.',
                   p1=(' '.join(record['extensions']))))
        else:
            unregister(engine.root, lambda record: record.get('profile') == profile
                       and record.get('installation') == installation and record.get('shortcut') == shortcut, log)
            log(tr('Retirado de «Abrir con».'))


def relocate(engine, old, copied, log=print):
    """After a move rewrite commands and icons; after a copy leave them to the original."""
    import os
    try:
        records = registry(engine.root)
    except (OSError, ValueError):
        return 0
    old_root = os.path.normcase(os.path.normpath(str(old)))
    changed = 0
    for key, record in list(records.items()):
        current = current_command(record)
        if current is None or old_root not in os.path.normcase(current):
            continue
        changed += 1
        if copied:
            records.pop(key)
            continue
        try:
            data = engine.profile(record['profile'])
            lock = engine.selected(data)[record['installation']]
            icon = icon_for(engine, lock, record['profile'], record.get('shortcut'))
            write_progid(record, command(engine, record['profile'], record['app'], record.get('shortcut')), icon)
        except (OSError, KeyError, ValueError, YitoError) as exc:
            log(tr('No se pudo retirar «Abrir con» {p1}: {p3}', p1=(key), p3=(exc)))
    if changed:
        save(engine.root, records)
        notify()
    return changed
