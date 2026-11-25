"""Progress and cooperative cancellation for the operation running in this thread.

The interface installs an Operation with monitored(); the engine and transfer
read it through current() without extra parameters. Without an Operation every
helper is a no-op, so the CLI and other callers keep their behaviour."""
from contextlib import contextmanager
import contextvars
import threading
import time

from .i18n import tr
from .storage import YitoError

_current = contextvars.ContextVar('mito_operation', default=None)


class Cancelled(YitoError):
    """The user cancelled the operation before publishing anything."""


class Operation:
    def __init__(self, listener=None):
        self.listener = listener
        self._event = threading.Event()
        self.state = {'title': '', 'text': '', 'received': None, 'total': None,
                      'unit': 'bytes', 'note': '', 'cancellable': True, 'active': False}

    def _emit(self):
        if self.listener is not None:
            self.listener(dict(self.state))

    def phase(self, title, text, cancellable=True):
        self.state.update(title=title, text=text, received=None, total=None, unit='bytes',
                          note='', cancellable=cancellable and not self.cancelled, active=True,
                          started=time.monotonic())
        self._emit()

    def transfer(self, received, total=None, unit='bytes'):
        self.state.update(received=received, total=total, unit=unit)
        self._emit()

    def note(self, text):
        self.state['note'] = text
        self._emit()

    def finish(self):
        self.state['active'] = False
        self._emit()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        """Callable from any thread; downloads notice it within 0.25 s."""
        self._event.set()

    def check(self):
        if self._event.is_set():
            raise Cancelled(tr('Instalación cancelada'))

    def wait(self, seconds):
        """Sleep that ends early, with Cancelled, when the user cancels."""
        if self._event.wait(max(0.0, seconds)):
            self.check()

def current():
    return _current.get()


@contextmanager
def monitored(operation):
    token = _current.set(operation)
    try:
        yield operation
    finally:
        _current.reset(token)
        operation.finish()


def checkpoint():
    operation = current()
    if operation is not None:
        operation.check()


def phase(title, text, cancellable=True):
    operation = current()
    if operation is not None:
        operation.phase(title, text, cancellable)


def transfer(received, total=None, unit='bytes'):
    operation = current()
    if operation is not None:
        operation.transfer(received, total, unit)


def note(text):
    operation = current()
    if operation is not None:
        operation.note(text)


def wait(seconds):
    operation = current()
    if operation is None:
        time.sleep(max(0.0, seconds))
    else:
        operation.wait(seconds)
