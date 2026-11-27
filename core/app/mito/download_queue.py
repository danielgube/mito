"""Cola de descargas en segundo plano del gestor: un hilo, en orden.

Sólo descarga a la caché (download_cache): nunca instala ni sustituye nada, así que
cerrar MITO la pausa sin daño. La cola se guarda en core/state/download-queue.json
y continúa al volver a abrir el gestor; los parciales con hash se reanudan.
Instalar o actualizar después es explícito y usa lo descargado.

No es un proceso aparte: vive y termina con el gestor.
"""
from .i18n import tr
import threading
import uuid
from .storage import read_json, write_json

KINDS = ('install', 'update')
STATE = 'core/state/download-queue.json'
WAITING = ('pending', 'downloading')


class DownloadQueue:
    def __init__(self, engine, log=lambda message, level='info': None, confirm_unverified=None, changed=lambda: None,
                 failed=lambda exc: None):
        """log, confirm_unverified, changed y failed se llaman desde el hilo de la cola."""
        self.engine, self.log, self.confirm, self.changed = engine, log, confirm_unverified, changed
        self.failed = failed
        self.items = []
        self.lock = threading.Lock()
        self.thread = None
        self.operation = None

    # Estado -----------------------------------------------------------------

    def _item(self, kind, target, status='pending'):
        return {'id': uuid.uuid4().hex[:12], 'kind': kind, 'target': target, 'status': status,
                'message': '', 'progress': {}}

    def snapshot(self):
        with self.lock:
            return [dict(item, progress=dict(item['progress'])) for item in self.items]

    def active(self):
        with self.lock:
            return sum(item['status'] in WAITING for item in self.items)

    def find(self, kind, target):
        with self.lock:
            return next((dict(item) for item in self.items if item['kind'] == kind and item['target'] == target), None)

    def save(self):
        """Caller holds self.lock. Interrupted downloads are persisted as pending."""
        data = [{'kind': item['kind'], 'target': item['target'],
                 'status': 'done' if item['status'] == 'done' else 'pending'}
                for item in self.items if item['status'] in WAITING + ('done',)]
        try:
            write_json(self.engine.root / STATE, data)
        except OSError as exc:
            from .error_tracking import capture
            capture(self.engine.root, exc, operation='downloads.save')

    def ready(self, item):
        """A done item is still useful only while its download is in the cache and pending."""
        from .components import update_downloaded
        from .download_cache import downloaded
        try:
            if item['kind'] == 'update':
                lock = self.engine.installed().get(item['target'])
                return bool(lock) and update_downloaded(self.engine, lock)
            key, manifest = self.engine.catalog.resolve(item['target'])
            installed = self.engine.installed().get(key)
            from .catalog import fingerprint
            if installed and installed['manifest_sha256'] == fingerprint(manifest):
                return False
            return downloaded(self.engine.root, key, manifest)
        except Exception:
            return False

    def load(self):
        """Restore the persisted queue and resume pending downloads."""
        path = self.engine.root / STATE
        try:
            data = read_json(path) if path.exists() else []
        except (OSError, ValueError):
            data = []
        restored = []
        for value in data if isinstance(data, list) else []:
            if (isinstance(value, dict) and value.get('kind') in KINDS and isinstance(value.get('target'), str)
                    and not any(item['kind'] == value['kind'] and item['target'] == value['target'] for item in restored)):
                restored.append(self._item(value['kind'], value['target'],
                                           'done' if value.get('status') == 'done' else 'pending'))
        restored = [item for item in restored if item['status'] == 'pending' or self.ready(item)]
        with self.lock:
            self.items = restored
            self.save()
        pending = sum(item['status'] == 'pending' for item in restored)
        if pending:
            self.log(tr('Reanudando {p1} descargas pendientes en segundo plano', p1=pending))
            self._start()
        return pending

    # Acciones ---------------------------------------------------------------

    def enqueue(self, kind, target):
        if kind not in KINDS:
            raise ValueError(kind)
        with self.lock:
            existing = next((item for item in self.items if item['kind'] == kind and item['target'] == target), None)
            if existing is not None and existing['status'] in WAITING + ('done',):
                return dict(existing)
            if existing is not None:
                self.items.remove(existing)
            item = self._item(kind, target)
            self.items.append(item)
            self.save()
        self.log(tr('En cola para descargar en segundo plano: {p1}', p1=target))
        self._start()
        self.changed()
        return dict(item)

    def cancel(self, item_id):
        with self.lock:
            item = next((item for item in self.items if item['id'] == item_id), None)
            if item is None:
                return
            if item['status'] == 'pending':
                item['status'], item['message'] = 'cancelled', tr('Cancelada')
                self.save()
            elif item['status'] == 'downloading' and self.operation is not None:
                self.operation.cancel()
        self.changed()

    def remove(self, item_id):
        """Forget a finished item; a downloaded but unused file is discarded."""
        from .download_cache import discard
        with self.lock:
            item = next((item for item in self.items if item['id'] == item_id and item['status'] not in WAITING), None)
            if item is None:
                return
            self.items.remove(item)
            self.save()
        if item['status'] == 'done':
            key = item['target']
            if item['kind'] == 'update':
                lock = self.engine.installed().get(item['target'])
                key = lock['key'] if lock else None
            if key:
                discard(self.engine.root, key)
        self.changed()

    def forget(self, kind, target):
        """Drop the item once the user installs or updates it."""
        with self.lock:
            self.items = [item for item in self.items
                          if not (item['kind'] == kind and item['target'] == target and item['status'] not in WAITING)]
            self.save()
        self.changed()

    # Hilo -------------------------------------------------------------------

    def _start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            # Daemon: closing the manager pauses the queue instead of waiting for it.
            self.thread = threading.Thread(target=self._run, name='mito-download-queue', daemon=True)
            self.thread.start()

    def _progress(self, item, state):
        with self.lock:
            item['progress'] = {key: state.get(key) for key in ('title', 'text', 'received', 'total', 'unit')}
        self.changed()

    def _run(self):
        from .operation import Cancelled, Operation, monitored
        while True:
            with self.lock:
                item = next((item for item in self.items if item['status'] == 'pending'), None)
                if item is None:
                    self.thread = None
                    return
                item['status'], item['message'], item['progress'] = 'downloading', '', {}
                operation = Operation(lambda state, item=item: self._progress(item, state))
                self.operation = operation
            self.changed()
            try:
                with monitored(operation):
                    self._download(item)
                status, message = 'done', ''
                self.log(tr('Descarga en segundo plano completa: {p1}', p1=item['target']))
            except Cancelled:
                status, message = 'cancelled', tr('Cancelada')
                self.log(tr('Descarga en segundo plano cancelada: {p1}', p1=item['target']))
            except Exception as exc:
                from .error_tracking import capture
                capture(self.engine.root, exc, operation='downloads.' + item['kind'], component=item['target'])
                status, message = 'failed', str(exc)
                self.log(tr('Descarga en segundo plano fallida: {p1}: {p2}', p1=item['target'], p2=exc), 'error')
                self.failed(exc)
            with self.lock:
                item['status'], item['message'] = status, message
                self.operation = None
                self.save()
            self.changed()

    def _download(self, item):
        log = lambda message: self.log(message)
        if item['kind'] == 'update':
            from .components import download_update
            download_update(self.engine, item['target'], log, confirm_unverified=self.confirm)
            return
        # Same freshness as installing from the catalog: the manifest the install will use.
        self.engine.catalog.refresh_keys([item['target']], log)
        self.engine.download_only(item['target'], log=log, confirm_unverified=self.confirm)
