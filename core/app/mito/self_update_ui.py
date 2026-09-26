"""Human review before downloading a MITO release or handing off the manager."""
from textual.containers import Vertical, Horizontal
from textual.widgets import Button, Static
from .dialog_header import DialogHeader
from .localized_ui import ModalScreen
from .i18n import tr


class SelfUpdateDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = '''
    SelfUpdateDialog { align: center middle; background: $background 70%; }
    #self-update-dialog { width: 76; height: auto; max-height: 95%; padding: 1 2; border: round #b5dc64; background: #153429; overflow-y: auto; }
    #self-update-dialog Static { height: auto; margin-bottom: 1; }
    #self-update-dialog Horizontal { height: 3; align-horizontal: right; }
    #self-update-dialog Button { margin-left: 1; }
    '''

    def __init__(self, info):
        super().__init__()
        self.info = info

    def compose(self):
        with Vertical(id='self-update-dialog'):
            yield DialogHeader(tr('Actualizar MITO'), 'self-update-close')
            yield Static(self.info['current'] + ' → ' + self.info['version'], markup=False)
            if self.info.get('local_path'):
                yield Static('Offline\n' + self.info['local_path'], markup=False)
            yield Static(tr('Guarda tu trabajo y cierra todas las aplicaciones y pestañas de MITO, incluidas las aplicaciones del host abiertas desde MITO. El gestor cerrará MCP al salir.'))
            if self.info.get('local_path'):
                yield Static(tr('El ZIP local se copiará y validará sin conexión. Después se abrirá un CMD independiente y se cerrará el gestor. Cierra Windows Terminal y sigue las instrucciones del CMD.'))
            else:
                yield Static(tr('Primero se descargará y validará la actualización. Después se abrirá un CMD independiente y se cerrará el gestor. Cierra la ventana de Windows Terminal y sigue las instrucciones del CMD para continuar.'))
            yield Static(tr('Se conservarán configuración, profiles, componentes, home y workspaces. Si falla la sustitución se intentará restaurar el programa anterior.'))
            if not self.info['hash']:
                yield Static(tr('Esta release no declara hash. Al continuar autorizas su descarga e instalación sin verificación de integridad.'))
            with Horizontal():
                yield Button(tr('Cancelar'), id='self-update-cancel')
                yield Button(tr('Actualizar'), id='self-update-confirm', variant='success')

    def on_mount(self):
        self.query_one('#self-update-cancel', Button).focus()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'self-update-confirm')

    def action_cancel(self):
        self.dismiss(False)
