from .i18n import tr
from textual.containers import Horizontal, Vertical
from .localized_ui import ModalScreen
from textual.widgets import Button, Static

from .dialog_header import DialogHeader
from .storage import YitoError


class HostInfoDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    HostInfoDialog { align: center middle; background: #00000088; }
    #host-info { width: 90%; max-width: 110; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #host-info > Static { height: auto; }
    #host-location { height: auto; min-height: 3; margin-top: 1; align-vertical: middle; }
    #host-location Static { width: 1fr; }
    #host-location Button { width: 10; min-width: 10; margin-left: 2; }
    """

    def __init__(self, path):
        super().__init__()
        self.path = path

    def compose(self):
        with Vertical(id='host-info'):
            yield DialogHeader(tr('APLICACIÓN DEL HOST'), 'host-info-close')
            yield Static(tr('{p0}\n{p2}\nSe ejecuta con el entorno del profile de MITO seleccionado, incluido APPDATA.', p0=(self.path.name), p2=(self.path)), markup=False)
            with Horizontal(id='host-location'):
                yield Static(tr('Carpeta del ejecutable\n{p1}', p1=(self.path.parent)), markup=False)
                yield Button(tr('Abrir'), id='host-folder-open', disabled=not self.path.parent.is_dir())
            if not self.path.is_file():
                yield Static(tr('El archivo ya no está disponible.'))

    def action_close(self):
        self.dismiss()

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'host-info-close':
            self.action_close()
        elif event.button.id == 'host-folder-open':
            from .persistence import open_location
            try:
                open_location(self.path.parent)
            except (OSError, YitoError) as exc:
                self.notify(str(exc), severity='error')


class RemoveHostDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    RemoveHostDialog { align: center middle; background: #00000088; }
    #remove-host { width: 70; height: auto; padding: 1 2; border: round #e27878; background: #153429; }
    #remove-host Static { height: auto; margin-bottom: 1; }
    #remove-host Button { margin-right: 1; }
    #remove-host Horizontal { height: 3; }
    """

    def __init__(self, path):
        super().__init__()
        self.path = path

    def compose(self):
        with Vertical(id='remove-host'):
            yield Static(tr('¿Quitar {p1} de MITO?', p1=(self.path.name)), markup=False)
            yield Static(tr('Se eliminan la referencia y sus enlaces de caché/datos. El ejecutable del host y sus datos se conservan.'))
            with Horizontal():
                yield Button(tr('Cancelar'), id='host-remove-cancel')
                yield Button(tr('Eliminar'), id='host-remove-confirm', variant='error')

    def on_mount(self):
        self.query_one('#host-remove-cancel', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'host-remove-confirm')


class HostDataConfirm(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    HostDataConfirm { align: center middle; background: #00000088; }
    #host-data-confirm { width: 90%; max-width: 110; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #host-data-confirm Static { height: auto; margin-bottom: 1; }
    #host-data-confirm Horizontal { height: 3; }
    #host-data-confirm Button { width: auto; margin-right: 2; }
    """

    def __init__(self, message, label=None):
        super().__init__()
        self.message, self.label = message, label if label is not None else tr('Enlazar')

    def compose(self):
        with Vertical(id='host-data-confirm'):
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button(tr('Cancelar'), id='host-data-cancel')
                yield Button(self.label, id='host-data-accept', variant='warning')

    def on_mount(self):
        self.query_one('#host-data-cancel', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'host-data-accept')
