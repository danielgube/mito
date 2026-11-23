"""Persist directories and files under the one home; rebuild links after moving."""
from .i18n import tr
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
from .catalog import array
from .storage import YitoError, inside, read_json, write_json


def definitions(manifest):
    for item in array(manifest.get('persist')):
        if isinstance(item, list):
            if len(item) != 2:
                raise YitoError(tr('persist debe ser una ruta o [origen, destino]'))
            yield item[0], item[1]
        else:
            yield item, item


def linked(source, target):
    """True when source already is the engine link to target (junction or hard link)."""
    try:
        if source.is_junction() and target.is_dir():
            current = os.readlink(source)
            if current.startswith('\\\\?\\'):
                current = current[4:]
            return os.path.normcase(os.path.normpath(current)) == os.path.normcase(os.path.normpath(os.path.abspath(target)))
        if source.is_file() and not source.is_symlink() and target.is_file():
            return os.path.samefile(source, target)
    except OSError:
        pass
    return False


def prepare(root, payload, key, manifest, destination=None):
    if not manifest.get('persist'):
        return
    managed_path = payload / '.mito-persist.json'
    managed = read_json(managed_path) if managed_path.exists() else {}
    for relative, alias in definitions(manifest):
        # Validate syntax independently of a stale junction's absolute target.
        inside(Path('C:/__mito_validation__'), relative)
        source = payload.joinpath(*PureWindowsPath(relative).parts)
        if not source.parent.resolve().is_relative_to(payload.resolve()):
            raise YitoError(tr('persist anidado dentro de otro enlace'))
        target = inside(destination if destination is not None else root / 'home/persist' / key, alias)
        defaults = inside(payload / '.mito-defaults', relative)
        if managed.get(relative) == alias and linked(source, target):
            # Rebuilding an intact link costs a PowerShell per entry on every launch.
            continue
        if relative in managed:
            if managed[relative] != alias:
                raise YitoError(tr('Definición persist modificada inesperadamente'))
            if source.is_junction():
                # rmdir removes only the reparse point, never target contents.
                os.rmdir(source)
            elif source.is_file():
                source.unlink()
            elif source.exists():
                raise YitoError(tr('Se esperaba un enlace persistente'))
        elif source.is_junction() or source.is_symlink():
            raise YitoError(tr('No se reemplaza un enlace ajeno al motor'))
        elif source.exists():
            defaults.parent.mkdir(parents=True, exist_ok=True)
            if not source.resolve().is_relative_to(root.resolve()) or not defaults.resolve().is_relative_to(root.resolve()):
                raise YitoError(tr('Ruta persist fuera de MITO'))
            source.rename(defaults)
        if not target.exists() and defaults.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if defaults.is_dir():
                shutil.copytree(defaults, target)
            else:
                shutil.copy2(defaults, target)
        if not target.exists():
            target.mkdir(parents=True)
        source.parent.mkdir(parents=True, exist_ok=True)
        # Journal before creating the link; an interrupted creation is retryable.
        managed[relative] = alias
        write_json(managed_path, managed)
        if target.is_dir():
            def literal(value):
                return "'" + str(value).replace("'", "''") + "'"
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', f'New-Item -ItemType Junction -Path {literal(source)} -Value {literal(target)} -ErrorAction Stop | Out-Null'], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.link(target, source)


def locations(root, lock, profile=None):
    """Inspect declared persistence without creating folders or preparing a payload."""
    from .catalog import effective
    from .isolation import persist_root
    folder = persist_root(root,lock,profile)
    return [(relative,inside(folder,alias)) for relative,alias in definitions(effective(lock['manifest']))]


def open_location(path):
    if not path.exists():
        raise YitoError(tr('Esta persistencia todavía no se ha creado'))
    folder = path if path.is_dir() else path.parent
    subprocess.Popen(['explorer.exe',str(folder)])
