"""Lista de profiles del repositorio configurado; instalar es la importación normal."""
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Static

from .dialog_header import DialogHeader
from .i18n import tr
from .localized_ui import ModalScreen


class ProfileRepositoryDialog(ModalScreen):
    """Dismiss (ruta, nombre del nuevo profile) o None."""
    BINDINGS = [('escape', 'cancel', 'Cerrar')]
    DEFAULT_CSS = """
    ProfileRepositoryDialog { align: center middle; background: #00000088; }
    #profile-repository-dialog { width: 95%; max-width: 130; height: 85%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #profile-repository-dialog > Static { height: auto; margin-bottom: 1; }
    #profile-repository-rows { height: 1fr; }
    .repository-row { height: auto; min-height: 3; margin-bottom: 1; align-vertical: middle; }
    .repository-text { width: 1fr; height: auto; }
    .repository-name { width: 28; margin: 0 1; }
    .repository-install { width: 14; min-width: 14; }
    #profile-repository-error { height: auto; color: #ff7070; }
    #profile-repository-refresh { width: 100%; margin-top: 1; }
    """

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.items = []

    def compose(self):
        from .profile_repository import configured
        from .storage import YitoError
        try:
            source = configured(self.engine.root)
        except YitoError as exc:
            source = None
            self.initial_error = str(exc)
        else:
            self.initial_error = ''
        self.source = source
        with Vertical(id='profile-repository-dialog'):
            yield DialogHeader(tr('REPOSITORIO DE PROFILES'), 'profile-repository-close')
            yield Static(source or tr('Configura {p0} en Configuración para usar un repositorio de profiles.', p0='profiles.repository'),
                         id='profile-repository-source', markup=False)
            yield VerticalScroll(id='profile-repository-rows')
            yield Static(self.initial_error, id='profile-repository-error', markup=False)
            yield Button(tr('Sincronizar'), id='profile-repository-refresh', disabled=not source)

    def on_mount(self):
        if self.source:
            self.refresh_list()

    def refresh_list(self):
        self.query_one('#profile-repository-refresh', Button).disabled = True
        self.query_one('#profile-repository-error', Static).update(tr('Sincronizando el repositorio de profiles…'))
        app = self.app

        def fetch():
            from .profile_repository import sync
            try:
                items, error = sync(self.engine, log=lambda message: None), ''
            except Exception as exc:
                from .error_tracking import capture
                capture(self.engine.root, exc, operation='profile.repository')
                items, error = None, str(exc)
            app.call_from_thread(self.listed, items, error)
        self.run_worker(fetch, thread=True, exclusive=True, group='profile-repository', exit_on_error=False)

    async def listed(self, items, error):
        if not self.is_mounted:
            return
        self.query_one('#profile-repository-refresh', Button).disabled = False
        self.query_one('#profile-repository-error', Static).update(error)
        if items is None:
            return
        self.items = items
        container = self.query_one('#profile-repository-rows', VerticalScroll)
        await container.remove_children()
        rows = []
        for index, item in enumerate(items):
            if item['error']:
                detail = tr('No válido: {p1}', p1=item['error'])
            else:
                detail = tr('{p1} componentes', p1=len(item['apps']))
                if item['pinned']:
                    detail += ' · ' + tr('{p1} retenidas', p1=len(item['pinned']))
                if item['files']:
                    detail += ' · ' + tr('{p1} archivos de configuración', p1=item['files'])
                if item['apps']:
                    detail += '\n' + ', '.join(item['apps'])
            text = Static(f'{item["relative"]}\n{detail}', classes='repository-text', markup=False)
            name = Input(value=item['name'], placeholder=tr('Nombre del nuevo profile'),
                         id=f'repository-name-{index}', classes='repository-name')
            button = Button(tr('Instalar'), id=f'repository-install-{index}', classes='repository-install',
                            variant='success', disabled=bool(item['error']))
            rows.append(Horizontal(text, name, button, classes='repository-row'))
        if rows:
            await container.mount(*rows)
        else:
            await container.mount(Static(tr('El repositorio no contiene profiles exportados (*.mito.json, *.mito.zip).')))

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id or ''
        if action == 'profile-repository-close':
            self.action_cancel()
        elif action == 'profile-repository-refresh':
            self.refresh_list()
        elif action.startswith('repository-install-'):
            index = int(action.removeprefix('repository-install-'))
            name = self.query_one(f'#repository-name-{index}', Input).value.strip()
            if not name:
                self.query_one('#profile-repository-error', Static).update(tr('Indica el nombre del nuevo profile.'))
                return
            if name.lower() in self.engine.profiles():
                self.query_one('#profile-repository-error', Static).update(tr('Ya existe ese profile; elige otro nombre'))
                return
            self.dismiss((str(self.items[index]['path']), name))
