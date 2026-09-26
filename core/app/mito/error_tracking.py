"""Local error traces first; optional PostHog delivery with a bounded disk outbox."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import re
import threading
import time
import uuid
from urllib.request import Request

from . import __version__
from . import diagnostics
from .network import NoRedirect, opener, properties, validate_url

_pending = queue.Queue(maxsize=20)
_lock = threading.Lock()
_worker = None
_seen = set()
_attempted = set()
_session = uuid.uuid4().hex
MAX_EVENTS = 100
MAX_EVENT_BYTES = 128 * 1024
MAX_AGE = 7 * 86400


def exception_list(exc, handled=True, root=None):
    result, visited = [], set()
    source = Path(__file__).parent
    root = root or source.parents[2]
    while exc is not None and id(exc) not in visited and len(result) < 5:
        visited.add(id(exc))
        frames = []
        tb = exc.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            path = Path(code.co_filename)
            try:
                filename = 'mito/' + path.relative_to(source).as_posix()
                in_app = True
            except ValueError:
                filename, in_app = path.name, False
            frames.append({'platform': 'custom', 'lang': 'python',
                           'filename': filename, 'function': code.co_name,
                           'lineno': tb.tb_lineno, 'in_app': in_app})
            tb = tb.tb_next
        result.append({'type': type(exc).__name__, 'value': diagnostics.message(root, exc),
                       'module': type(exc).__module__,
                       'mechanism': {'handled': handled, 'synthetic': False},
                       'stacktrace': {'type': 'raw', 'frames': frames[-50:]}})
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return result


def _configuration(root):
    config = properties(root)
    host, token = config.get('posthog.host', ''), config.get('posthog.api_key', '')
    if config.get('posthog.enabled', 'false').lower() != 'true':
        raise ValueError('PostHog está desactivado (posthog.enabled=false)')
    if not host or not token:
        raise ValueError('Falta posthog.host o posthog.api_key')
    parsed = validate_url(host)
    if parsed.query or parsed.fragment:
        raise ValueError('posthog.host no admite query ni fragmento')
    return host.rstrip('/'), token


def _destination(host, token):
    return hashlib.sha256((host.rstrip('/') + '\n' + token).encode()).hexdigest()


def _payload(root, exc, handled, operation, component=None, phase=None):
    component = getattr(exc, '_mito_component', None) or component
    phase = getattr(exc, '_mito_phase', None) or phase
    props = {'distinct_id': _session, '$process_person_profile': False,
             '$geoip_disable': True, '$exception_list': exception_list(exc, handled, root),
             '$lib': 'mito', '$lib_version': __version__, 'mito_version': __version__,
             'mito_operation': diagnostics.scrub(root, operation),
             'mito_runtime': diagnostics.environment()}
    for key, value in (('mito_component', component), ('mito_phase', phase)):
        if value is not None:
            props[key] = diagnostics.scrub(root, value)
    return {'uuid': str(uuid.uuid4()), 'timestamp': datetime.now(timezone.utc).isoformat(),
            'event': '$exception', 'properties': props}


def _send(root, host, token, payload, timeout=5):
    request = Request(host.rstrip('/') + '/i/v0/e/',
                      data=json.dumps({'token': token, **payload}).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with opener(root, None, NoRedirect()).open(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise OSError('PostHog rejected the event')
        return response.status


def _files(folder):
    return [p for p in folder.glob('*.json') if re.fullmatch(r'[0-9a-f]{32}\.json', p.name)
            and p.is_file() and not p.is_symlink()]


def _prune(folder, reserve=0):
    # Atomic writes interrupted by process termination never become queued events.
    for path in folder.glob('*.tmp'):
        if re.fullmatch(r'[0-9a-f]{32}\.tmp', path.name) and not path.is_symlink():
            path.unlink(missing_ok=True)
    files = sorted(_files(folder), key=lambda p: p.stat().st_mtime)
    for path in files[:]:
        if time.time() - path.stat().st_mtime > MAX_AGE or path.stat().st_size > MAX_EVENT_BYTES:
            path.unlink(missing_ok=True)
            files.remove(path)
    for path in files[:max(0, len(files) - MAX_EVENTS + reserve)]:
        path.unlink(missing_ok=True)


def _store(root, payload, host, token):
    data = json.dumps({'schema': 1, 'destination': _destination(host, token), 'event': payload}).encode()
    if len(data) > MAX_EVENT_BYTES:
        raise ValueError('Diagnostic event exceeds disk limit')
    for base in diagnostics.locations(root):
        try:
            with diagnostics.guard(base):
                folder = base / 'pending'
                folder.mkdir(exist_ok=True)
                _prune(folder, reserve=1)
                path = folder / (uuid.UUID(payload['uuid']).hex + '.json')
                temporary = path.with_suffix('.tmp')
                try:
                    temporary.write_bytes(data)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                return path
        except OSError:
            continue
    raise OSError('Cannot persist pending PostHog event')


def _enqueue(root, path):
    global _worker
    with _lock:
        if str(path) in _attempted or len(_attempted) >= 20:
            return
        _pending.put_nowait((root, path))
        _attempted.add(str(path))
        if _worker is None:
            _worker = threading.Thread(target=_drain, name='mito-errors', daemon=True)
            try:
                _worker.start()
            except Exception:
                _worker = None
                while not _pending.empty():
                    _pending.get_nowait(); _pending.task_done()
                raise


def _drain():
    global _worker
    while True:
        with _lock:
            try:
                root, path = _pending.get_nowait()
            except queue.Empty:
                _worker = None
                return
        try:
            host, token = _configuration(root)
            with diagnostics.guard(path.parent.parent):
                if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_EVENT_BYTES:
                    continue
                record = json.loads(path.read_text(encoding='utf-8-sig'))
            if record.get('schema') != 1 or record.get('destination') != _destination(host, token):
                continue
            event = record['event']
            if event.get('event') != '$exception' or not isinstance(event.get('properties'), dict):
                raise ValueError('Invalid pending error event')
            _send(root, host, token, event)
            with diagnostics.guard(path.parent.parent):
                path.unlink(missing_ok=True)
            diagnostics.write(root, 'info', 'posthog', 'Event accepted', event_id=event['uuid'])
        except Exception as exc:
            diagnostics.write(root, 'warning', 'posthog', diagnostics.message(root, exc),
                              event_id=path.stem, pending=True)
        finally:
            _pending.task_done()


def resume(root):
    """Retry up to 20 pending events at process startup; no polling or timer."""
    try:
        host, token = _configuration(root)
        for base in diagnostics.locations(root):
            folder = base / 'pending'
            if not folder.is_dir():
                continue
            with diagnostics.guard(base):
                _prune(folder)
                files = sorted(_files(folder), key=lambda p: p.stat().st_mtime)
            for path in files:
                try:
                    record = json.loads(path.read_text(encoding='utf-8-sig'))
                    if record.get('destination') != _destination(host, token):
                        continue
                except (OSError, ValueError):
                    continue
                _enqueue(root, path)
    except Exception as exc:
        if not isinstance(exc, ValueError):
            diagnostics.write(root, 'warning', 'posthog.resume', diagnostics.message(root, exc))


def capture(root, exc, *, handled=True, operation='error', component=None, phase=None):
    if not isinstance(exc, Exception):
        return False
    try:
        payload = _payload(root, exc, handled, operation, component, phase)
        diagnostics.write(root, 'error', operation, diagnostics.message(root, exc),
                          event_id=payload['uuid'], version=__version__, runtime=diagnostics.environment(),
                          exceptions=payload['properties']['$exception_list'],
                          component=payload['properties'].get('mito_component'),
                          phase=payload['properties'].get('mito_phase'))
        host, token = _configuration(root)
        signature = json.dumps([str(root), payload['properties']['$exception_list'], operation], sort_keys=True)
        with _lock:
            if signature in _seen or len(_seen) >= 20:
                return False
            _seen.add(signature)
        path = _store(root, payload, host, token)
        _enqueue(root, path)
        return True
    except ValueError:
        return False
    except Exception as failure:
        diagnostics.write(root, 'warning', 'posthog.queue', diagnostics.message(root, failure))
        return False


def check(root):
    host, token = _configuration(root)
    try:
        raise RuntimeError('MITO PostHog diagnostic')
    except RuntimeError as exc:
        payload = _payload(root, exc, True, 'posthog-check')
    payload['properties']['mito_diagnostic'] = True
    path = _store(root, payload, host, token)
    diagnostics.write(root, 'info', 'posthog-check', 'Sending diagnostic event', event_id=payload['uuid'])
    try:
        status = _send(root, host, token, payload, timeout=10)
    except Exception as exc:
        diagnostics.write(root, 'error', 'posthog-check', diagnostics.message(root, exc), pending=True)
        raise
    with diagnostics.guard(path.parent.parent):
        path.unlink(missing_ok=True)
    diagnostics.write(root, 'info', 'posthog-check', f'HTTP {status}', event_id=payload['uuid'])
    return status


def flush(timeout=6):
    with _lock:
        worker = _worker
    if worker is not None and worker is not threading.current_thread():
        worker.join(timeout)


def report_threads(root):
    previous = threading.excepthook
    def hook(args):
        capture(root, args.exc_value, handled=False, operation='thread.unhandled')
        previous(args)
    threading.excepthook = hook
    return previous
