from __future__ import annotations
from .i18n import tr

import contextlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile


class YitoError(Exception):
    pass


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", value):
        raise YitoError(tr('Nombre no válido: {p1}', p1=(f'{value!r}')))
    if value.endswith('.') or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'{p}{i}' for p in ('COM', 'LPT') for i in range(10)]}:
        raise YitoError(tr('Nombre reservado: {p1}', p1=(value)))
    return value.lower()


def portable_path(relative: str) -> PureWindowsPath:
    """Validate manifest syntax without consulting the host filesystem."""
    win = PureWindowsPath(relative)
    if win.root or win.drive or '..' in win.parts or any(':' in p for p in win.parts):
        raise YitoError(tr('Ruta no portable: {p1}', p1=(relative)))
    for part in win.parts:
        if part not in ('.', '') and (any(c in '<>"|?*' or ord(c) < 32 for c in part) or part.endswith((' ', '.')) or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'{p}{i}' for p in ('COM', 'LPT') for i in range(10)]}):
            raise YitoError(tr('Ruta reservada: {p1}', p1=(relative)))
    return win


def inside(root: Path, relative: str) -> Path:
    win = portable_path(relative)
    path = root.joinpath(*win.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise YitoError(tr('Ruta fuera de su raíz: {p1}', p1=(relative)))
    return path


def command_name(value: str) -> str:
    """A single Windows filename, distinct from internal package/profile IDs."""
    if not isinstance(value, str) or not value.strip() or len(value) > 240 or '/' in value or '\\' in value:
        raise YitoError(tr('Nombre de comando no válido: {p1}', p1=(f'{value!r}')))
    portable_path(value + '.cmd')
    # Validate the alias itself too (reserved names, trailing dots/spaces).
    portable_path(value)
    if value in ('.', '..'):
        raise YitoError(tr('Nombre de comando no válido: {p1}', p1=(f'{value!r}')))
    return value.lower()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def mutation(root: Path, name='mutation.lock', wait=0.0):
    """OS lock: released even when the process crashes.

    wait > 0 reintenta durante esos segundos (locks breves, como las comprobaciones
    diarias que la UI lanza en paralelo); por defecto falla en el acto."""
    state = root / 'core/state'
    state.mkdir(parents=True, exist_ok=True)
    with (state / identifier(name)).open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            import time
            deadline = time.monotonic() + max(0.0, wait)
            while True:
                try:
                    # Leer un byte bloqueado por otro handle da PermissionError: también
                    # significa «ocupado», no un error de permisos real.
                    stream.seek(0)
                    if not stream.read(1):
                        stream.write(b'0')
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise YitoError(tr('Otra instancia está modificando MITO. Espera a que termine.')) from exc
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def root_path():
    return Path(__file__).resolve().parents[3]
