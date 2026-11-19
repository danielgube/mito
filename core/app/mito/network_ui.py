from .i18n import tr
from textual import work
from .localized_ui import ModalScreen
from textual.containers import Vertical
from textual.widgets import Button, Input, Static


def source_label(source):
    return {
        'config.properties': 'config.properties',
        'manual': tr('configuración manual de Windows'),
        'pac': tr('script PAC'),
        'wpad': tr('autodetección WPAD'),
    }.get(source, source)


class NetworkSetupDialog(ModalScreen):
    """Shows the detected route and the keys to save; credentials are never among them."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    NetworkSetupDialog { align: center middle; background: #00000088; }
    #network-setup { width: 80; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #network-setup-keys { margin: 1 0; color: #b5dc64; }
    #network-setup Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, diagnosis):
        super().__init__()
        self.diagnosis = diagnosis

    def compose(self):
        diagnosis = self.diagnosis
        route = tr('Proxy: {p0} ({p1})', p0=diagnosis.proxy, p1=source_label(diagnosis.source)) if diagnosis.proxy else tr('Conexión directa, sin proxy')
        state = tr('El proxy da acceso a Internet.') if diagnosis.kind == 'ok' else tr('La red redirige a un portal de autenticación.')
        with Vertical(id='network-setup'):
            yield Static(tr('CONFIGURACIÓN DE RED'))
            yield Static(route)
            yield Static(state)
            yield Static(tr('Se guardarán estas claves en config.properties:'))
            yield Static('\n'.join(f'{key}={value}' for key, value in diagnosis.changes.items()), id='network-setup-keys', markup=False)
            if diagnosis.kind == 'portal':
                yield Static(tr('Tu usuario y contraseña nunca se guardan: MITO los pedirá en cada sesión que el portal lo requiera.'))
            yield Button(tr('Guardar y conectar'), id='network-setup-save', variant='primary')
            yield Button(tr('Cancelar'), id='network-setup-cancel')

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'network-setup-save')


class ProxyDialog(ModalScreen):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    ProxyDialog { align: center middle; background: #00000088; }
    #proxy-dialog { width: 66; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #proxy-dialog Input { margin-top: 1; }
    #proxy-dialog Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, network):
        super().__init__()
        self.network = network
        self.credentials = None

    def compose(self):
        with Vertical(id='proxy-dialog'):
            yield Static(tr('AUTENTICACIÓN DE RED'))
            yield Static(tr('Las credenciales no se guardan. Escape cancela.'))
            yield Input(placeholder=tr('Usuario'), id='proxy-user')
            yield Input(placeholder=tr('Contraseña'), password=True, id='proxy-password')
            yield Static('', id='proxy-result')
            yield Button(tr('Conectar'), id='proxy-submit', variant='primary')
            yield Button(tr('Cancelar'), id='proxy-cancel')

    def action_cancel(self):
        self.query_one('#proxy-password', Input).value = ''
        self.credentials = None
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'proxy-cancel':
            self.action_cancel()
        elif event.button.id == 'proxy-submit':
            username = self.query_one('#proxy-user', Input).value
            password = self.query_one('#proxy-password', Input).value
            self.query_one('#proxy-password', Input).value = ''
            self.query_one('#proxy-submit', Button).disabled = True
            self.query_one('#proxy-result', Static).update(tr('Conectando…'))
            self.credentials = (username, password)
            self.authenticate()

    @work(thread=True, exit_on_error=False)
    def authenticate(self):
        app = self.app
        credentials, self.credentials = self.credentials, None
        if credentials is None:
            return
        username, password = credentials
        credentials = None
        try:
            success = self.network.authenticate(username, password)
        except Exception as exc:
            # Do not include username/password or the credential-bearing request.
            from .diagnostics import write
            write(self.network.root, 'error', 'network.authenticate', type(exc).__name__)
            success = False
        finally:
            username = password = None
        app.call_from_thread(self.completed, success)

    def completed(self, success):
        if not self.is_mounted:
            return
        if success:
            self.dismiss(True)
        else:
            self.query_one('#proxy-result', Static).update(tr('No se pudo verificar Internet. Puedes reintentar o cancelar.'))
            self.query_one('#proxy-submit', Button).disabled = False
