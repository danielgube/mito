"""Dependency-free, bounded local diagnostics, usable by bootstrap Python."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit

MAX_BYTES = 1024 * 1024
BACKUPS = 3
_mutex = threading.RLock()
_warned = set()


def locations(root):
    identity = hashlib.sha256(str(Path(root).resolve()).lower().encode()).hexdigest()[:16]
    return (Path(root) / 'logs', Path(tempfile.gettempdir()) / 'MITO-logs' / identity)


def scrub(root, value):
    text = str(value)
    # Never log URL credentials or signed query parameters.
    def url(match):
        try:
            parsed = urlsplit(match[0])
            host = parsed.netloc.rsplit('@', 1)[-1]
            return urlunsplit((parsed.scheme, host, parsed.path, '[redacted]' if parsed.query else '', ''))
        except ValueError:
            return '[url]'
    text = re.sub(r'https?://[^\s<>"\']+', url, text, flags=re.I)
    text = re.sub(r'(?i)\b(?:ph[cp]_|gh[pousr]_)[A-Za-z0-9_]+', '[redacted]', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', text)
    text = re.sub(r'(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+', '<USER>', text)
    text = re.sub(r'''(?ix)(["']?(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', r'\1[redacted]', text)
    for path, label in ((str(Path(root).resolve()), '<MITO>'),
                        (os.environ.get('USERPROFILE', ''), '<USER>')):
        if path:
            text = re.sub(re.escape(path), lambda _: label, text, flags=re.I)
            text = re.sub(re.escape(path.replace('\\', '/')), lambda _: label, text, flags=re.I)
    return text[:12000]


def message(root, exc):
    if isinstance(exc, subprocess.CalledProcessError):
        return f'Child process failed with exit code {exc.returncode}'
    if isinstance(exc, subprocess.TimeoutExpired):
        return f'Child process exceeded timeout of {exc.timeout} seconds'
    return scrub(root, exc)


def environment():
    result = {'python': sys.version.split()[0], 'bits': 64 if sys.maxsize > 2**32 else 32,
              'platform': sys.platform}
    if hasattr(sys, 'getwindowsversion'):
        win = sys.getwindowsversion()
        result['windows'] = f'{win.major}.{win.minor}.{win.build}'
    return result


@contextmanager
def step(root, operation, phase, component=None):
    write(root, 'info', operation, phase, component=scrub(root, component) if component else None)
    try:
        yield
    except Exception as exc:
        # Attach only explicit labels, never arbitrary locals or subprocess arguments.
        if not hasattr(exc, '_mito_phase'):
            exc._mito_phase = phase
            exc._mito_component = component
        raise


@contextmanager
def guard(folder):
    """Byte lock shared with .NET FileStream.Lock in the PowerShell bootstrap."""
    folder.mkdir(parents=True, exist_ok=True)
    with _mutex, (folder / '.diagnostics.lock').open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'\0'); stream.flush()
        locked = False
        try:
            for _ in range(10):
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    time.sleep(.02)
            if not locked:
                raise OSError('Diagnostic lock busy')
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def write(root, level, operation, text, **details):
    """Never allow logging failure to replace the original application error."""
    try:
        record = {'time': datetime.now(timezone.utc).isoformat(), 'level': level,
                  'pid': os.getpid(), 'operation': scrub(root, operation),
                  'message': scrub(root, text), **details}
        # Details are constructed by MITO, never locals, argv or environment dumps.
        data = (json.dumps(record, ensure_ascii=True, default=str) + '\n').encode()
        if len(data) > 128 * 1024:
            record = {k: v for k, v in record.items() if k != 'exceptions'}
            record['truncated'] = True
            data = (json.dumps(record, ensure_ascii=True, default=str) + '\n').encode()
        for folder in locations(root):
            try:
                with guard(folder):
                    target = folder / 'mito.log'
                    if target.exists() and target.stat().st_size + len(data) > MAX_BYTES:
                        (folder / f'mito.log.{BACKUPS}').unlink(missing_ok=True)
                        for index in range(BACKUPS - 1, 0, -1):
                            previous = folder / f'mito.log.{index}'
                            if previous.exists():
                                previous.replace(folder / f'mito.log.{index + 1}')
                        target.replace(folder / 'mito.log.1')
                    with target.open('ab') as stream:
                        stream.write(data)
                if folder != locations(root)[0] and str(folder) not in _warned:
                    _warned.add(str(folder))
                    print(f'MITO: log alternativo: {folder}', file=sys.stderr)
                return target
            except OSError:
                continue
        if 'unwritable' not in _warned:
            _warned.add('unwritable')
            print('MITO: no se pudo escribir el log local ni el alternativo.', file=sys.stderr)
    except Exception:
        pass
    return None
