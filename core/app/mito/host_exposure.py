"""Explicit, instance-wide junctions from the host AppData into portable AppData.

Inverse of host_data: an application started directly by the host (autostart,
file associations, another program) finds the data it uses inside MITO. Only
data is shared; the profile environment is not applied. Candidates are generic:
folders of home/AppData whose name contains a declared executable name.
Existing host folders are renamed aside, never deleted, and restored on unlink.
"""
import os
from pathlib import Path
import subprocess
import time

from .host_data import KINDS, destination, host_folder
from .i18n import tr
from .storage import YitoError, mutation, read_json, write_json

REGISTRY = 'core/state/host-exposed-links.json'


def registry(root):
    file = root/REGISTRY
    return read_json(file) if file.exists() else {}


def save(root, records):
    write_json(root/REGISTRY, records)


def normal(path):
    value = str(path)
    for prefix in ('\\\\?\\', '\\??\\'):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return os.path.normcase(os.path.normpath(value))


def points_to(link, folder):
    """Compare the stored junction target without resolving it (it may dangle)."""
    try:
        return link.is_junction() and normal(os.readlink(link)) == normal(folder)
    except (OSError, ValueError):
        return False


def junction_target(link):
    try:
        return normal(os.readlink(link)) if link.is_junction() else None
    except (OSError, ValueError):
        return None


def words(engine, lock):
    names = {Path(executable).stem.casefold() for _, executable, _ in engine.application_entries(lock)}
    return sorted(name for name in names if len(name) >= 3)


def candidates(engine, lock):
    """(kind, relative) of real folders in MITO's AppData named after an executable."""
    found = []
    names = words(engine, lock)
    if not names:
        return found
    for kind, folder in KINDS.items():
        base = engine.root/'home/AppData'/folder
        if not base.is_dir():
            continue
        for directory, children, _ in os.walk(base, followlinks=False):
            descend = []
            for name in sorted(children, key=str.casefold):
                path = Path(directory)/name
                try:
                    if path.is_junction() or path.is_symlink():
                        continue
                except OSError:
                    continue
                if any(word in name.casefold() for word in names):
                    found.append((kind, str(path.relative_to(base))))
                else:
                    descend.append(name)
            children[:] = descend
    return found


def plan(root, kind, relative):
    """Return (MITO folder, host path) for a relative AppData folder."""
    source = destination(root, kind, relative)
    base = root/'home/AppData'/KINDS[kind]
    current = source
    while current != base:
        if current.is_junction() or current.is_symlink():
            raise YitoError(tr('No se reemplazan enlaces ajenos a MITO'))
        current = current.parent
    if not source.is_dir():
        raise YitoError(tr('Se esperaba una carpeta de datos en MITO: {p1}', p1=(source)))
    host_base = host_folder(kind)
    host = host_base/relative
    if host == host_base or not host.parent.resolve().is_relative_to(host_base.resolve()):
        raise YitoError(tr('Destino fuera del AppData de MITO'))
    if host_base.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(host.resolve()):
        raise YitoError(tr('Selecciona datos externos a MITO, sin enlaces circulares'))
    return source, host


def create_junction(path, folder):
    literal = lambda value: "'" + str(value).replace("'", "''") + "'"
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    f'New-Item -ItemType Junction -Path {literal(path)} -Value {literal(folder)} -ErrorAction Stop | Out-Null'],
                   check=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def backup_path(host):
    return host.with_name(f'{host.name}.mito-backup-{time.strftime("%Y%m%d-%H%M%S")}')


def entries(root, installation):
    """[(kind, record)] registered for the installation."""
    return [(kind, item) for kind, items in registry(root).get(installation, {}).items()
            if kind in KINDS for item in items]


def linked(root, installation):
    for kind, item in entries(root, installation):
        try:
            source, host = plan(root, kind, item['relative'])
        except (OSError, YitoError):
            continue
        if points_to(host, source):
            return True
    return False


def link(engine, installation, kind, relative, aside=False):
    root = engine.root
    with mutation(root):
        if installation not in engine.installed():
            raise YitoError(tr('El componente ya no está instalado'))
        source, host = plan(root, kind, relative)
        records = registry(root)
        for owner, app in records.items():
            for category, items in app.items():
                for item in items:
                    if category not in KINDS:
                        continue
                    other = host_folder(category)/item['relative']
                    if host == other or host.is_relative_to(other) or other.is_relative_to(host):
                        if owner == installation and category == kind and host == other and points_to(host, source):
                            return None
                        raise YitoError(tr('Esta ruta del host ya está gestionada por MITO'))
        if points_to(host, source):
            records.setdefault(installation, {}).setdefault(kind, []).append({'relative': relative, 'backup': None})
            save(root, records)
            return None
        if host.is_junction() or host.is_symlink():
            raise YitoError(tr('No se reemplazan enlaces ajenos a MITO'))
        moved = None
        if host.exists():
            if not aside or not host.is_dir():
                raise YitoError(tr('La carpeta del host ya existe: {p1}', p1=(host)))
            moved = backup_path(host)
            host.rename(moved)  # Never delete host data; restore it on unlink.
        host.parent.mkdir(parents=True, exist_ok=True)
        try:
            create_junction(host, source)
            records.setdefault(installation, {}).setdefault(kind, []).append(
                {'relative': relative, 'backup': str(moved) if moved else None})
            save(root, records)
        except Exception:
            if host.is_junction():
                os.rmdir(host)
            if moved is not None:
                moved.rename(host)
            raise
        return moved


def unlink_locked(root, installation):
    records = registry(root)
    for kind, item in entries(root, installation):
        # MITO's folder may be gone; the junction is still identified by its target.
        source = destination(root, kind, item['relative'])
        host = host_folder(kind)/item['relative']
        if host.is_junction():
            if not points_to(host, source):
                raise YitoError(tr('El enlace ha cambiado fuera de MITO: {p1}', p1=(host)))
            os.rmdir(host)  # Removes only the junction, never MITO's data.
        elif host.exists() or host.is_symlink():
            raise YitoError(tr('Se esperaba una junction gestionada: {p1}', p1=(host)))
        backup = item.get('backup')
        if backup and Path(backup).is_dir() and not host.exists():
            Path(backup).rename(host)
    if installation in records:
        records.pop(installation)
        save(root, records)


def unlink(root, installation):
    with mutation(root):
        unlink_locked(root, installation)


def relocate(root, old, copied, log=print):
    """Retarget junctions after a move; a copy leaves them to the original instance."""
    try:
        records = registry(root)
    except (OSError, ValueError):
        return 0
    old_root = normal(old)
    changed = 0
    for installation in list(records):
        kinds = records[installation]
        for kind in list(kinds):
            if kind not in KINDS:
                continue
            kept = []
            for item in kinds[kind]:
                host = host_folder(kind)/item['relative']
                target = junction_target(host)
                if target is None or not (target == old_root or target.startswith(old_root + os.sep)):
                    kept.append(item)
                    continue
                if copied:
                    changed += 1
                    continue
                try:
                    source, host = plan(root, kind, item['relative'])
                    os.rmdir(host)
                    create_junction(host, source)
                    log(tr('Enlaces del host actualizados a la nueva ruta: {p1}', p1=(host)))
                    changed += 1
                except (OSError, YitoError, subprocess.SubprocessError) as exc:
                    log(tr('No se pudo actualizar el enlace del host {p1}: {p3}', p1=(host), p3=(exc)))
                kept.append(item)
            if kept:
                kinds[kind] = kept
            else:
                kinds.pop(kind)
        if not kinds:
            records.pop(installation)
    if changed:
        save(root, records)
    return changed
