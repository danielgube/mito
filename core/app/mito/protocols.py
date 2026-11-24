"""Explicit claim of URL schemes (claude://, vscode://…) for MITO components.

A browser hands custom-scheme links (for example the return of a sign-in) to
Windows, which runs the command of HKCU/HKLM\\Software\\Classes\\<scheme>. The
host copy of an application usually owns that key, so the link reaches the host
instead of the MITO instance. Claiming writes HKCU\\Software\\Classes\\<scheme>
with the same launcher as «Open with» (`mito run --profile --app -- "%1"`), so
the profile environment and data apply and the running instance receives it.

A claim lives only while its dialog is open: the dialog re-asserts it every few
seconds (applications such as Electron ones register themselves again when they
start or sign in) and releases it when closed. The previous key is saved in
core/state/protocols.json during the claim and restored on release only while
the key still holds MITO's command; a key another application took back is left
as it is. A leftover claim (MITO closed with the dialog open) is released the
next time the dialog opens. Candidates are generic: URL schemes whose command
runs an executable with a declared executable name.
"""
import os
import re
import shlex

from .i18n import tr
from .storage import YitoError, identifier, mutation, read_json, write_json

REGISTRY = 'core/state/protocols.json'
CLASSES = r'Software\Classes'
# HKLM is only read, to show and detect schemes registered for all users.
MACHINE_CLASSES = r'Software\Classes'
SCHEME = re.compile(r'^[a-z][a-z0-9+.-]{1,31}$')
# Schemes Windows or browsers handle themselves; claiming them would break the host.
RESERVED = frozenset(('http', 'https', 'file', 'ftp', 'mailto', 'javascript', 'data', 'about', 'blob',
                      'res', 'shell', 'ms-settings', 'search-ms', 'microsoft-edge', 'mito'))
STATUSES = ('mine', 'profile', 'direct', 'other', 'absent')


def winreg():
    import winreg as module
    return module


def registry(root):
    file = root/REGISTRY
    data = read_json(file) if file.exists() else {}
    return data if isinstance(data, dict) else {}


def save(root, records):
    write_json(root/REGISTRY, records)


def scheme_name(text):
    value = str(text or '').strip().lower().removesuffix('://').removesuffix(':')
    if not SCHEME.match(value):
        raise YitoError(tr('Esquema no válido: {p1}', p1=(text)))
    if value in RESERVED or value.startswith('ms-'):
        raise YitoError(tr('Esquema reservado del sistema: {p1}', p1=(value)))
    return value


def command(root, profile, app, shortcut=None):
    from types import SimpleNamespace
    from .open_with import command as launcher
    return launcher(SimpleNamespace(root=root), profile, app, shortcut)


# --- Registry snapshot -------------------------------------------------------

def snapshot(hive, path):
    """Whole key tree as JSON data, or None when the key does not exist."""
    reg = winreg()
    try:
        key = reg.OpenKey(hive, path)
    except FileNotFoundError:
        return None
    with key:
        values = []
        index = 0
        while True:
            try:
                name, data, kind = reg.EnumValue(key, index)
            except OSError:
                break
            if isinstance(data, bytes):
                data = {'hex': data.hex()}
            values.append([name, kind, data])
            index += 1
        children = {}
        index = 0
        while True:
            try:
                children[reg.EnumKey(key, index)] = None
            except OSError:
                break
            index += 1
    for child in children:
        children[child] = snapshot(hive, path + '\\' + child)
    return {'values': values, 'keys': {name: tree for name, tree in children.items() if tree is not None}}


def write_tree(path, tree):
    reg = winreg()
    with reg.CreateKey(reg.HKEY_CURRENT_USER, path) as key:
        for name, kind, data in tree.get('values', []):
            if isinstance(data, dict) and 'hex' in data:
                data = bytes.fromhex(data['hex'])
            reg.SetValueEx(key, name, 0, kind, data)
    for child, subtree in tree.get('keys', {}).items():
        write_tree(path + '\\' + child, subtree)


def delete_key(scheme):
    from .open_with import delete_tree
    delete_tree(CLASSES + '\\' + scheme_name(scheme))


# --- Current state -----------------------------------------------------------

def read_command(hive, base, scheme):
    reg = winreg()
    try:
        with reg.OpenKey(hive, base + '\\' + scheme) as key:
            reg.QueryValueEx(key, 'URL Protocol')
        with reg.OpenKey(hive, base + '\\' + scheme + r'\shell\open\command') as key:
            value = reg.QueryValueEx(key, '')[0]
            return value if isinstance(value, str) else ''
    except FileNotFoundError:
        return None
    except OSError:
        return None


def current(scheme):
    """(command, 'user'|'machine') as Windows resolves it, or (None, None)."""
    reg = winreg()
    value = read_command(reg.HKEY_CURRENT_USER, CLASSES, scheme)
    if value is not None:
        return value, 'user'
    if MACHINE_CLASSES:
        value = read_command(reg.HKEY_LOCAL_MACHINE, MACHINE_CLASSES, scheme)
        if value is not None:
            return value, 'machine'
    return None, None


def executable(command_line):
    """First token of a shell command, without quotes; '' when unreadable."""
    try:
        parts = shlex.split(str(command_line or ''), posix=False)
    except ValueError:
        return ''
    return parts[0].strip('"') if parts else ''


def inside_root(root, path):
    try:
        base = os.path.normcase(os.path.normpath(str(root)))
        value = os.path.normcase(os.path.normpath(path))
        return os.path.commonpath([base, value]) == base
    except ValueError:
        return False


def status(root, scheme, profile, installation, records=None):
    """(status, command): see STATUSES."""
    records = registry(root) if records is None else records
    value, _ = current(scheme)
    if value is None:
        return 'absent', None
    record = records.get(scheme)
    if record and record.get('profile') == profile and record.get('installation') == installation \
            and value == command(root, record['profile'], record['app'], record.get('shortcut')):
        return 'mine', value
    program = executable(value)
    if inside_root(root, program):
        mito = os.path.normcase(str(root/'core/tools/python/pythonw.exe'))
        if os.path.normcase(os.path.normpath(program)) == mito and ' -m mito run ' in value:
            return 'profile', value
        return 'direct', value
    return 'other', value


def label(state):
    return {'mine': tr('De este profile'), 'profile': tr('Otro profile de MITO'),
            'direct': tr('Ejecutable de MITO sin entorno'), 'other': tr('Otra app'),
            'absent': tr('No registrado')}[state]


def schemes(hive, base):
    reg = winreg()
    try:
        key = reg.OpenKey(hive, base)
    except OSError:
        return
    with key:
        index = 0
        while True:
            try:
                name = reg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            if name.startswith('.') or not SCHEME.match(name.lower()):
                continue
            yield name.lower()


def candidates(engine, lock):
    """URL schemes whose command runs an executable named like a declared one."""
    from pathlib import Path
    names = {Path(entry).name.casefold() for _, entry, _ in engine.application_entries(lock)}
    found = []
    reg = winreg()
    bases = [(reg.HKEY_CURRENT_USER, CLASSES)]
    if MACHINE_CLASSES:
        bases.append((reg.HKEY_LOCAL_MACHINE, MACHINE_CLASSES))
    for hive, base in bases:
        for name in schemes(hive, base):
            if name in found or name in RESERVED or name.startswith('ms-'):
                continue
            value = read_command(hive, base, name)
            if value and Path(executable(value)).name.casefold() in names:
                found.append(name)
    return sorted(found)


def describe(engine, profile, installation, scheme):
    """Dialog row {scheme, status, command} for one scheme."""
    scheme = scheme_name(scheme)
    state, value = status(engine.root, scheme, profile, installation)
    return {'scheme': scheme, 'status': state, 'command': value}


def rows(engine, profile, installation):
    """Dialog rows for the candidates of a component."""
    lock = engine.installed()[installation]
    return [describe(engine, profile, installation, scheme) for scheme in candidates(engine, lock)]


class Session:
    """Claims made from one open dialog; release() undoes them all."""

    def __init__(self, engine, profile, installation, log=print):
        self.engine, self.profile, self.installation, self.log = engine, profile, installation, log
        self.schemes = {}

    def describe(self, scheme):
        return describe(self.engine, self.profile, self.installation, scheme)

    def claim(self, scheme, shortcut=None):
        record = claim(self.engine, self.profile, self.installation, scheme, shortcut, self.log)
        self.schemes[record['scheme']] = shortcut
        return self.describe(record['scheme'])

    def check(self, scheme):
        """Re-assert a claimed scheme; True when it had been overwritten."""
        if scheme not in self.schemes:
            return False
        if status(self.engine.root, scheme, self.profile, self.installation)[0] == 'mine':
            return False
        claim(self.engine, self.profile, self.installation, scheme, self.schemes[scheme],
              lambda message: None, keep_backup=True)
        return True

    def release(self, scheme=None):
        selected = set(self.schemes) if scheme is None else {scheme}
        unregister(self.engine.root, lambda record: record.get('scheme') in selected
                   and record.get('profile') == self.profile
                   and record.get('installation') == self.installation, self.log)
        for name in selected:
            self.schemes.pop(name, None)


# --- Claim and release -------------------------------------------------------

def claim(engine, profile, installation, scheme, shortcut=None, log=print, keep_backup=False):
    """Write the scheme key with MITO's launcher. keep_backup re-asserts a claim
    another application overwrote without replacing the copy taken first."""
    from .open_with import icon_for, notify
    profile = identifier(profile)
    scheme = scheme_name(scheme)
    with mutation(engine.root):
        data = engine.profile(profile)
        if installation not in data['apps']:
            raise YitoError(tr('El componente debe estar activo en el profile'))
        lock = engine.selected(data)[installation]
        if shortcut is not None:
            engine.application_target(lock, shortcut)
        else:
            engine.application_target(lock)
        app = lock['key']
        records = registry(engine.root)
        previous = records.get(scheme)
        state, _ = status(engine.root, scheme, profile, installation, records)
        reg = winreg()
        path = CLASSES + '\\' + scheme
        if previous and (keep_backup or state in ('mine', 'profile')):
            # Still MITO's claim: keep the copy of what was there before MITO.
            backup = previous.get('backup')
        else:
            # Absent, foreign or a raw MITO executable: this is what release restores.
            backup = snapshot(reg.HKEY_CURRENT_USER, path)
        record = {'scheme': scheme, 'profile': profile, 'installation': installation, 'app': app,
                  'shortcut': shortcut, 'backup': backup}
        icon = icon_for(engine, lock, profile, shortcut)
        line = command(engine.root, profile, app, shortcut)
        try:
            delete_key(scheme)
            write_tree(path, {'values': [['', reg.REG_SZ, f'URL:{scheme}'], ['URL Protocol', reg.REG_SZ, '']],
                              'keys': {'DefaultIcon': {'values': [['', reg.REG_SZ, f'{icon},0']]},
                                       'shell': {'keys': {'open': {'keys': {'command': {
                                           'values': [['', reg.REG_SZ, line]]}}}}}}})
            records[scheme] = record
            save(engine.root, records)
        except Exception:
            delete_key(scheme)
            if backup is not None:
                write_tree(path, backup)
            raise
        finally:
            notify()
        log(tr('Protocolo {p1} reclamado para {p3}.', p1=(scheme), p3=(f'{profile} - {app}')))
        return record


def release_record(root, record, log=print):
    """Restore the saved key only while it still runs MITO's command."""
    scheme = scheme_name(record.get('scheme'))
    value = read_command(winreg().HKEY_CURRENT_USER, CLASSES, scheme)
    if value == command(root, record['profile'], record['app'], record.get('shortcut')):
        delete_key(scheme)
        if record.get('backup') is not None:
            write_tree(CLASSES + '\\' + scheme, record['backup'])
        log(tr('Protocolo {p1} liberado; registro anterior restaurado.', p1=(scheme)))
    else:
        log(tr('Protocolo {p1} liberado; ya apuntaba a otra aplicación y no se ha modificado.', p1=(scheme)))


def unregister(root, select, log=print):
    """Release the records matching select(record); keep the others claimed."""
    from .open_with import notify
    records = registry(root)
    removed = []
    for scheme, record in list(records.items()):
        if not select(record):
            continue
        try:
            release_record(root, record, log)
            records.pop(scheme)
            removed.append(record)
        except (OSError, KeyError, ValueError, YitoError) as exc:
            log(tr('No se pudo liberar el protocolo {p1}: {p3}', p1=(scheme), p3=(exc)))
    if removed:
        save(root, records)
        notify()
    return removed
