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


class HostDiscoverDialog(ModalScreen[object]):
    """Pick a discovered host application. Returns a path, 'browse' or None."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    HostDiscoverDialog { align: center middle; background: #00000088; }
    #host-discover { width: 90%; max-width: 110; height: auto; max-height: 100%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #host-discover > Static { height: auto; margin-bottom: 1; }
    #host-discover-list { height: 16; margin: 1 0; }
    #host-discover-actions { height: 3; align-horizontal: right; }
    #host-discover-actions Button { margin-left: 1; }
    """

    def __init__(self, found):
        super().__init__()
        self.found = list(found)
        self.shown = []

    def compose(self):
        from textual.widgets import Input, OptionList
        with Vertical(id='host-discover'):
            yield DialogHeader(tr('AGREGAR APLICACIÓN DEL HOST'), 'host-discover-close')
            yield Static(tr('Aplicaciones encontradas en el menú Inicio y el escritorio del host. Si falta alguna, usa Examinar.'), markup=False)
            yield Input(placeholder=tr('Filtrar…'), id='host-discover-filter')
            yield OptionList(id='host-discover-list')
            with Horizontal(id='host-discover-actions'):
                yield Button(tr('Examinar…'), id='host-discover-browse')
                yield Button(tr('Cancelar'), id='host-discover-cancel')
                yield Button(tr('Agregar'), id='host-discover-add', variant='success')

    def on_mount(self):
        self.refresh_list('')
        self.query_one('#host-discover-filter').focus()

    def refresh_list(self, text):
        from textual.widgets import OptionList
        words = text.casefold().split()
        self.shown = [(label, path) for label, path in self.found
                      if all(word in f'{label} {path}'.casefold() for word in words)]
        options = self.query_one('#host-discover-list', OptionList)
        options.clear_options()
        options.add_options([f'{label}  ·  {path}' for label, path in self.shown]
                            or [tr('No se ha encontrado ninguna aplicación.')])
        options.highlighted = 0 if self.shown else None
        self.query_one('#host-discover-add', Button).disabled = not self.shown

    def selected(self):
        from textual.widgets import OptionList
        index = self.query_one('#host-discover-list', OptionList).highlighted
        return self.shown[index][1] if index is not None and 0 <= index < len(self.shown) else None

    def on_input_changed(self, event):
        event.stop()
        self.refresh_list(event.value)

    def on_input_submitted(self, event):
        event.stop()
        if self.selected():
            self.dismiss(self.selected())

    def on_option_list_option_selected(self, event):
        event.stop()
        if self.selected():
            self.dismiss(self.selected())

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'host-discover-add':
            if self.selected():
                self.dismiss(self.selected())
        elif event.button.id == 'host-discover-browse':
            self.dismiss('browse')
        else:
            self.dismiss(None)


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
