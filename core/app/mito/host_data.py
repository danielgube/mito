"""Explicit, instance-wide junctions from portable AppData to host folders."""
from .i18n import tr
import ctypes
import json
import os
from pathlib import Path
import subprocess
import uuid

from .storage import YitoError, inside, mutation, read_json, write_json

KINDS = {'data': 'Roaming', 'cache': 'Local'}


def host_folder(kind):
    # Known folders work even when MITO inherited its own APPDATA variables.
    folder = ctypes.create_unicode_buffer(32768)
    code = 26 if kind == 'data' else 28
    if ctypes.windll.shell32.SHGetFolderPathW(None, code, None, 0, folder):
        raise YitoError(tr('No se pudo localizar AppData del host'))
    return Path(folder.value)


def candidates(root, identity, kind):
    from .host_apps import target
    executable = target(root, identity).resolve()
    word = executable.stem.casefold()
    found = []
    for directory, names, _ in os.walk(host_folder(kind), followlinks=False):
        descend = []
        for name in sorted(names, key=str.casefold):
            path = Path(directory)/name
            try:
                if path.is_junction() or path.is_symlink():
                    continue
                if word in name.casefold():
                    if not executable.is_relative_to(path.resolve()):
                        found.append(path)
                    continue
                descend.append(name)
            except OSError:
                continue
        names[:] = descend
    return found


def registry(root):
    file = root/'core/state/host-data-links.json'
    return read_json(file) if file.exists() else {}


def save(root, records):
    write_json(root/'core/state/host-data-links.json', records)


def destination(root, kind, relative):
    base = root/'home/AppData'/KINDS[kind]
    # Validate syntax without resolving the final junction into the host.
    inside(Path('C:/__mito_validation__'), relative)
    path = base/relative
    if not relative or path == base or not path.parent.resolve().is_relative_to(base.resolve()):
        raise YitoError(tr('Destino fuera del AppData de MITO'))
    if not base.resolve().is_relative_to(root.resolve()):
        raise YitoError(tr('AppData de MITO está redirigido fuera de la instancia'))
    return path


def plan(root, kind, folder):
    folder = Path(folder)
    if not folder.is_absolute() or not folder.is_dir():
        raise YitoError(tr('Selecciona una carpeta existente'))
    folder = folder.resolve()
    base = host_folder(kind).resolve()
    if folder == base:
        raise YitoError(tr('Selecciona una carpeta de aplicación, no todo AppData'))
    relative = str(folder.relative_to(base)) if folder.is_relative_to(base) else folder.name
    path = destination(root, kind, relative)
    if folder.is_relative_to(root.resolve()) or root.resolve().is_relative_to(folder):
        raise YitoError(tr('Selecciona datos externos a MITO, sin enlaces circulares'))
    return path, folder, relative


def entries(root, identity, kind):
    from .host_apps import target
    records = registry(root)
    result = list(records.get(identity, {}).get(kind, []))
    try:
        word = target(root, identity).stem.casefold()
    except YitoError:
        return result
    base = root/'home/AppData'/KINDS[kind]
    host = host_folder(kind).resolve()
    claimed = {item['relative'].casefold() for app in records.values()
               for item in app.get(kind, [])}
    # Inspect the portable tree only; never follow junctions into host data.
    for directory, names, _ in os.walk(base, followlinks=False):
        descend = []
        for name in names:
            path = Path(directory)/name
            relative = str(path.relative_to(base))
            if path.is_junction():
                if (relative.casefold() not in claimed
                        and any(word in part.casefold() for part in Path(relative).parts)):
                    expected = host/relative
                    try:
                        checked = destination(root, kind, relative)
                        if (expected.is_dir() and checked.resolve() == expected.resolve()
                                and not expected.resolve().is_relative_to(root.resolve())):
                            result.append({'relative': relative, 'target': str(expected.resolve())})
                    except (OSError, YitoError):
                        pass
            elif not path.is_symlink():
                descend.append(name)
        names[:] = descend
    return result


def linked(root, identity, kind):
    for item in entries(root, identity, kind):
        path = destination(root, kind, item['relative'])
        if path.is_junction() and path.resolve() == Path(item['target']).resolve():
            return True
    return False


def link(root, identity, kind, folder, replace=False):
    from .host_apps import target
    with mutation(root):
        target(root, identity)
        path, folder, relative = plan(root, kind, folder)
        records = registry(root)
        for owner, app in records.items():
            for category, items in app.items():
                for item in items:
                    other = destination(root, category, item['relative'])
                    if path == other or path.is_relative_to(other) or other.is_relative_to(path):
                        if (owner == identity and category == kind and path == other
                                and path.is_junction() and path.resolve() == folder
                                and Path(item['target']).resolve() == folder):
                            return
                        raise YitoError(tr('Esta ruta ya está gestionada por una aplicación del host'))
        if path.is_junction() and path.resolve() == folder:
            records.setdefault(identity, {}).setdefault(kind, []).append({'relative': relative, 'target': str(folder)})
            save(root, records)
            return
        if path.is_junction() or path.is_symlink():
            raise YitoError(tr('No se reemplazan enlaces ajenos a MITO'))
        if path.exists() and not replace:
            raise YitoError(tr('La carpeta {p1} ya existe y ha de ser borrada para generar el enlace', p1=(path)))
        if path.exists() and not path.is_dir():
            raise YitoError(tr('El destino existente no es una carpeta'))
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if path.exists():
            if not path.resolve().is_relative_to((root/'home/AppData').resolve()):
                raise YitoError(tr('Destino fuera del AppData de MITO'))
            backup = root/'core/staging'/f'host-data-{uuid.uuid4().hex}'
            backup.parent.mkdir(parents=True, exist_ok=True)
            path.rename(backup)
        try:
            literal = lambda value: "'" + str(value).replace("'", "''") + "'"
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                            f'New-Item -ItemType Junction -Path {literal(path)} -Value {literal(folder)} -ErrorAction Stop | Out-Null'],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            records.setdefault(identity, {}).setdefault(kind, []).append({'relative': relative, 'target': str(folder)})
            save(root, records)
        except Exception:
            if path.is_junction():
                os.rmdir(path)
            if backup is not None:
                backup.rename(path)
            raise
        if backup is not None:
            import shutil
            if not backup.resolve().is_relative_to((root/'core/staging').resolve()):
                raise YitoError(tr('Copia temporal fuera de staging'))
            shutil.rmtree(backup)


def unlink_locked(root, identity, kind):
    records = registry(root)
    for item in entries(root, identity, kind):
        path = destination(root, kind, item['relative'])
        if path.is_junction():
            if path.resolve() != Path(item['target']).resolve():
                raise YitoError(tr('El enlace ha cambiado fuera de MITO: {p1}', p1=(path)))
            os.rmdir(path)  # Never traverse or delete the host target.
        elif path.exists() or path.is_symlink():
            raise YitoError(tr('Se esperaba una junction gestionada: {p1}', p1=(path)))
    if identity in records:
        records[identity].pop(kind, None)
        if not records[identity]:
            records.pop(identity)
    save(root, records)


def unlink(root, identity, kind):
    with mutation(root):
        unlink_locked(root, identity, kind)


def choose_folder(kind):
    script = '''
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'MITO - Selecciona la carpeta del host para enlazar'
$dialog.ShowNewFolderButton = $false
$dialog.SelectedPath = [Console]::In.ReadToEnd()
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -InputObject $dialog.SelectedPath -Compress
    }
} finally { $dialog.Dispose() }
'''
    from .native_ui import localize_script
    script = localize_script(script, 'MITO - Selecciona la carpeta del host para enlazar')
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', script],
                            input=str(host_folder(kind)), capture_output=True, text=True, encoding='utf-8', check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.stdout.strip() else None
