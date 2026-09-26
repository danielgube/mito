from .i18n import tr
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from .localized_ui import ModalScreen
from textual.widgets import Button, Static

from .dialog_header import DialogHeader
from .pocketools import info, sources, update, is_live_source
from .storage import YitoError, inside


class PocketoolsDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    PocketoolsDialog { align: center middle; background: #00000088; }
    #pocketools-dialog { width: 90%; max-width: 120; height: 80%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #pocketools-list { height: 1fr; margin-top: 1; }
    .pocketool-row { height: auto; min-height: 5; padding: 1 0; border-top: solid #456451; align-vertical: middle; }
    .pocketool-description { width: 1fr; height: auto; margin-right: 2; }
    .pocketool-update { width: 12; min-width: 12; }
    .pocketool-manage { width: 14; min-width: 14; margin-left: 1; }
    #pocketools-status { height: auto; max-height: 6; margin-top: 1; }
    #pocketools-status.error { color: #ff7070; }
    """

    def __init__(self, engine, profile=None):
        super().__init__()
        self.engine = engine
        self.profile = profile
        self.configured = sources(engine.root)
        self.busy = False

    def description(self, name):
        path = inside(self.engine.root/'pocketools', name)
        try:
            description = info(path)
        except (YitoError, OSError):
            description = tr('Sin sincronizar o pocketool.json no válido')
        return f'{name}\n{description}\n{self.configured[name]}'

    def compose(self):
        with Vertical(id='pocketools-dialog'):
            yield DialogHeader(tr('POCKETOOLS'), 'pocketools-close')
            yield Button(tr('Crear paquete'), id='pocketools-create', variant='primary')
            with VerticalScroll(id='pocketools-list'):
                for index, name in enumerate(self.configured):
                    with Horizontal(classes='pocketool-row'):
                        yield Static(self.description(name), id=f'pocketool-info-{index}', classes='pocketool-description', markup=False)
                        if not is_live_source(self.engine.root, name, self.configured[name]):
                            yield Button(tr('Actualizar'), id=f'pocketool-update-{index}', classes='pocketool-update', name=name)
                        yield Button(tr('Comandos'), id=f'pocketool-manage-{index}', classes='pocketool-manage', name=name)
                if not self.configured:
                    yield Static(tr('Añade pocketool.<nombre> en Configuración para configurar un repositorio Git o una carpeta local.'))
            yield Static(tr('Compartidas por todos los profiles. Los comandos locales están disponibles al guardar.'), id='pocketools-status', markup=False)

    def action_close(self):
        if not self.busy:
            self.dismiss()

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'pocketools-close':
            self.action_close()
        elif event.button.id == 'pocketools-create' and not self.busy:
            from .pocketool_editor_ui import PackageDialog
            self.app.push_screen(PackageDialog(self.engine), self.package_created)
        elif event.button.has_class('pocketool-manage') and not self.busy:
            from .pocketool_editor_ui import PackageCommandsDialog
            self.app.push_screen(PackageCommandsDialog(self.engine, event.button.name, self.profile), self.reload_packages)
        elif event.button.has_class('pocketool-update') and not self.busy:
            self.busy = True
            self.app.busy = True
            for button in self.query(Button):
                button.disabled = True
            self.query_one('#pocketools-status', Static).remove_class('error')
            self.query_one('#pocketools-status', Static).update(tr('Sincronizando {p1}…', p1=(event.button.name)))
            self.synchronize(event.button.name)

    async def reload_packages(self, result=None):
        self.configured = sources(self.engine.root)
        await self.recompose()

    async def package_created(self, name):
        await self.reload_packages()
        if name:
            from .pocketool_editor_ui import PackageCommandsDialog
            self.app.push_screen(PackageCommandsDialog(self.engine, name, self.profile), self.reload_packages)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def synchronize(self, name):
        app = self.app
        error = None
        try:
            update(self.engine, name, log=lambda message: app.call_from_thread(app.write_log, message))
        except Exception as exc:
            from .error_tracking import capture
            capture(self.engine.root, exc, operation='pocketools.update', component=name)
            error = str(exc)
            app.call_from_thread(app.write_log, tr('Error: {p1}', p1=(error)), 'error')
        app.call_from_thread(self.finished, name, error)

    def finished(self, name, error):
        self.busy = False
        self.app.busy = False
        if not self.is_mounted:
            return
        for button in self.query(Button):
            button.disabled = False
        status = self.query_one('#pocketools-status', Static)
        status.set_class(bool(error), 'error')
        status.update(tr('Error: {p1}', p1=(error)) if error else tr('{p0} actualizada. Abre una nueva pestaña o aplicación para cargar su PATH.', p0=(name)))
        index = list(self.configured).index(name)
        self.query_one(f'#pocketool-info-{index}', Static).update(self.description(name))
