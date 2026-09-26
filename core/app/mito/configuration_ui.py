from .i18n import tr
from textual.containers import Horizontal, Vertical, VerticalScroll
from .localized_ui import ModalScreen
from textual.widgets import Button, Input, Select, Static

from .configuration import Configuration, PREFIXES, PROPERTIES
from .dialog_header import DialogHeader
from .storage import YitoError


class SettingRow(Horizontal):
    def __init__(self, key, value, profile=False):
        super().__init__(classes='setting-row')
        self.free = profile or key.startswith('env.')
        self.display_key = key if profile or not key.startswith('env.') else key[4:]
        self.key, self.value = key, value

    def compose(self):
        if self.free:
            yield Input(self.display_key, placeholder=tr('Variable'), classes='setting-key')
        else:
            yield Static(self.key, classes='setting-key', markup=False)
        choices = PROPERTIES.get(self.key, (None, ''))[0] if not self.free else None
        if choices:
            current = self.value.lower()
            options = [(tr('Automático (Windows)') if self.key == 'mito.language' and value == 'system' else value, value)
                       for value in choices]
            if current not in choices:
                options.append((tr('{p0} (valor no válido)', p0=(current)), current))
            yield Select(options, value=current, allow_blank=False, classes='setting-value')
        else:
            yield Input(self.value, placeholder=tr('Valor'), classes='setting-value')
        yield Button(tr('Eliminar'), classes='setting-remove', variant='error')

    def pair(self, profile):
        key = self.query_one('.setting-key', Input).value.strip() if self.free else self.key
        if self.free and not profile:
            key = 'env.' + key
        return key, str(self.query_one('.setting-value').value)


class ConfigurationDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cerrar')]
    DEFAULT_CSS = """
    ConfigurationDialog { align: center middle; background: #00000088; }
    #configuration-dialog { width: 95%; max-width: 136; height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #configuration-context { height: auto; margin-bottom: 1; }
    #configuration-rows { height: 1fr; min-height: 3; }
    .setting-row { height: 3; margin: 0; align-vertical: middle; }
    .setting-key { width: 1fr; height: 3; content-align: left middle; margin: 0 1 0 0; }
    .setting-value { width: 1fr; margin: 0 1 0 0; }
    .setting-remove { width: 10; min-width: 10; }
    #configuration-add-row { height: 3; margin-top: 1; }
    #configuration-kind { width: 1fr; margin: 0 1 0 0; }
    #configuration-name { width: 1fr; margin: 0 1 0 0; }
    #configuration-add { width: 10; min-width: 10; }
    #configuration-error { height: auto; color: #ff7070; margin-top: 1; }
    #configuration-save { width: 100%; margin-top: 1; }
    """

    def __init__(self, engine, profile=None):
        super().__init__()
        self.settings = Configuration(engine, profile)
        self.profile = profile
        if not profile:
            for key in ('mito.language', 'mcp.jobs.monitor', 'terminal.defaultShell'):
                self.settings.values.setdefault(key, PROPERTIES[key][1])

    def compose(self):
        with Vertical(id='configuration-dialog'):
            yield DialogHeader(tr('CONFIGURACIÓN DEL PROFILE') if self.profile else tr('CONFIGURACIÓN'), 'configuration-close')
            yield Static(tr('Profile: {p1} · Variables que sobrescriben las globales.', p1=(self.profile)) if self.profile else tr('config.properties · Variables comunes a todos los profiles.'), id='configuration-context', markup=False)
            with VerticalScroll(id='configuration-rows'):
                for key, value in self.settings.values.items():
                    yield SettingRow(key, value, bool(self.profile))
            with Horizontal(id='configuration-add-row'):
                options = [(tr('Variable de entorno'), 'env.')]
                if not self.profile:
                    options += [(key, key) for key in PROPERTIES]
                    options += [(prefix + tr('<nombre>'), prefix) for prefix in PREFIXES]
                yield Select(options, value='env.', allow_blank=False, id='configuration-kind', disabled=bool(self.profile))
                yield Input(placeholder=tr('Nombre de la variable'), id='configuration-name')
                yield Button(tr('Añadir'), id='configuration-add')
            yield Static('', id='configuration-error', markup=False)
            yield Button(tr('Guardar'), id='configuration-save', variant='primary')

    def action_cancel(self):
        self.dismiss(False)

    def on_select_changed(self, event):
        if event.select.id != 'configuration-kind':
            return
        entry = self.query_one('#configuration-name', Input)
        entry.disabled = event.value in PROPERTIES
        entry.placeholder = tr('Nombre de la variable') if event.value == 'env.' else tr('Nombre')

    async def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        error = self.query_one('#configuration-error', Static)
        error.update('')
        if action == 'configuration-close':
            self.action_cancel()
        elif event.button.has_class('setting-remove'):
            await event.button.parent.remove()
        elif action == 'configuration-add':
            kind = str(self.query_one('#configuration-kind', Select).value)
            name = self.query_one('#configuration-name', Input).value.strip()
            if kind not in PROPERTIES and not name:
                error.update(tr('Introduce el nombre de la propiedad o variable.'))
                return
            key = name if self.profile else kind if kind in PROPERTIES else kind + name
            rows = self.query(SettingRow)
            if any(row.pair(bool(self.profile))[0].casefold() == key.casefold() for row in rows):
                error.update(tr('Esta propiedad ya está añadida.'))
                return
            row = SettingRow(key, PROPERTIES.get(key, (None, ''))[1], bool(self.profile))
            await self.query_one('#configuration-rows').mount(row)
            row.scroll_visible(immediate=True)
            self.query_one('#configuration-name', Input).value = ''
        elif action == 'configuration-save':
            try:
                self.settings.save([row.pair(bool(self.profile)) for row in self.query(SettingRow)])
            except (YitoError, OSError, ValueError) as exc:
                from .error_tracking import capture
                capture(self.settings.engine.root, exc, operation='configuration.save')
                error.update(str(exc))
                return
            self.dismiss(True)
