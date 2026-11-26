from .i18n import tr
from .localized_ui import ModalScreen
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static
from .dialog_header import DialogHeader


class ManageProfileDialog(ModalScreen):
    BINDINGS = [('escape', 'cancel', 'Cerrar')]
    DEFAULT_CSS = """
    ManageProfileDialog { align: center middle; background: #00000088; }
    #manage-profile-dialog { width: 62; height: auto; max-height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #manage-profile-dialog > Button { width: 100%; margin-top: 1; }
    #managed-profile-name { text-align: center; margin-bottom: 1; }
    #profile-transfer-actions { height: 4; }
    #profile-transfer-actions Button { width: 1fr; margin-top: 1; }
    #export-profile { margin-right: 2; }
    """

    def __init__(self, profile, can_delete):
        super().__init__()
        self.profile, self.can_delete = profile, can_delete

    def compose(self):
        with VerticalScroll(id='manage-profile-dialog'):
            yield DialogHeader(tr('GESTIONAR PROFILE'), 'manage-profile-close')
            yield Static(self.profile or tr('Ningún profile seleccionado'), id='managed-profile-name', markup=False)
            yield Input(placeholder=tr('Nombre del nuevo profile'), id='new-profile')
            yield Button(tr('Crear profile'), id='create', variant='primary')
            with Horizontal(id='profile-transfer-actions'):
                yield Button(tr('Exportar'), id='export-profile', disabled=not self.profile)
                yield Button(tr('Importar'), id='import-profile')
            yield Button(tr('Acceso directo'), id='mito-shortcut', disabled=not self.profile)
            yield Button(tr('Configuración'), id='profile-configuration', disabled=not self.profile)
            yield Button(tr('Archivos de configuración'), id='profile-files', disabled=not self.profile)
            yield Button(tr('Eliminar profile'), id='delete-profile', variant='error', disabled=not self.can_delete)

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        if action == 'manage-profile-close':
            self.action_cancel()
            return
        name = self.query_one('#new-profile', Input).value.strip() if action == 'create' else None
        if action == 'create' and not name:
            self.query_one('#new-profile', Input).focus()
            return
        self.dismiss((action, name))


class DeleteProfileDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    DeleteProfileDialog { align: center middle; background: #00000088; }
    #delete-profile-dialog { width: 66; height: auto; padding: 1 2; border: round #e27878; background: #153429; }
    #delete-profile-dialog Static { height: auto; margin-bottom: 1; }
    #delete-profile-dialog Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, profile):
        super().__init__()
        self.profile = profile

    def compose(self):
        with VerticalScroll(id='delete-profile-dialog'):
            yield Static(tr('¿Eliminar el profile {p1}?', p1=(self.profile)), markup=False)
            yield Static(tr('Se eliminará su configuración. Se conservan componentes, datos persistentes, home y workspace. Los accesos de este profile dejarán de funcionar.'))
            yield Button(tr('Cancelar'), id='delete-profile-cancel')
            yield Button(tr('Eliminar profile'), id='delete-profile-confirm', variant='error')

    def on_mount(self):
        self.query_one('#delete-profile-cancel', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'delete-profile-confirm')
