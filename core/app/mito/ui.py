from .i18n import tr
import json
import subprocess
import time
import urllib.parse
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from .localized_ui import ModalScreen
from textual.containers import Grid, Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, ContentSwitcher, DataTable, Footer, Header, Input, Label, RichLog, Select, Static, TextArea
from .storage import mutation, read_json, write_json
from .search import rank_entries
from . import __version__
from .dialog_header import DialogHeader
from .error_tracking import capture
from . import diagnostics


def license_label(manifest):
    license = manifest.get('license')
    if isinstance(license, dict):
        license = license.get('identifier') or license.get('url')
    return license.strip() if isinstance(license, str) and license.strip() else tr('No declarada')


class ManifestDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    ManifestDialog { align: center middle; background: $background 70%; }
    #manifest-dialog { width: 90%; height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #manifest-component { height: auto; margin-bottom: 1; }
    #manifest-content { height: 1fr; }
    """

    def __init__(self, key, manifest):
        super().__init__()
        self.component_key = key
        self.content = json.dumps(manifest, ensure_ascii=False, indent=4)

    def compose(self):
        with Vertical(id='manifest-dialog'):
            yield DialogHeader(tr('Manifiesto'), 'manifest-close')
            yield Static(self.component_key, id='manifest-component', markup=False)
            yield TextArea(self.content, read_only=True, soft_wrap=False, id='manifest-content')

    def on_mount(self):
        self.query_one('#manifest-content', TextArea).focus()

    def action_close(self):
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'manifest-close':
            self.action_close()


class InstallVersionDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    InstallVersionDialog { align: center middle; background: $background 70%; }
    #install-version-dialog { width: 68; height: auto; max-height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #install-version-dialog Static { height: auto; margin-bottom: 1; }
    #install-version-dialog Horizontal { height: auto; align-horizontal: right; }
    #install-version-dialog Button { margin-left: 1; }
    """

    def __init__(self, key, previous, version):
        super().__init__()
        self.key, self.previous, self.version = key, previous, version

    def compose(self):
        with VerticalScroll(id='install-version-dialog'):
            yield Label(tr('Nueva versión disponible'), classes='title')
            yield Static(f'{self.key}\n{self.previous} → {self.version}', markup=False)
            with Horizontal():
                yield Button(tr('Cancelar'), id='install-version-cancel')
                yield Button(tr('Instalar'), id='install-version-confirm', variant='success')

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == 'install-version-confirm')


class VersionsDialog(ModalScreen[object]):
    """Elegir una versión concreta de bucket/app: histórico Git o autoupdate."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    VersionsDialog { align: center middle; background: $background 70%; }
    #versions-dialog { width: 76; height: auto; max-height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #versions-dialog Static { height: auto; margin-bottom: 1; }
    #versions-dialog Select, #versions-dialog Input, #versions-more { margin-bottom: 1; }
    #versions-actions { height: auto; align-horizontal: right; }
    #versions-actions Button { margin-left: 1; }
    """

    def __init__(self, key, current, installed, found, autoupdate, history_available, loader=None):
        from .versions import HISTORY_BATCH, history_versions
        super().__init__()
        self.key, self.current, self.installed = key, current, installed
        self.found, self.loader, self.limit = found, loader, HISTORY_BATCH
        self.versions = history_versions(found, self.limit)
        self.autoupdate, self.history_available = autoupdate, history_available

    def version_options(self):
        return [(f'{item["version"]} · {item["date"]}', item['version']) for item in self.versions]

    def more_available(self):
        from .versions import has_more, history_versions
        return self.loader is not None and (has_more(self.found) or len(history_versions(self.found)) > len(self.versions))

    def more_label(self):
        from .versions import HISTORY_BATCH
        return tr('Cargar {p1} más', p1=HISTORY_BATCH) if self.more_available() else tr('Sin más versiones')

    def compose(self):
        with VerticalScroll(id='versions-dialog'):
            yield DialogHeader(tr('VERSIONES DEL COMPONENTE'), 'versions-close')
            yield Static(f'{self.key}\n' + tr('Catálogo: {p1} · Instalada: {p3}', p1=self.current, p3=self.installed or '—'), markup=False)
            if self.versions:
                yield Static(tr('Versiones publicadas en el histórico Git del bucket, con el manifiesto y el hash de entonces.'))
                yield Select(self.version_options(), prompt=tr('Selecciona o escribe una versión'), id='versions-list')
                yield Button(self.more_label(), id='versions-more', disabled=not self.more_available())
            else:
                yield Static(tr('No hay versiones en el histórico Git del bucket (sólo buckets de GitHub).'))
            if self.autoupdate:
                yield Static(tr('Otra versión se genera desde autoupdate, como scoop install app@versión. Si no se obtiene su hash, se pedirá confirmación.'))
                yield Input(placeholder=tr('Otra versión, por ejemplo 1.2.3'), id='versions-input')
            yield Static(tr('La versión elegida sustituye a la instalada y queda retenida: Actualizar no la cambiará hasta liberarla en Info.'))
            with Horizontal(id='versions-actions'):
                yield Button(tr('Cancelar'), id='versions-cancel')
                yield Button(tr('Instalar'), id='versions-confirm', variant='success')

    def action_cancel(self):
        self.dismiss(None)

    def load_more(self):
        from .versions import HISTORY_BATCH
        button = self.query_one('#versions-more', Button)
        button.disabled = True
        button.label = tr('Cargando…')
        wanted = self.limit + HISTORY_BATCH

        def fetch():
            error = None
            try:
                self.loader(self.found, wanted)
            except Exception as exc:
                error = exc
            self.app.call_from_thread(self.show_more, wanted, error)
        self.run_worker(fetch, thread=True, group='versions-more', exit_on_error=False)

    def show_more(self, wanted, error):
        from .versions import history_versions
        if error is not None:
            self.notify(tr('No se pudo consultar el histórico del bucket: {p1}', p1=error), severity='error')
        else:
            self.limit = wanted
            self.versions = history_versions(self.found, self.limit)
            select = self.query_one('#versions-list', Select)
            selected = select.value
            select.set_options(self.version_options())
            if isinstance(selected, str) and any(item['version'] == selected for item in self.versions):
                select.value = selected
        button = self.query_one('#versions-more', Button)
        button.label = self.more_label()
        button.disabled = not self.more_available()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'versions-more':
            self.load_more()
            return
        if event.button.id != 'versions-confirm':
            self.dismiss(None)
            return
        version = ''
        if self.query('#versions-input'):
            version = self.query_one('#versions-input', Input).value.strip()
        if not version and self.query('#versions-list'):
            selected = self.query_one('#versions-list', Select).value
            version = selected if isinstance(selected, str) else ''
        if not version:
            self.notify(tr('Selecciona o escribe una versión'), severity='warning')
            return
        self.dismiss(version)


class OpenApplicationDialog(ModalScreen[object]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    OpenApplicationDialog { align: center middle; background: $background 70%; }
    #open-application-dialog { width: 62; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #open-application-dialog Static { height: auto; margin-bottom: 1; }
    #open-application-dialog Horizontal { height: auto; align-horizontal: right; }
    #open-application-dialog Button { margin-left: 1; }
    """

    def __init__(self, name, entries=()):
        super().__init__()
        self.application_name = name
        self.entries = entries

    def compose(self):
        with Vertical(id='open-application-dialog'):
            yield Label(tr('Abrir'), classes='title')
            yield Static(self.application_name, markup=False)
            if self.entries:
                yield Select([(label, label) for label, _, _ in self.entries],
                             value=self.entries[0][0], allow_blank=False, id='application-entry')
            yield Label(tr('Argumentos'))
            yield Input(id='application-arguments')
            yield Label(tr('Proceso'))
            yield Select([('detached', False), ('attached', True)],
                         value=False, allow_blank=False, id='application-process')
            with Horizontal():
                yield Button(tr('Cancelar'), id='application-open-cancel')
                yield Button(tr('Abrir'), id='application-open-independent', variant='primary')

    def on_mount(self):
        self.query_one('#application-arguments', Input).focus()

    def action_cancel(self):
        self.dismiss(None)

    def selected(self):
        from .arguments import split
        result = (bool(self.query_one('#application-process', Select).value),
                  split(self.query_one('#application-arguments', Input).value))
        if self.entries:
            result += (self.query_one('#application-entry', Select).value,)
        self.dismiss(result)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.selected()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'application-open-independent':
            self.selected()
        else:
            self.dismiss(None)


class ShortcutDialog(ModalScreen[object]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    ShortcutDialog { align: center middle; background: $background 70%; }
    #shortcut-dialog { width: 54; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #shortcut-dialog Horizontal { height: auto; align-horizontal: right; }
    #shortcut-dialog Button { margin-left: 1; }
    #shortcut-colors { width: 100%; height: 7; grid-size: 4 2; grid-columns: 1fr 1fr 1fr 1fr; grid-rows: 3 3; grid-gutter: 1 0; }
    #shortcut-colors Button { width: 100%; height: 3; margin: 0; }
    #badge-error { height: auto; color: #ff9090; }
    """

    def compose(self):
        with Vertical(id='shortcut-dialog'):
            yield Label(tr('Generar acceso directo'), classes='title')
            if self.entries:
                yield Select([(label, label) for label, _, _ in self.entries],
                             value=self.entries[0][0], allow_blank=False, id='shortcut-entry')
            if self.application:
                yield Label(tr('Argumentos'))
                yield Input(id='shortcut-arguments')
            if self.application and not self.pocketool:
                yield Label(tr('Proceso'))
                yield Select([('detached', False), ('attached', True)],
                    value=False, allow_blank=False, id='shortcut-coupled')
            yield Label(tr('Letras del icono (máximo 4)'))
            yield Input(value=self.default_badge, max_length=4, id='shortcut-badge')
            yield Label(tr('Color del fondo'))
            from .icons import BADGE_COLORS
            with Grid(id='shortcut-colors'):
                for name in BADGE_COLORS:
                    yield Button(name.capitalize(), id=f'shortcut-color-{name}')
            yield Static('', id='badge-error')
            with Horizontal():
                yield Button(tr('Cancelar'), id='shortcut-cancel')
                yield Button(tr('Generar'), id='shortcut-confirm', variant='primary')

    def on_mount(self):
        from .icons import BADGE_COLORS
        for name, rgb in BADGE_COLORS.items():
            button = self.query_one(f'#shortcut-color-{name}', Button)
            button.styles.background = '#%02x%02x%02x' % rgb
            button.styles.color = 'white'
        self.show_selected_color()
        self.query_one('#shortcut-badge', Input).focus()

    def __init__(self, application=False, entries=(), engine=None, pocketool=False):
        super().__init__()
        self.application = application
        self.pocketool = pocketool
        self.entries = entries
        from .shortcuts import badge_text, badge_color
        self.default_badge = badge_text(engine)
        self.default_color = badge_color(engine)
        self.selected_color = self.default_color

    def show_selected_color(self):
        from .icons import BADGE_COLORS
        for name in BADGE_COLORS:
            button = self.query_one(f'#shortcut-color-{name}', Button)
            button.label = ('✓ ' if name == self.selected_color else '') + name.capitalize()
            button.styles.border = ('heavy', 'white') if name == self.selected_color else ('none', 'transparent')

    def action_cancel(self):
        self.dismiss(None)

    def confirm(self):
        from .shortcuts import badge_text, badge_color
        from .storage import YitoError
        try:
            value = badge_text(None, self.query_one('#shortcut-badge', Input).value)
            color = badge_color(None, self.selected_color)
        except YitoError as exc:
            self.query_one('#badge-error', Static).update(str(exc))
            return
        if self.application:
            from .arguments import split
            result = (value, color, False if self.pocketool else bool(self.query_one('#shortcut-coupled', Select).value),
                split(self.query_one('#shortcut-arguments', Input).value))
            if self.entries:
                result += (self.query_one('#shortcut-entry', Select).value,)
        else:
            result = (value, color)
        self.dismiss(result)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        if self.application and event.input.id == 'shortcut-badge':
            self.query_one('#shortcut-arguments', Input).focus()
        else:
            self.confirm()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id and event.button.id.startswith('shortcut-color-'):
            self.selected_color = event.button.id.removeprefix('shortcut-color-')
            self.show_selected_color()
        elif event.button.id == 'shortcut-confirm':
            self.confirm()
        else:
            self.action_cancel()


class RemoveComponentDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    RemoveComponentDialog { align: center middle; background: $background 70%; }
    #remove-dialog { width: 68; height: auto; max-height: 90%; padding: 1 2; border: round #e27878; background: #153429; }
    #remove-dialog Horizontal { height: auto; align-horizontal: right; margin-top: 1; }
    #remove-dialog Button { margin-left: 1; }
    """

    def __init__(self, lock, profiles):
        super().__init__()
        self.lock = lock
        self.profiles = profiles

    def compose(self):
        with VerticalScroll(id='remove-dialog'):
            yield Label(tr('Eliminar componente'), classes='title')
            yield Static(f'{self.lock["key"]} · {self.lock["version"]}', markup=False)
            yield Static(tr('Se retirará de estos profiles: ') + (', '.join(self.profiles) or tr('ninguno')), markup=False)
            yield Static(tr('Se eliminarán los archivos de este componente. Se conservan el home, los datos persistentes y los workspaces. Las aplicaciones dependientes y sus accesos pueden dejar de funcionar. Cierra el componente antes de eliminarlo.'))
            with Horizontal():
                yield Button(tr('Cancelar'), id='remove-cancel')
                yield Button(tr('Eliminar'), id='remove-confirm', variant='error')

    def on_mount(self):
        self.query_one('#remove-cancel',Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == 'remove-confirm')


class ComponentInfoDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    ComponentInfoDialog { align: center middle; background: $background 70%; }
    #component-info { width: 90%; max-width: 110; height: 85%; padding: 1 0 1 2; border: round #b5dc64; background: #153429; }
    #component-info > DialogHeader { margin-right: 2; }
    #info-content { height: 1fr; padding: 0; }
    #info-content-body { height: auto; padding-right: 3; }
    #info-content Static { height: auto; }
    .info-group { height: auto; border-top: solid #52765a; margin-top: 1; padding-top: 1; }
    .info-group-title { color: #b5dc64; text-style: bold; margin-bottom: 1; }
    .info-subtitle { color: #b5dc64; text-style: bold; margin: 1 0; }
    .info-path { height: auto; min-height: 3; margin-bottom: 1; align-vertical: middle; }
    .info-path-text { width: 1fr; height: auto; }
    .info-path-label { color: #b5dc64; text-style: bold; }
    .info-path Button { width: 10; min-width: 10; margin-left: 2; }
    #info-description { height: auto; margin: 1 0; }
    """

    def __init__(self, root, lock, profile):
        super().__init__()
        self.root = root
        from .persistence import locations
        from .isolation import isolated, payload_path
        from .catalog import effective
        from .storage import inside
        self.lock = lock
        self.manifest = effective(lock['manifest'])
        separate = isolated(lock)
        self.scope = tr('Profile: {p1}', p1=(profile)) if separate and profile else tr('Selecciona un profile para ver sus datos') if separate else tr('Compartido entre profiles')
        self.paths = [(tr('Binarios base'),inside(root,lock['path']))]
        if separate and profile:
            self.paths.append((tr('Binarios del profile'),payload_path(root,lock,profile)))
        if not separate or profile:
            self.paths += [(tr('Persistencia: {p1}', p1=(source)),path) for source,path in locations(root,lock,profile)]
        self.environment = None
        if not separate or profile:
            from .engine import Engine
            self.environment = Engine(root).component_environment(lock, profile)
        self.environment_paths = []

    def environment_row(self, label, value, path_entry=False):
        from pathlib import Path
        # Values are literal data: never expand host variables or execute files.
        candidate = Path(value.strip('"')) if value else None
        is_path = candidate is not None and candidate.is_absolute() and ';' not in value
        with Horizontal(classes='info-path'):
            status = ''
            if is_path or path_entry:
                status = tr('\nArchivo: se abrirá su carpeta') if candidate and candidate.is_file() else '' if candidate and candidate.is_dir() else tr('\nTodavía no creada')
            with Vertical(classes='info-path-text'):
                if label:
                    yield Static(label, classes='info-path-label', markup=False)
                yield Static(f'{value}{status}', markup=False)
            if is_path or path_entry:
                index = len(self.environment_paths)
                self.environment_paths.append(candidate)
                yield Button(tr('Abrir'), id=f'info-env-open-{index}',
                             disabled=candidate is None or not candidate.exists())

    def compose(self):
        self.environment_paths.clear()
        with Vertical(id='component-info'):
            yield DialogHeader(tr('INFORMACIÓN DEL COMPONENTE'), 'info-close')
            with VerticalScroll(id='info-content'):
                with Vertical(id='info-content-body'):
                    yield Static(f'{self.lock["key"]} · {self.lock["version"]}\n{self.scope}', markup=False)
                    yield Static(self.manifest.get('description') or tr('Sin descripción.'), id='info-description', markup=False)
                    pinned = self.lock.get('pinned')
                    if pinned:
                        # Origen técnico sin traducir: git (con commit), autoupdate o catalog.
                        source = str(pinned.get('source', '?')) + (f' {pinned["commit"][:10]}' if pinned.get('commit') else '')
                        with Vertical(classes='info-group'):
                            yield Static(tr('VERSIÓN RETENIDA'), classes='info-group-title')
                            with Horizontal(classes='info-path'):
                                with Vertical(classes='info-path-text'):
                                    yield Static(tr('{p1} elegida desde {p3}. Actualizar no la sustituirá.', p1=self.lock['version'], p3=source), markup=False)
                                yield Button(tr('Liberar'), id='info-release-version')
                    with Vertical(classes='info-group'):
                        yield Static(tr('RUTAS DEL COMPONENTE'), classes='info-group-title')
                        for index, (label, path) in enumerate(self.paths):
                            with Horizontal(classes='info-path'):
                                status = '' if path.is_dir() else tr('\nArchivo: se abrirá su carpeta') if path.is_file() else tr('\nTodavía no creada')
                                with Vertical(classes='info-path-text'):
                                    yield Static(label, classes='info-path-label', markup=False)
                                    yield Static(f'{path}{status}', markup=False)
                                yield Button(tr('Abrir'), id=f'info-open-{index}', disabled=not path.exists())
                        if not self.manifest.get('persist'):
                            yield Static(tr('Este componente no declara persistencia.'))
                    with Vertical(classes='info-group'):
                        yield Static(tr('APORTACIÓN AL ENTORNO'), classes='info-group-title')
                        yield Static(tr('Se aplica al activar el componente. Otras variables del entorno pueden sobrescribir estos valores.'))
                        if self.environment is None:
                            yield Static(tr('Selecciona un profile para ver sus datos'))
                        else:
                            variables, paths = self.environment
                            yield Static(tr('Variables de entorno'), classes='info-subtitle')
                            if not variables:
                                yield Static('—')
                            for name, value in variables.items():
                                yield from self.environment_row(name, value)
                            yield Static('path', classes='info-subtitle', markup=False)
                            if not paths:
                                yield Static('—')
                            for value in paths:
                                yield from self.environment_row('', value, path_entry=True)
                    notes = self.manifest.get('notes', [])
                    if notes:
                        with Vertical(classes='info-group'):
                            yield Static(tr('NOTAS\n').strip(), classes='info-group-title')
                            yield Static('\n'.join(map(str,notes)) if isinstance(notes,list) else str(notes), markup=False)
                    if self.manifest.get('pre_install'):
                        yield Static(tr('pre_install: se ejecuta con PowerShell durante la instalación.'))
                    if isinstance(self.manifest.get('installer'), dict) and self.manifest['installer'].get('script'):
                        yield Static(tr('{hook}: se ejecuta con PowerShell durante la instalación.', hook='installer.script'))
                    if isinstance(self.manifest.get('installer'), dict) and self.manifest['installer'].get('file'):
                        yield Static(tr('{hook}: se ejecuta con PowerShell durante la instalación.', hook='installer.file'))
                    for hook in ('post_install',):
                        if self.manifest.get(hook):
                            yield Static(tr('Paso omitido: MITO no ejecuta {p1}.', p1=(hook)))

    def action_close(self):
        self.dismiss()

    def on_button_pressed(self,event: Button.Pressed):
        event.stop()
        if event.button.id == 'info-close':
            self.dismiss()
        elif event.button.id == 'info-release-version':
            self.dismiss('release-version')
        elif event.button.id.startswith(('info-open-', 'info-env-open-')):
            from .persistence import open_location
            from .storage import YitoError
            if event.button.id.startswith('info-env-open-'):
                path = self.environment_paths[int(event.button.id.removeprefix('info-env-open-'))]
            else:
                path = self.paths[int(event.button.id.removeprefix('info-open-'))][1]
            try:
                open_location(path)
            except (OSError,YitoError) as exc:
                capture(self.root, exc, operation='component.open-location')
                self.notify(str(exc), severity='error')


class UnverifiedDownloadsDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    UnverifiedDownloadsDialog { align: center middle; background: #00000088; }
    #unverified-dialog { width: 78; height: auto; max-height: 85%; padding: 1 2; border: round #e5ba65; background: #153429; }
    #unverified-dialog Static { height: auto; margin-bottom: 1; }
    #unverified-dialog Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, downloads):
        super().__init__()
        self.downloads = downloads

    def compose(self):
        with VerticalScroll(id='unverified-dialog'):
            yield Static(tr('DESCARGAS SIN HASH'))
            yield Static(tr('No se podrá comprobar la integridad de estas descargas. ¿Quieres continuar?'))
            yield Static('\n'.join(self.downloads), markup=False)
            yield Button(tr('Cancelar'), id='unverified-cancel')
            yield Button(tr('Descargar e instalar'), id='unverified-confirm', variant='warning')

    def on_mount(self):
        self.query_one('#unverified-cancel', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'unverified-confirm')


class DownloadIntegrityDialog(ModalScreen):
    """Centered explanation when the network replaced a download; nothing is installed."""
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    DownloadIntegrityDialog { align: center middle; background: #00000088; }
    #integrity-dialog { width: 90; max-width: 95%; height: auto; max-height: 85%; padding: 1 2; border: round #e27878; background: #153429; }
    #integrity-dialog Static { height: auto; margin-bottom: 1; }
    #integrity-title { color: #e27878; text-style: bold; }
    #integrity-details { height: 4; margin-bottom: 1; }
    #integrity-dialog Button { width: 100%; }
    """

    def __init__(self, error):
        super().__init__()
        self.error = error

    def compose(self):
        size = '?' if self.error.size is None else self.error.size
        with VerticalScroll(id='integrity-dialog'):
            yield Static(tr('DESCARGA ALTERADA POR LA RED'), id='integrity-title')
            yield Static(tr('Lo que ha llegado no es el archivo publicado, así que MITO lo ha descartado y no ha instalado nada.'))
            if getattr(self.error, 'confirm', False):
                yield Static(tr('Causa más probable: el proxy de la red pide confirmar el acceso a {p1}. Ábrelo en el navegador, acepta el aviso y vuelve a intentarlo.',
                                p1=self.error.host or '?'), markup=False)
            elif self.error.portal:
                yield Static(tr('Causa más probable: el portal de autenticación de la red. Pulsa INTERNET, conéctate y vuelve a intentarlo.'))
            else:
                yield Static(tr('Causa más probable: el proxy o el antivirus de la red ha sustituido o modificado la descarga. No es un fallo de MITO ni del catálogo. Prueba desde otra red o pasa estos datos a soporte.'))
            # Read-only text so the URL can be selected and copied for support.
            yield TextArea(tr('URL: {p1}\nRecibido: {p2} bytes desde {p3}', p1=self.error.url or '?', p2=size, p3=self.error.host or '?'),
                           read_only=True, soft_wrap=True, id='integrity-details')
            if self.browse:
                yield Button(tr('Abrir en el navegador'), id='integrity-browse', variant='primary')
            yield Button(tr('Cerrar'), id='integrity-close', variant='default' if self.browse else 'primary')

    @property
    def browse(self):
        # Only an http(s) origin built by transfer.web_page_error, never a link from the page.
        url = getattr(self.error, 'browse', '') if getattr(self.error, 'confirm', False) else ''
        return url if urllib.parse.urlsplit(url).scheme in ('http', 'https') else ''

    def on_mount(self):
        self.query_one('#integrity-browse' if self.browse else '#integrity-close', Button).focus()

    def action_close(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'integrity-browse':
            # The user accepts the notice there; MITO never submits the proxy form itself.
            import webbrowser
            webbrowser.open(self.browse)
        self.dismiss(None)


class LongStepDialog(ModalScreen):
    """Centered notice for an engine step without progress; closes itself."""
    DEFAULT_CSS = """
    LongStepDialog { align: center middle; background: #00000088; }
    #long-step-dialog { width: 72; max-width: 95%; height: auto; padding: 1 2; border: round #e5ba65; background: #153429; }
    #long-step-dialog Static { height: auto; margin-bottom: 1; }
    #long-step-title { color: #e5ba65; text-style: bold; }
    #long-step-elapsed { color: #9fb59a; margin-bottom: 0; }
    """

    def __init__(self, message):
        super().__init__()
        self.message = message
        self.elapsed = 0
        self.finished = False

    def compose(self):
        with Vertical(id='long-step-dialog'):
            yield Static(tr('PREPARANDO ARCHIVOS'), id='long-step-title')
            yield Static(self.message, markup=False)
            yield Static('', id='long-step-elapsed')

    def on_mount(self):
        self.tick()
        self.set_interval(1, self.tick)

    def tick(self):
        if self.finished:
            self.finish()
            return
        self.query_one('#long-step-elapsed', Static).update(tr('Tiempo transcurrido: {p1} s', p1=self.elapsed))
        self.elapsed += 1

    def finish(self):
        # Another dialog may sit on top; the next tick closes this one then.
        self.finished = True
        if self.is_attached and self is self.app.screen:
            self.dismiss(None)


def human_size(value):
    size = float(value or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


class OperationDialog(ModalScreen):
    """Progress of the running installation, with cooperative cancellation.

    It reads the Operation state the engine updates from its worker thread and
    closes itself when the operation ends. Cancelling is offered until publishing."""
    DEFAULT_CSS = """
    OperationDialog { align: center middle; background: #00000088; }
    #operation-dialog { width: 76; max-width: 95%; height: auto; padding: 1 2; border: round #e5ba65; background: #153429; }
    #operation-dialog Static { height: auto; margin-bottom: 1; }
    #operation-title { color: #e5ba65; text-style: bold; }
    #operation-bar { width: 100%; margin-bottom: 1; }
    #operation-note { color: #e5ba65; }
    #operation-elapsed { color: #9fb59a; }
    #operation-cancel { width: 100%; }
    """

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.finished = False
        self.started = time.monotonic()
        self.phase = None
        self.samples = []

    def compose(self):
        from textual.widgets import ProgressBar
        with Vertical(id='operation-dialog'):
            yield Static('', id='operation-title')
            yield Static('', id='operation-text', markup=False)
            yield ProgressBar(total=None, show_eta=False, id='operation-bar')
            yield Static('', id='operation-detail')
            yield Static('', id='operation-note', markup=False)
            yield Static('', id='operation-elapsed')
            yield Button(tr('Cancelar instalación'), id='operation-cancel', variant='error')

    def on_mount(self):
        self.tick()
        self.set_interval(.25, self.tick)

    def detail(self, state):
        received, total = state.get('received'), state.get('total')
        if received is None:
            return ''
        if state.get('unit') == 'files':
            return tr('{p1}/{p2} archivos', p1=received, p2=total if total is not None else '?')
        now = time.monotonic()
        self.samples = [(t, r) for t, r in self.samples if now - t <= 5 and r <= received] + [(now, received)]
        first_time, first_received = self.samples[0]
        speed = (received - first_received) / (now - first_time) if now - first_time >= 1 else None
        text = (tr('{p1} de {p2}', p1=human_size(received), p2=human_size(total)) if total
                else tr('{p1} recibidos', p1=human_size(received)))
        if speed is not None:
            text += f' · {human_size(speed)}/s'
        return text

    def tick(self):
        from textual.widgets import ProgressBar
        state = self.operation.state
        if self.finished or not state.get('active'):
            self.finish()
            return
        if state.get('started') != self.phase:
            self.phase, self.samples = state.get('started'), []
        self.query_one('#operation-title', Static).update(state.get('title') or tr('INSTALANDO'))
        self.query_one('#operation-text', Static).update(state.get('text') or '')
        bar = self.query_one('#operation-bar', ProgressBar)
        received, total = state.get('received'), state.get('total')
        if total:
            bar.update(total=total, progress=min(received or 0, total))
        else:
            bar.update(total=None)
        self.query_one('#operation-detail', Static).update(self.detail(state))
        self.query_one('#operation-note', Static).update(state.get('note') or '')
        self.query_one('#operation-elapsed', Static).update(
            tr('Tiempo transcurrido: {p1} s', p1=int(time.monotonic() - self.started)))
        button = self.query_one('#operation-cancel', Button)
        if self.operation.cancelled:
            button.label, button.disabled = tr('Cancelando…'), True
        else:
            button.disabled = not state.get('cancellable')

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'operation-cancel' and self.operation.state.get('cancellable'):
            self.operation.cancel()
            self.tick()

    def finish(self):
        # Another dialog may sit on top; the next tick closes this one then.
        self.finished = True
        if self.is_attached and self is self.app.screen:
            self.dismiss(None)


class YitoApp(App):
    TITLE = f'MITO · {__version__}'
    SUB_TITLE = 'My Integrated Terminal Orchestrator'
    BINDINGS = [('ctrl+q', 'quit', 'Salir'), ('ctrl+f', 'search', 'Buscar componentes'), ('escape', 'home', 'Inicio')]
    CSS = '''
    Screen { background: #102820; color: #e7efdf; }
    Header { background: #215139; }
    #body { height: 1fr; }
    #sidebar { width: 34; padding: 1 2; background: #153429; }
    ContentSwitcher { width: 1fr; }
    .page { padding: 1 2; }
    .title { color: #b5dc64; text-style: bold; margin-bottom: 1; }
    #actions-title { margin-top: 2; }
    #sidebar Button { width: 100%; margin-top: 1; }
    Input, Select { margin-bottom: 1; }
    DataTable { height: 1fr; }
    #details-scroll { height: 9; }
    #bucket-filters { height: auto; min-height: 1; margin-bottom: 1; }
    #bucket-filters Checkbox { width: auto; height: 1; min-height: 1; border: none; padding: 0 1; margin-right: 1; }
    Checkbox > .toggle--button { color: #8A4B50; }
    Checkbox.-on > .toggle--button { color: #69FF64; }
    #details { height: auto; padding: 1; background: #1b3b2e; }
    #catalog-actions { height: 4; }
    #catalog-actions Button { width: 1fr; margin-right: 1; }
    #log-panel { height: 8; border-top: solid #264858; }
    #log { width: 1fr; height: 1fr; }
    #copy-log { width: 14; min-width: 14; margin: 1 1; }
    .summary { height: 3; color: #95a9bd; }
    #summary { height: auto; min-height: 3; }
    #installed-list { height: 1fr; }
    .component { height: 1; margin-bottom: 1; padding: 0 1; align-vertical: middle; }
    .component-row-dark { background: #1b3b2e; }
    .component-row-light { background: #29513d; }
    .component Checkbox { width: 1fr; height: 1; min-height: 1; content-align: left middle; border: none; background: transparent; }
    .component Button { width: 10; min-width: 10; height: 1; min-height: 1; padding: 0 1; border: none; }
    .component .component-info { width: 10; margin-left: 1; }
    .component .component-update { width: 14; margin-left: 1; }
    .component .component-remove { margin-left: 1; }
    .component .app-shortcut { width: 16; min-width: 16; margin-left: 1; }
    #home-actions { height: 3; margin-top: 1; }
    #home-actions Button { width: 1fr; }
    #browse { margin-right: 1; }
    #bulk-update { margin-left: 1; }
    #busy-indicator { height: 3; padding: 1; color: #FFD166; text-style: bold; }
    .host-component { overflow-x: auto; }
    .host-component.component-row-dark { background: #593300; }
    .host-component.component-row-light { background: #8f5000; }
    .component .host-data-button { width: 12; min-width: 12; margin-left: 1; }
    .component .open-with-button { width: 20; min-width: 20; margin-left: 1; }
    .component .protocol-button { width: 14; min-width: 14; margin-left: 1; }
    .host-app-name { width: 1fr; height: 1; padding-left: 5; content-align: left middle; color: #FFF; }
    '''

    def __init__(self, engine, profile=None):
        super().__init__()
        from .i18n import Translator
        from .localized_ui import translate_bindings
        self.translator = Translator.from_root(engine.root)
        translate_bindings(self, self.translator)
        self.engine = engine
        self.initial_profile = profile
        self.entries = []
        self.catalog_installed = set()
        self.filter_path = engine.root / 'core/state/catalog-filters.json'
        self.disabled_buckets = set(read_json(self.filter_path)) if self.filter_path.exists() else set()
        self.selected = None
        self.busy = False
        self.busy_sequence = 0
        self.mito_update = None
        self.rebuilding = False
        self.inventory = {}
        self.row_ids = {}
        self.log_messages = []
        from .mcp_service import MCPService
        self.mcp = MCPService(engine)
        self.mcp_timer = None
        self.mcp_quit = False
        self.mcp_confirmations = []
        self.mcp_confirming = False
        self.mcp_dirty = False
        import threading
        from .download_queue import DownloadQueue
        self.main_thread = threading.get_ident()
        self.downloads = DownloadQueue(engine, log=self.queue_log, confirm_unverified=self.queue_confirm,
                                       changed=self.queue_changed, failed=self.queue_failed)
        self.downloads_dirty = True
        self.downloads_done = set()

    # Background downloads: the queue thread reports here; the UI repaints every second.
    def queue_log(self, message, level='info'):
        import threading
        try:
            if threading.get_ident() == self.main_thread:
                self.write_log(message, level)
            else:
                self.call_from_thread(self.write_log, message, level)
        except RuntimeError:
            pass  # The manager is closing: the queue pauses with it.

    def queue_confirm(self, downloads):
        return self.call_from_thread(self.confirm_unverified, downloads)

    def queue_changed(self):
        self.downloads_dirty = True

    def queue_failed(self, exc):
        # Only the proxy confirmation needs a step from the user beyond reading the log.
        from .transfer import DownloadIntegrityError
        if isinstance(exc, DownloadIntegrityError) and exc.confirm:
            import threading
            try:
                if threading.get_ident() == self.main_thread:
                    self.push_screen(DownloadIntegrityDialog(exc))
                else:
                    self.call_from_thread(self.push_screen, DownloadIntegrityDialog(exc))
            except RuntimeError:
                pass  # The manager is closing.

    def refresh_downloads(self):
        if not self.downloads_dirty or not self.is_mounted or not self.screen_stack:
            return
        from textual.css.query import NoMatches
        try:
            # The sidebar lives on the base screen, also while a dialog is open on top.
            button = self.screen_stack[0].query_one('#downloads-button', Button)
        except NoMatches:
            return
        self.downloads_dirty = False
        items = self.downloads.snapshot()
        active = [item for item in items if item['status'] in ('pending', 'downloading')]
        ready = [item for item in items if item['status'] == 'done']
        running = next((item for item in active if item['status'] == 'downloading'), None)
        label = tr('Descargas · {p1} en curso', p1=len(active)) if active else tr('Descargas · {p1} listas', p1=len(ready))
        progress = (running or {}).get('progress') or {}
        if progress.get('total') and progress.get('received') is not None and progress.get('unit', 'bytes') == 'bytes':
            label += f' · {progress["received"] * 100 // progress["total"]}%'
        if str(button.label) != label:
            button.label = label
        button.display = bool(items)
        # Inicio repaints when an item changes state (queued, downloading, done), never for progress.
        done = {(item['kind'], item['target'], item['status']) for item in items}
        if done != self.downloads_done:
            # With a dialog open or an operation running, retry on the next tick.
            if self.busy or self.rebuilding or len(self.screen_stack) > 1:
                self.downloads_dirty = True
                return
            self.downloads_done = done
            self.call_later(self.refresh_home)

    def choose_download_mode(self, kind, profile, target):
        """Instalar/Actualizar: ahora (como siempre) o descarga en segundo plano."""
        from .components import update_downloaded
        from .downloads_ui import InstallModeDialog
        if kind == 'install':
            item = self.downloads.find('install', target)
            if item and item['status'] == 'done':
                self.begin('install', profile, target)
                return
            title, now = tr('¿Cómo quieres instalar {p1}?', p1=target), tr('Descargar e instalar ahora')
            action = 'install'
        elif kind == 'update':
            lock = self.inventory.get(target)
            if lock and update_downloaded(self.engine, lock):
                self.begin('update-component', profile, target)
                return
            title, now = tr('¿Cómo quieres actualizar {p1}?', p1=target), tr('Descargar y actualizar ahora')
            action = 'update-component'
        else:
            title, now = tr('¿Cómo quieres actualizar {p1} componentes?', p1=len(target)), tr('Actualizar todos ahora')
            action = 'update-all'

        def chosen(mode):
            if mode == 'now':
                self.begin(action, profile, target)
            elif mode == 'background':
                for value in (target if kind == 'all' else [target]):
                    self.downloads.enqueue('install' if kind == 'install' else 'update', value)
                self.notify(tr('Descarga en segundo plano iniciada. Sigue su progreso en Descargas.'))
        self.push_screen(InstallModeDialog(title, now), chosen)

    def open_downloads(self):
        from .downloads_ui import DownloadsDialog
        def chosen(result):
            if result is None:
                return
            kind, target = result
            if self.busy:
                self.notify(tr('Espera a que termine la operación en curso'), severity='warning')
                return
            self.begin('install' if kind == 'install' else 'update-component', self.current_profile(), target)
        self.push_screen(DownloadsDialog(self.downloads), chosen)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='body'):
            with VerticalScroll(id='sidebar'):
                yield Label(tr('PROFILES'), classes='title')
                yield Select([(p, p) for p in self.engine.profiles()], prompt=tr('Selecciona un profile'), id='profile')
                yield Button(tr('Gestionar profile'), id='manage-profile')
                yield Label(tr('ACCIONES'), id='actions-title', classes='title')
                yield Button(tr('Inicio · Instalados'), id='home-button', variant='success')
                yield Button(tr('Buscar actualizaciones'), id='check-updates')
                updater = Button(tr('Actualizar MITO'), id='self-update', variant='success')
                updater.display = False
                yield updater
                downloads = Button(tr('Descargas · {p1} en curso', p1=0), id='downloads-button', variant='primary')
                downloads.display = False
                yield downloads
                yield Button(tr('Abrir Home'), id='open-home')
                yield Button(tr('Configuración'), id='configuration')
                yield Button(tr('Pocketools'), id='pocketools')
                yield Button('MCP - disabled', id='mcp-button')
                monitor = Button('MCP · Jobs', id='mcp-jobs-button')
                monitor.display = self.mcp_monitor_enabled()
                yield monitor
                yield Button(tr('Comprobando Internet…'), id='network-status')
                yield Static('', id='busy-indicator')
            with ContentSwitcher(initial='home-page', id='pages'):
                with Vertical(id='home-page', classes='page'):
                    yield Label(tr('MIS COMPONENTES'), classes='title')
                    yield Static('', id='installed-summary', classes='summary')
                    yield VerticalScroll(id='installed-list')
                    with Horizontal(id='home-actions'):
                        yield Button(tr('Buscar e instalar nuevos componentes'), id='browse', variant='primary')
                        yield Button(tr('Agregar aplicación del host'), id='host-add')
                        bulk = Button(tr('Actualizar todos ({p1})', p1=0), id='bulk-update', variant='success')
                        bulk.display = False
                        yield bulk
                with Vertical(id='catalog-page', classes='page'):
                    yield Label(tr('BUSCAR E INSTALAR · CATÁLOGOS SCOOP'), classes='title')
                    yield Input(placeholder=tr('Buscar aplicación, runtime o herramienta…'), id='search')
                    with HorizontalScroll(id='bucket-filters'):
                        names = sorted(set(self.engine.catalog.sources()) | {p.stem for p in self.engine.catalog.folder.glob('*.json')})
                        for name in names:
                            yield Checkbox(name, value=name not in self.disabled_buckets, name=name, id=f'bucket-{name}')
                    yield Static('', id='summary', classes='summary')
                    yield DataTable(id='apps', cursor_type='row', zebra_stripes=True)
                    with VerticalScroll(id='details-scroll'):
                        yield Static(tr('Selecciona una aplicación.'), id='details', markup=False)
                    with Horizontal(id='catalog-actions'):
                        yield Button(tr('Instalar'), id='install', variant='success', disabled=True)
                        yield Button(tr('Versiones…'), id='versions', disabled=True)
                        yield Button(tr('Manifiesto'), id='manifest', disabled=True)
                        yield Button(tr('Actualizar catálogos'), id='refresh')
                        yield Button(tr('Volver a Inicio'), id='back')
        with Horizontal(id='log-panel'):
            yield RichLog(id='log', wrap=True, markup=False)
            yield Button(tr('Copiar log'), id='copy-log')
        yield Footer()

    def _handle_exception(self, error):
        capture(self.engine.root, error, handled=False, operation='ui.fatal')
        super()._handle_exception(error)

    async def on_mount(self):
        self.query_one(DataTable).add_columns(tr('Componente'), tr('Versión'), tr('Disponibilidad'), tr('Licencia'))
        names = self.engine.profiles()
        if names:
            self.query_one('#profile', Select).value = self.initial_profile if self.initial_profile in names else names[0]
        self.refresh_terminal_default()
        await self.refresh_home()
        from .relocation import pop_notice
        for message in pop_notice(self.engine.root):
            self.write_log(message)
        from .terminal_identity import apply_safely
        apply_safely(self.engine.root)
        self.check_network()
        self.start_network_poll()
        self.check_component_updates()
        self.check_mito_update()
        # Background downloads left pending by an earlier session continue now.
        try:
            self.downloads.load()
        except Exception as exc:
            capture(self.engine.root, exc, operation='downloads.load')
        self.set_interval(1, self.refresh_downloads)

    def start_network_poll(self):
        """Periodic silent check that only repaints the INTERNET button (0 disables it)."""
        from .network import properties
        try:
            seconds = float(properties(self.engine.root).get('network.poll_seconds', '20'))
        except ValueError:
            seconds = 20.0
        if seconds > 0 and seconds != float('inf'):
            self.network_timer = self.set_interval(max(5.0, seconds), self.poll_network)

    def poll_network(self):
        # Never overlap a manual check or diagnosis, nor interrupt an open dialog.
        if len(self.screen_stack) > 1 or any(worker.group == 'network' and not worker.is_finished for worker in self.workers):
            return
        self.check_network(quiet=True)

    @work(thread=True, exclusive=True, group='self-update-check', exit_on_error=False)
    def check_mito_update(self):
        self.refresh_mito_update()

    def refresh_mito_update(self, force=False):
        from .self_update import check
        # Test fixtures and incomplete bootstraps have no distributable version.
        if not (self.engine.root / 'core/app/mito/__init__.py').is_file():
            return
        try:
            info = check(self.engine.root, force=force)
            self.call_from_thread(self.mito_update_checked, info)
        except Exception as exc:
            capture(self.engine.root, exc, operation='update.check-mito')
            self.call_from_thread(self.mito_update_checked, None)
            self.call_from_thread(self.write_log, tr('No se pudo comprobar la actualización de MITO: {p1}', p1=exc), 'error')

    def mito_update_checked(self, info):
        self.mito_update = info
        button = self.query_one('#self-update', Button)
        button.display = info is not None
        if info:
            button.label = tr('Actualizar MITO') + ' · ' + info['version'] + (' · Offline' if info.get('local_path') else '')

    @work(thread=True, exclusive=True, group='self-update-install', exit_on_error=False)
    def prepare_mito_update(self, info):
        from .self_update import prepare, launch_ready
        try:
            folder = prepare(self.engine.root, info, allow_unverified=not info['hash'],
                             log=lambda message: self.call_from_thread(self.write_log, message))
            launch_ready(folder)
        except Exception as exc:
            capture(self.engine.root, exc, operation='update.prepare-mito')
            self.call_from_thread(self.write_log, tr('Error: {p1}', p1=exc), 'error')
            self.call_from_thread(self.operation_done, self.call_from_thread(self.current_profile))
            return
        self.call_from_thread(self.mito_update_launched)

    def mito_update_launched(self):
        self.stop_busy_indicator()
        self.action_quit()

    def confirm_mito_update(self, confirmed):
        if not confirmed or self.busy or not self.mito_update:
            return
        self.start_busy_indicator()
        for widget in self.query('Button, Checkbox, Select'):
            widget.disabled = True
        self.prepare_mito_update(dict(self.mito_update))

    @work(thread=True, exclusive=True, group='component-updates', exit_on_error=False)
    def check_component_updates(self):
        self.refresh_component_updates()

    def refresh_component_updates(self, force=False):
        try:
            inventory = self.engine.installed()
            if inventory:
                from .update_checks import daily
                def refresh():
                    self.call_from_thread(self.write_log, tr('Comprobando actualizaciones de componentes instalados…'))
                    count = self.engine.catalog.refresh_installed(inventory,
                        log=lambda message: self.call_from_thread(self.write_log, message))
                    self.call_from_thread(self.write_log, tr('Manifiestos de componentes instalados comprobados: {p1}.', p1=(count)))
                    self.call_from_thread(self.component_updates_done)
                    return count
                daily(self.engine.root, 'component-updates',
                      {'inventory': inventory, 'sources': self.engine.catalog.sources()}, refresh, force=force)
        except Exception as exc:
            capture(self.engine.root, exc, operation='update.check-components')
            self.call_from_thread(self.write_log, tr('Error al comprobar actualizaciones: {p1}', p1=(exc)), 'error')

    async def component_updates_done(self):
        self.entries = []
        if not self.busy:
            await self.refresh_home()
            if self.query_one('#pages', ContentSwitcher).current == 'catalog-page':
                self.load_catalog()

    @work(thread=True, exclusive=True, group="network", exit_on_error=False)
    def check_network(self, prompt=False, quiet=False):
        from .network import Network
        try:
            network = Network(self.engine.root)
            online = network.check(log=not quiet)
            local = online or network.local_network()
            self.call_from_thread(self.network_result, network, online, local, prompt)
        except Exception as exc:
            capture(self.engine.root, exc, operation='network.check')
            self.call_from_thread(self.network_result, None, False, True, False)

    def network_result(self, network, online, local, prompt):
        button = self.query_one("#network-status", Button)
        label = tr('Internet accesible') if online else tr('Internet no accesible') if local else tr('Sin conexión de red')
        # Assign only on change: the periodic check must not repaint an unchanged button.
        if str(button.label) != label:
            button.label = label
        variant = 'default' if online else 'error'
        if button.variant != variant:
            button.variant = variant
        if prompt and not online and local and network:
            button.label = tr('Diagnosticando la red…')
            self.diagnose_network()
        elif prompt and not online:
            self.notify(tr('Conecta el equipo a una red'), severity="warning")

    @work(thread=True, exclusive=True, group="network", exit_on_error=False)
    def diagnose_network(self):
        from .network import diagnose
        try:
            result = diagnose(self.engine.root)
        except Exception as exc:
            capture(self.engine.root, exc, operation='network.diagnose')
            result = None
        self.call_from_thread(self.network_diagnosis, result)

    def network_diagnosis(self, diagnosis):
        """Offer the detected route, then ask portal credentials only for this session."""
        from .network import Network
        from .network_ui import NetworkSetupDialog, ProxyDialog
        from .storage import YitoError
        if diagnosis is None or diagnosis.kind == 'ok' and not diagnosis.changes:
            self.check_network()
            return
        if diagnosis.kind == 'offline':
            self.check_network()
            self.notify(tr('Conecta el equipo a una red'), severity="warning")
            return

        def login():
            try:
                network = Network(self.engine.root)
            except YitoError as exc:
                self.notify(str(exc), severity='error')
                self.check_network()
                return
            if network.enabled:
                self.push_screen(ProxyDialog(network), lambda _: self.check_network())
            else:
                self.check_network()

        def saved(accepted):
            if not accepted:
                self.check_network()
                return
            from .network import apply
            try:
                apply(self.engine, diagnosis.changes)
            except (OSError, YitoError) as exc:
                self.notify(str(exc), severity='error')
                self.check_network()
                return
            self.write_log(tr('Configuración de red guardada en config.properties'))
            login() if diagnosis.kind == 'portal' else self.check_network()

        if diagnosis.changes:
            self.push_screen(NetworkSetupDialog(diagnosis), saved)
        elif diagnosis.kind == 'portal':
            login()
        else:
            self.check_network()
            if diagnosis.kind == 'auth':
                self.notify(tr('El proxy {p0} exige autenticación HTTP 407 ({p1}), que MITO no admite.', p0=diagnosis.proxy, p1=diagnosis.authenticate or 'Basic'), severity='warning', timeout=10)
            elif diagnosis.kind == 'blocked':
                self.notify(tr('La comprobación de Internet respondió HTTP {p0}.', p0=diagnosis.status), severity='warning', timeout=10)
            else:
                self.notify(tr('No se pudo llegar a Internet ni con el proxy de Windows ni directamente.'), severity='warning', timeout=10)

    def current_profile(self):
        value = self.query_one('#profile', Select).value
        return value if isinstance(value, str) else None

    async def refresh_home(self):
        import asyncio
        if not hasattr(self, '_home_lock'):
            self._home_lock = asyncio.Lock()
        async with self._home_lock:
            await self._refresh_home()

    async def _refresh_home(self):
        self.rebuilding = True
        try:
            self.inventory = self.engine.installed()
            profile = self.current_profile()
            active = self.engine.profile(profile)['apps'] if profile else {}
            container = self.query_one('#installed-list', VerticalScroll)
            await container.remove_children()
            self.row_ids = {}
            self.checkbox_states = {}
            rows = []
            from .components import available_update, update_downloaded
            from .host_exposure import linked as exposure_linked
            from .open_with import entries as open_with_entries
            catalog = dict(self.engine.catalog.all())
            pending = []
            for index, (installation, lock) in enumerate(self.inventory.items()):
                self.row_ids[str(index)] = installation
                label = f'{lock["key"]}   ·   {lock["version"]}' + (f'   ·   {tr("retenida")}' if lock.get('pinned') else '')
                widgets = [Checkbox(label, value=installation in active, name=installation, id=f'active-{index}', disabled=not profile or self.busy)]
                self.checkbox_states[widgets[0]] = (profile, installation in active)
                if installation in active and self.engine.can_open(lock):
                    widgets += [Button(tr('Abrir'), id=f'open-{index}', disabled=self.busy), Button(tr('Acc. directo'), id=f'link-{index}', classes='app-shortcut', disabled=self.busy)]
                    registered = bool(open_with_entries(self.engine.root, profile, installation))
                    widgets.append(Button(tr('Extensiones') + (' ✓' if registered else ' ✗'), id=f'openwith-{index}', classes='open-with-button', disabled=self.busy))
                    widgets.append(Button(tr('Protocolos'), id=f'protocol-{index}', classes='protocol-button', disabled=self.busy))
                if self.engine.can_open(lock):
                    exposed = exposure_linked(self.engine.root, installation)
                    # Estado, como Extensiones: ✓ enlazado, ✗ sin enlace (no la acción).
                    widgets.append(Button(tr('Host · ') + ('✓' if exposed else '✗'), id=f'expose-{index}', classes='host-data-button', disabled=self.busy))
                widgets.append(Button(tr('Info'), id=f'info-{index}', classes='component-info', disabled=self.busy))
                candidate = available_update(self.engine,lock,catalog)
                if candidate:
                    # Green: update available · yellow: downloading in the background ·
                    # blue: already downloaded, updating no longer waits for the network.
                    queued = self.downloads.find('update', installation)
                    queued = queued['status'] if queued else None
                    tooltip = tr('Versión {p1} · {p3}', p1=(candidate['version']), p3=(tr(candidate['status'])))
                    if queued in ('pending', 'downloading'):
                        label, variant = (tr('Descargando…') if queued == 'downloading' else tr('En cola')), 'warning'
                        tooltip += ' · ' + tr('Descarga en segundo plano; pulsa para ver el progreso')
                    elif candidate['status'] == 'Compatible' and update_downloaded(self.engine, lock, catalog):
                        label, variant = tr('Actualizar'), 'primary'
                        tooltip += ' · ' + tr('Descargada; actualizar ya no espera a la red')
                    else:
                        label, variant = tr('Actualizar'), 'success'
                    button = Button(label, id=f'update-{index}', classes='component-update', variant=variant, disabled=self.busy or candidate['status'] != 'Compatible')
                    button.tooltip = tooltip
                    widgets.append(button)
                    if candidate['status'] == 'Compatible':
                        pending.append(installation)
                widgets.append(Button(tr('Eliminar'), id=f'remove-{index}', classes='component-remove', disabled=self.busy))
                shade = 'component-row-light' if len(rows) % 2 else 'component-row-dark'
                rows.append(Horizontal(*widgets, classes=f'component {shade}'))
            from .host_apps import inventory as host_inventory
            from .host_data import linked
            from pathlib import Path
            self.host_inventory = host_inventory(self.engine.root)
            for identity, filename in self.host_inventory.items():
                path = Path(filename)
                unavailable = not path.is_file()
                widgets = [Static(f'{path.stem} · Host' + (tr(' · No disponible') if unavailable else ''), classes='host-app-name', markup=False),
                           Button(tr('Abrir'), id=f'host-open-{identity}', disabled=self.busy or unavailable or not profile),
                           Button(tr('Cache · ') + ('✓' if linked(self.engine.root, identity, 'cache') else '✗'), id=f'host-cache-{identity}', classes='host-data-button', disabled=self.busy),
                           Button(tr('Data · ') + ('✓' if linked(self.engine.root, identity, 'data') else '✗'), id=f'host-data-{identity}', classes='host-data-button', disabled=self.busy),
                           Button(tr('Acc. directo'), id=f'host-link-{identity}', classes='app-shortcut', disabled=self.busy or unavailable or not profile),
                           Button(tr('Info'), id=f'host-info-{identity}', classes='component-info', disabled=self.busy),
                           Button(tr('Eliminar'), id=f'host-remove-{identity}', classes='component-remove', disabled=self.busy)]
                shade = 'component-row-light' if len(rows) % 2 else 'component-row-dark'
                rows.append(Horizontal(*widgets, classes=f'component host-component {shade}'))
            # Sólo con más de una actualización compatible pendiente.
            self.pending_updates = pending
            bulk = self.query_one('#bulk-update', Button)
            bulk.label = tr('Actualizar todos ({p1})', p1=(len(pending)))
            bulk.display = len(pending) > 1
            bulk.disabled = self.busy
            if rows:
                await container.mount(*rows)
            else:
                await container.mount(Static(tr('Todavía no hay componentes instalados.\nUsa el botón de abajo para añadirlos.')))
            self.query_one('#installed-summary', Static).update(tr('{p0} instalados · {p2} activos en {p4} · {p6} del host\nLos cambios de entorno se cargan al abrir una nueva pestaña.', p0=(len(self.inventory)), p2=(len(active)), p4=(profile or tr('ningún profile')), p6=(len(self.host_inventory))))
        finally:
            self.rebuilding = False

    async def on_select_changed(self, event: Select.Changed):
        if self.is_mounted and event.select.id == 'profile' and self.query('#profile'):
            self.refresh_terminal_default()
            await self.refresh_home()

    def refresh_terminal_default(self):
        from .terminal import prepare
        from .storage import YitoError
        try:
            prepare(self.engine, selected_profile=self.current_profile())
        except (OSError, ValueError, YitoError) as exc:
            capture(self.engine.root, exc, operation='terminal.settings')
            self.notify(str(exc), severity='error')

    def on_checkbox_changed(self, event: Checkbox.Changed):
        if event.checkbox.id and event.checkbox.id.startswith('bucket-'):
            name = event.checkbox.name
            if event.value:
                self.disabled_buckets.discard(name)
            else:
                self.disabled_buckets.add(name)
            write_json(self.filter_path, sorted(self.disabled_buckets))
            if self.is_mounted:
                self.filter_catalog()
            return
        if self.rebuilding or self.busy or not event.checkbox.is_mounted or event.checkbox not in self.query('#installed-list Checkbox'):
            return
        profile = self.current_profile()
        displayed_profile, displayed_value = self.checkbox_states.get(event.checkbox, (None, None))
        if profile and profile == displayed_profile and event.value == event.checkbox.value and event.value != displayed_value:
            # Only a change to the displayed checkbox is user intent. Mount and
            # queued events must never turn a fresh inventory into profile edits.
            self.checkbox_states[event.checkbox] = (profile, event.value)
            if (event.checkbox.name in self.engine.profile(profile)['apps']) != event.value:
                self.begin('toggle', profile, event.checkbox.name, event.value)

    def load_catalog(self):
        if not self.entries:
            self.entries = [(key, m, self.engine.catalog.compatibility(key, m)) for key, m in self.engine.catalog.all()]
        self.filter_catalog()

    def filter_catalog(self):
        term = self.query_one('#search', Input).value.lower()
        table = self.query_one(DataTable)
        table.clear()
        self.selected = None
        self.catalog_installed = set(self.engine.installed())
        self.query_one('#install', Button).disabled = True
        self.query_one('#versions', Button).disabled = True
        self.query_one('#manifest', Button).disabled = True
        rows = rank_entries(self.entries, term, self.disabled_buckets)
        self.query_one('#details', Static).update(tr('Selecciona una aplicación.') if rows else tr('Sin resultados en los buckets seleccionados.'))
        for key, manifest, status in rows:
            availability = tr('Instalado') if key in self.catalog_installed else tr('Disponible') if status == 'Compatible' else tr('Todavía no soportado')
            table.add_row(key, str(manifest.get('version', '?')), availability, Text(license_label(manifest)), key=key)
        summary = tr('{p0} componentes · {p2} disponibles\nOrden: palabras coincidentes, bucket/nombre y descripción. Coincidencias parciales al final.', p0=(len(rows)), p2=(sum((s == 'Compatible' and k not in self.catalog_installed for k, _, s in rows))))
        if not rows:
            message = Text(summary.split('\n', 1)[0] + '\n')
            message.append(tr('Sin componentes. Pulsa el botón "{refresh}". Si no aparece ninguno comprueba tus fuentes en "{configuration}"',
                              refresh=tr('Actualizar catálogos'), configuration=tr('Configuración')),
                           style='bold #4da6ff')
            self.query_one('#summary', Static).update(message)
        else:
            self.query_one('#summary', Static).update(summary)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == 'search' and self.is_mounted:
            self.filter_catalog()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.row_key not in self.query_one(DataTable).rows:
            return
        self.selected = str(event.row_key.value)
        found = next(((m, s) for k, m, s in self.entries if k == self.selected), None)
        if found:
            m, status = found
            self.query_one('#manifest', Button).disabled = False
            installed = self.selected in self.catalog_installed
            self.query_one('#install', Button).disabled = self.busy or installed or status != 'Compatible'
            # Instalado o no: elegir otra versión sustituye la instalación actual.
            self.query_one('#versions', Button).disabled = self.busy or status != 'Compatible'
            notes = m.get('notes', [])
            notes = '\n'.join(str(note) for note in notes) if isinstance(notes, list) else str(notes)
            availability = tr('Instalado') if installed else tr(status)
            details = f'{self.selected} · {m.get("version")}\n{m.get("description", "")}\n{availability}'
            if m.get('architecture', {}).get('64bit', {}).get('pre_install', m.get('pre_install')):
                details += tr('\npre_install: se ejecuta con PowerShell durante la instalación.')
            installer = m.get('architecture', {}).get('64bit', {}).get('installer', m.get('installer'))
            if isinstance(installer, dict) and installer.get('script'):
                details += '\n' + tr('{hook}: se ejecuta con PowerShell durante la instalación.', hook='installer.script')
            if isinstance(installer, dict) and installer.get('file'):
                details += '\n' + tr('{hook}: se ejecuta con PowerShell durante la instalación.', hook='installer.file')
            for hook in ('post_install',):
                if m.get('architecture', {}).get('64bit', {}).get(hook, m.get(hook)):
                    details += tr('\nPaso omitido: MITO no ejecuta {p1}.', p1=(hook))
            if notes:
                details += tr('\n\nNOTAS\n{p1}', p1=(notes))
            self.query_one('#details', Static).update(details)
            self.query_one('#details-scroll', VerticalScroll).scroll_home(animate=False)

    def action_search(self):
        self.query_one('#pages', ContentSwitcher).current = 'catalog-page'
        self.load_catalog()
        self.query_one('#search', Input).focus()

    def action_home(self):
        self.query_one('#pages', ContentSwitcher).current = 'home-page'
        if not self.busy:
            self.check_mito_update()

    def on_button_pressed(self, event: Button.Pressed):
        action = event.button.id
        if action == 'manifest':
            manifest = next((m for key, m, _ in self.entries if key == self.selected), None)
            if manifest is not None:
                self.push_screen(ManifestDialog(self.selected, manifest))
            return
        if action == 'mcp-button':
            from .mcp_ui import MCPDialog
            self.push_screen(MCPDialog())
            return
        if action == 'mcp-jobs-button':
            if self.mcp_monitor_enabled():
                from .mcp_ui import MCPJobsDialog
                self.push_screen(MCPJobsDialog())
            return
        if action == 'copy-log':
            if self.log_messages:
                self.copy_log('\n'.join(self.log_messages))
            else:
                self.notify(tr('El log está vacío'))
            return
        if action == 'network-status':
            self.check_network(prompt=True)
            return
        if action == 'downloads-button':
            self.open_downloads()
            return
        if action == 'open-home':
            from .persistence import open_location
            from .storage import YitoError
            try:
                open_location(self.engine.root/'home')
            except (OSError, YitoError) as exc:
                capture(self.engine.root, exc, operation='home.open-location')
                self.notify(str(exc), severity='error')
            return
        if action == 'browse':
            self.action_search()
            return
        if action in ('home-button', 'back'):
            self.action_home()
            return
        if self.busy or self.rebuilding:
            return
        if action == 'check-updates':
            self.begin(action, self.current_profile(), None)
            return
        if action == 'self-update':
            if self.mito_update:
                from .self_update_ui import SelfUpdateDialog
                self.push_screen(SelfUpdateDialog(self.mito_update), self.confirm_mito_update)
            return
        profile = self.current_profile()
        if action == 'host-add':
            self.begin(action, profile, None)
            return
        if action.startswith(('host-open-', 'host-link-', 'host-info-', 'host-remove-', 'host-cache-', 'host-data-')):
            from .host_apps import target as host_target
            from .host_apps_ui import HostInfoDialog, RemoveHostDialog
            kind, identity = action[5:].split('-', 1)
            if kind in ('open', 'link') and not profile:
                self.notify(tr('Selecciona un profile'), severity='warning')
                return
            if kind in ('info', 'remove', 'open'):
                from .storage import YitoError
                try:
                    path = host_target(self.engine.root, identity)
                except (YitoError, OSError, ValueError) as exc:
                    capture(self.engine.root, exc, operation='host.resolve')
                    self.notify(str(exc), severity='error')
                    return
            if kind in ('cache', 'data'):
                self.begin('host-' + kind, profile, identity)
            elif kind == 'info':
                self.push_screen(HostInfoDialog(path))
            elif kind == 'remove':
                self.push_screen(RemoveHostDialog(path),
                                 lambda confirmed: self.begin('host-remove', profile, identity) if confirmed else None)
            elif kind == 'link':
                def shortcut_selected(result):
                    if result is not None:
                        badge, color, coupled, arguments = result
                        self.begin('host-link', profile, identity, badge=badge,
                            coupled=coupled, arguments=arguments, color=color)
                self.push_screen(ShortcutDialog(application=True, engine=self.engine), shortcut_selected)
            else:
                self.push_screen(OpenApplicationDialog(path.stem),
                    lambda result: self.begin('host-open', profile, identity,
                        coupled=result[0], arguments=result[1]) if result is not None else None)
            return
        if action == 'configuration':
            self.open_configuration()
            return
        if action == 'pocketools':
            from .pocketools_ui import PocketoolsDialog
            from .storage import YitoError
            try:
                self.push_screen(PocketoolsDialog(self.engine, profile))
            except (YitoError, OSError, ValueError) as exc:
                capture(self.engine.root, exc, operation='pocketools.open')
                self.notify(str(exc), severity='error')
            return
        if action == 'manage-profile':
            from .profile_management_ui import ManageProfileDialog
            self.push_screen(ManageProfileDialog(profile, bool(profile) and len(self.engine.profiles()) > 1),
                             lambda result: self.manage_profile_result(profile, result))
            return
        if action == 'bulk-update':
            if len(getattr(self, 'pending_updates', [])) > 1:
                self.choose_download_mode('all', profile, list(self.pending_updates))
            return
        if action not in ('create', 'refresh', 'install', 'versions') and not action.startswith(('remove-', 'update-', 'info-', 'expose-')) and not profile:
            self.notify(tr('Selecciona un profile'), severity='warning')
            return
        if action.startswith('info-'):
            installation = self.row_ids[action.removeprefix('info-')]
            def closed(result):
                if result == 'release-version':
                    self.begin('release-version', profile, installation)
            self.push_screen(ComponentInfoDialog(self.engine.root,self.inventory[installation],profile), closed)
            return
        if action.startswith('remove-'):
            from .components import users
            installation = self.row_ids[action.removeprefix('remove-')]
            def confirmed(remove):
                if remove:
                    self.begin('remove-component',profile,installation)
            self.push_screen(RemoveComponentDialog(self.inventory[installation],users(self.engine,installation)),confirmed)
            return
        if action.startswith('expose-'):
            self.begin('host-expose', profile, self.row_ids[action.removeprefix('expose-')])
            return
        if action.startswith('openwith-'):
            from .open_with import entries as open_with_entries
            from .open_with_ui import OpenWithDialog
            target = self.row_ids[action.removeprefix('openwith-')]
            lock = self.inventory[target]
            current = {record.get('shortcut'): record['extensions']
                       for record in open_with_entries(self.engine.root, profile, target)}
            self.push_screen(OpenWithDialog(lock['key'], self.engine.application_entries(lock), current),
                lambda result: self.begin('open-with', profile, target, arguments=result)
                if result is not None else None)
            return
        if action.startswith('protocol-'):
            from . import protocols
            from .protocols_ui import ProtocolDialog
            from .storage import YitoError
            target = self.row_ids[action.removeprefix('protocol-')]
            lock = self.inventory[target]
            try:
                # Claims live with the dialog; one left by a closed MITO is undone first.
                protocols.unregister(self.engine.root, lambda record: True, self.write_log)
                rows = protocols.rows(self.engine, profile, target)
            except (YitoError, OSError, ValueError) as exc:
                capture(self.engine.root, exc, operation='protocols.open')
                self.notify(str(exc), severity='error')
                return
            session = protocols.Session(self.engine, profile, target, self.write_log)
            self.push_screen(ProtocolDialog(lock['key'], self.engine.application_entries(lock), rows, session))
            return
        if action.startswith('update-'):
            installation = self.row_ids[action.removeprefix('update-')]
            queued = self.downloads.find('update', installation)
            if queued and queued['status'] in ('pending', 'downloading'):
                self.open_downloads()
            else:
                self.choose_download_mode('update', profile, installation)
            return
        if action == 'install' and (not self.selected or self.query_one('#install', Button).disabled):
            return
        if action == 'install':
            self.choose_download_mode('install', profile, self.selected)
            return
        if action == 'versions' and (not self.selected or self.query_one('#versions', Button).disabled):
            return
        target = self.row_ids.get(action.removeprefix('open-')) if action.startswith('open-') else self.selected
        if action.startswith('open-'):
            lock = self.inventory[target]
            entries = self.engine.application_entries(lock)
            self.push_screen(OpenApplicationDialog(lock['key'], entries),
                lambda result: self.begin('launch', profile, target, shortcut=result[2],
                    coupled=result[0], arguments=result[1]) if result is not None else None)
            return
        elif action.startswith('link-'):
            target = self.row_ids[action.removeprefix('link-')]
            shortcut_action = 'application-shortcut'
            def confirmed(result):
                if result is not None:
                    badge, color, coupled, arguments, shortcut = result
                    self.begin(shortcut_action, profile, target, badge=badge,
                        coupled=coupled, arguments=arguments, shortcut=shortcut, color=color)
            entries = self.engine.application_entries(self.inventory[target])
            self.push_screen(ShortcutDialog(application=True, entries=entries, engine=self.engine), confirmed)
            return
        self.begin(action, profile, target)

    def manage_profile_result(self, profile, result):
        if result is None:
            return
        action, name = result
        if action == 'create':
            self.begin(action, profile, name)
        elif action == 'profile-configuration':
            self.open_configuration(profile)
        elif action == 'profile-files':
            self.open_profile_files(profile)
        elif action in ('export-profile', 'import-profile'):
            from .profile_transfer_ui import TransferPathDialog
            def chosen(selection):
                if selection:
                    self.begin(action, profile, selection)
            from .storage import YitoError
            archive = False
            if action == 'export-profile':
                try:
                    archive = bool(self.engine.profile(profile).get('files'))
                except (OSError, ValueError, YitoError) as exc:
                    capture(self.engine.root, exc, operation='profile.export')
            self.push_screen(TransferPathDialog(self.engine.root, profile if action == 'export-profile' else None, archive), chosen)
        elif action == 'mito-shortcut':
            def confirmed(result):
                if result is not None:
                    badge, color = result
                    self.begin(action, profile, None, badge=badge, color=color)
            self.push_screen(ShortcutDialog(engine=self.engine), confirmed)
        elif action == 'delete-profile':
            from .profile_management_ui import DeleteProfileDialog
            self.push_screen(DeleteProfileDialog(profile),
                             lambda confirmed: self.begin(action, profile, None) if confirmed else None)

    def open_configuration(self, profile=None):
        from .configuration_ui import ConfigurationDialog
        from .storage import YitoError
        try:
            dialog = ConfigurationDialog(self.engine, profile)
        except (OSError, ValueError, YitoError) as exc:
            capture(self.engine.root, exc, operation='configuration.open')
            self.notify(str(exc), severity='error')
            return
        def saved(changed):
            if changed:
                self.notify(tr('Configuración guardada. Abre nuevas pestañas o aplicaciones para usar el nuevo entorno.'))
                if not profile:
                    self.refresh_terminal_default()
                    from .i18n import Translator
                    if Translator.from_root(self.engine.root).language != self.translator.language:
                        self.notify(tr('Vuelve a abrir el gestor MITO para aplicar el idioma seleccionado.'), timeout=10)
                    self.refresh_mcp_monitor()
                    self.check_network()
        self.push_screen(dialog, saved)

    def open_profile_files(self, profile):
        from .profile_files_ui import ProfileFilesDialog
        from .storage import YitoError
        try:
            dialog = ProfileFilesDialog(self.engine, profile)
        except (OSError, ValueError, YitoError) as exc:
            capture(self.engine.root, exc, operation='profile.files.open')
            self.notify(str(exc), severity='error')
            return
        self.push_screen(dialog, lambda saved: self.notify(tr('Archivos del profile guardados.')) if saved else None)

    def begin(self, action, profile, target, active=None, badge=None, coupled=False, arguments=(), shortcut=None, color=None):
        self.start_busy_indicator()
        for widget in self.query('Button, Checkbox, Select'):
            widget.disabled = widget.id != 'copy-log'
        self.operation(action, profile, target, active, badge, coupled, arguments, shortcut, color)

    def start_busy_indicator(self):
        self.busy = True
        self.busy_sequence += 1
        sequence = self.busy_sequence
        self.query_one('#busy-indicator', Static).update('')
        self.set_timer(2, lambda: self.show_busy_indicator(sequence))

    def show_busy_indicator(self, sequence):
        if self.busy and sequence == self.busy_sequence:
            for indicator in self.query('#busy-indicator'):
                indicator.update('⏳ …')

    def stop_busy_indicator(self):
        self.busy = False
        self.busy_sequence += 1
        for indicator in self.query('#busy-indicator'):
            indicator.update('')

    def schedule_operation_dialog(self, operation):
        """Show the progress dialog when the operation still runs after 2 s."""
        self.set_timer(2, lambda: self.show_operation_dialog(operation))

    def show_operation_dialog(self, operation):
        if (operation.state.get('active') and getattr(self, 'current_operation', None) is operation
                and getattr(self, 'operation_dialog', None) is None):
            self.operation_dialog = OperationDialog(operation)
            self.push_screen(self.operation_dialog)

    def close_operation_dialog(self):
        self.current_operation = None
        dialog, self.operation_dialog = getattr(self, 'operation_dialog', None), None
        if dialog is not None:
            dialog.finish()

    def long_step(self, line):
        """Show the engine's step without progress after 2 s; hide it when it ends."""
        operation = getattr(self, 'current_operation', None)
        if operation is not None and operation.state.get('active'):
            # The progress dialog already shows this step.
            return
        self.long_step_sequence = getattr(self, 'long_step_sequence', 0) + 1
        sequence = self.long_step_sequence
        if line.active:
            self.set_timer(2, lambda: self.show_long_step(sequence, str(line)))
            return
        dialog, self.long_step_dialog = getattr(self, 'long_step_dialog', None), None
        if dialog is not None:
            dialog.finish()

    def show_long_step(self, sequence, message):
        if sequence == self.long_step_sequence and getattr(self, 'long_step_dialog', None) is None:
            self.long_step_dialog = LongStepDialog(message)
            self.push_screen(self.long_step_dialog)

    def write_log(self, message, level='info'):
        from .engine import LongStep
        if isinstance(message, LongStep):
            self.long_step(message)
        message = str(message)
        diagnostics.write(self.engine.root, level, 'ui', message)
        self.log_messages.append(message)
        self.query_one('#log', RichLog).write(Text(message, style='bold red' if level == 'error' else ''))

    @work(thread=True, exclusive=True, group='clipboard', exit_on_error=False)
    def copy_log(self, text):
        try:
            # Text travels through stdin, never as PowerShell code or arguments.
            subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                            '[Console]::InputEncoding = [System.Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
                           input=text, text=True, encoding='utf-8', capture_output=True, check=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            self.call_from_thread(self.notify, tr('No se pudo copiar el log al portapapeles'), severity='error')
        else:
            self.call_from_thread(self.notify, tr('Log copiado al portapapeles'))

    async def confirm_unverified(self, downloads):
        return await self.push_screen_wait(UnverifiedDownloadsDialog(downloads))

    async def wait_startup_updates(self):
        # A manual request must run after pending startup reads, so their older
        # results cannot overwrite the result of the explicit check.
        for worker in list(self.workers):
            if worker.group in ('component-updates', 'self-update-check'):
                await worker.wait()

    async def confirm_install_version(self, key, previous, version):
        return await self.push_screen_wait(InstallVersionDialog(key, previous, version))

    def install_from_catalog(self, target, log, confirm):
        from .components import version_key
        from .storage import YitoError
        # Retain the version the user saw, even if a startup check is finishing.
        previous = next((m['version'] for key, m, _ in self.entries if key == target),
                        self.engine.catalog.resolve(target)[1]['version'])
        self.call_from_thread(self.wait_startup_updates)
        log(tr('Comprobando {p1}…', p1=target))
        if self.engine.catalog.refresh_keys([target], log) != 1:
            return
        self.entries = []

        def review(records):
            manifest = records[target]['manifest']
            old, new = version_key(previous), version_key(manifest['version'])
            if old is not None and new is not None and new > old:
                if not self.call_from_thread(self.confirm_install_version, target, previous, manifest['version']):
                    # Keep cancellation distinct from an import-plan rejection.
                    raise YitoError(tr('Instalación cancelada'))
            return True

        self.engine.install(target, log=log, confirm_unverified=confirm, confirm_plan=review)

    async def choose_version(self, key, current, installed, found, autoupdate, history_available, loader=None):
        return await self.push_screen_wait(VersionsDialog(key, current, installed, found, autoupdate, history_available, loader))

    def install_version_from_catalog(self, target, log, confirm):
        from .versions import history, load_versions, resolve
        from .components import install_version
        _, manifest = self.engine.catalog.resolve(target)
        log(tr('Consultando versiones de {p1}…', p1=target))
        found = None
        try:
            found = history(self.engine.root, self.engine.catalog, target)
        except Exception as exc:
            capture(self.engine.root, exc, operation='versions.history', component=target)
            log(tr('No se pudo consultar el histórico del bucket: {p1}', p1=exc))
        installed = self.engine.installed().get(target)
        version = self.call_from_thread(self.choose_version, target, str(manifest.get('version', '?')),
                                        installed['version'] if installed else None, found,
                                        isinstance(manifest.get('autoupdate'), dict), found is not None,
                                        lambda current, wanted: load_versions(self.engine.root, current, wanted))
        if not version:
            log(tr('Selección de versión cancelada'))
            return
        # {} evita repetir la consulta del histórico si ya falló o no existe.
        chosen, pin = resolve(self.engine.root, self.engine.catalog, target, version,
                              found=found if found is not None else {}, log=log)
        install_version(self.engine, target, chosen, pin, log=log, confirm_unverified=confirm)

    async def choose_host_application(self, found):
        from .host_apps_ui import HostDiscoverDialog
        return await self.push_screen_wait(HostDiscoverDialog(found))

    async def confirm_host_data(self, message, label=None):
        from .host_apps_ui import HostDataConfirm
        return await self.push_screen_wait(HostDataConfirm(message, label))

    def offer_host_data(self, identity, kind, folder):
        from .host_data import plan, link
        path, folder, _ = plan(self.engine.root, kind, folder)
        label = tr('Host data') if kind == 'data' else tr('Host cache')
        if not self.call_from_thread(self.confirm_host_data,
                tr('{p0}: ¿crear un enlace?\nHost: {p2}\nMITO: {p4}\nCompartido por todos los profiles. La aplicación podrá modificar los datos del host. Cierra la aplicación antes de enlazar.', p0=(label), p2=(folder), p4=(path))):
            return
        replace = path.exists() and not (path.is_junction() and path.resolve() == folder)
        if replace and not self.call_from_thread(self.confirm_host_data,
                tr('La carpeta {p1} ya existe y ha de ser borrada para generar el enlace.\nSe perderá su contenido local en MITO.', p1=(path)), tr('Borrar y enlazar')):
            return
        link(self.engine.root, identity, kind, folder, replace=replace)

    async def confirm_import(self, name, records, items=()):
        from .profile_transfer_ui import ImportReviewDialog
        return await self.push_screen_wait(ImportReviewDialog(name, records, self.engine.installed(), items))

    @work(thread=True, exit_on_error=False)
    def operation(self, action, profile, target, active, badge, coupled, arguments, shortcut=None, color=None):
        def log(message, level='info'):
            self.call_from_thread(self.write_log, message, level)
        def confirm(downloads):
            return self.call_from_thread(self.confirm_unverified, downloads)
        from contextlib import ExitStack
        from .operation import Cancelled, Operation, monitored
        scheduled = []
        def progress(state):
            # The dialog polls the state; only its first appearance needs the UI thread.
            if state.get('active') and not scheduled:
                scheduled.append(True)
                self.call_from_thread(self.schedule_operation_dialog, current)
        current = Operation(progress)
        self.current_operation = current
        scope = ExitStack()
        scope.enter_context(monitored(current))
        try:
            if action == 'check-updates':
                self.call_from_thread(self.wait_startup_updates)
                self.refresh_component_updates(force=True)
                self.refresh_mito_update(force=True)
                log(tr('Búsqueda de actualizaciones finalizada.'))
            elif action == 'host-add':
                from .host_apps import choose_file, add, discover
                log(tr('Buscando aplicaciones en el menú Inicio y el escritorio del host…'))
                try:
                    found = discover(self.engine.root)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    log(tr('No se pudieron revisar los accesos del host: {p1}', p1=(exc)))
                    found = []
                # Nothing discovered: straight to the native picker, as before.
                filename = self.call_from_thread(self.choose_host_application, found) if found else 'browse'
                if filename == 'browse':
                    filename = choose_file()
                if filename:
                    identity = add(self.engine.root, filename)
                    log(tr('Aplicación del host agregada. Usará el entorno del profile de MITO seleccionado.'))
                    from .host_data import candidates, entries
                    for kind in ('data', 'cache'):
                        if not entries(self.engine.root, identity, kind):
                            log(tr('Buscando carpetas del host: {p1}…', p1=(kind)))
                            for folder in candidates(self.engine.root, identity, kind):
                                self.offer_host_data(identity, kind, folder)
            elif action in ('host-cache', 'host-data'):
                from .host_data import entries, unlink, choose_folder, candidates
                kind = action[5:]
                current = entries(self.engine.root, target, kind)
                if current:
                    paths = '\n'.join(item['target'] for item in current)
                    if self.call_from_thread(self.confirm_host_data,
                            tr('¿Eliminar los enlaces de Host {p1}?\n{p3}\nLos datos del host se conservan.', p1=(kind), p3=(paths)), tr('Desenlazar')):
                        unlink(self.engine.root, target, kind)
                else:
                    log(tr('Buscando carpetas del host: {p1}…', p1=(kind)))
                    found = candidates(self.engine.root, target, kind)
                    for folder in found:
                        self.offer_host_data(target, kind, folder)
                    if not entries(self.engine.root, target, kind):
                        manual = not found or self.call_from_thread(self.confirm_host_data,
                            tr('¿Seleccionar otra carpeta del host manualmente?'), tr('Seleccionar'))
                        if manual:
                            folder = choose_folder(kind)
                            if folder:
                                self.offer_host_data(target, kind, folder)
            elif action == 'open-with':
                from .open_with import apply
                apply(self.engine, profile, target, arguments, log)
            elif action == 'host-expose':
                from . import host_exposure as exposure
                current = exposure.entries(self.engine.root, target)
                if current:
                    paths = '\n'.join(str(exposure.host_folder(kind)/item['relative']) for kind, item in current)
                    if self.call_from_thread(self.confirm_host_data,
                            tr('¿Eliminar los enlaces del host?\n{p1}\nLos datos de MITO se conservan.', p1=(paths)), tr('Desenlazar')):
                        exposure.unlink(self.engine.root, target)
                        log(tr('Enlaces del host retirados. Los datos de MITO se conservan.'))
                else:
                    found = exposure.candidates(self.engine, self.engine.installed()[target])
                    if not found:
                        log(tr('No hay carpetas en el AppData de MITO que coincidan con el ejecutable. Abre antes la aplicación desde MITO.'))
                    for kind, relative in found:
                        source, host = exposure.plan(self.engine.root, kind, relative)
                        if not self.call_from_thread(self.confirm_host_data,
                                tr('{p0} en el host: ¿crear un enlace?\nHost: {p2}\nMITO: {p4}\nSi la aplicación arranca fuera de MITO (inicio automático, asociaciones) usará estos datos, pero no el entorno del profile. Cierra la aplicación antes de enlazar.', p0=(exposure.KINDS[kind]), p2=(host), p4=(source))):
                            continue
                        aside = (host.exists() or host.is_junction()) and not exposure.points_to(host, source)
                        if aside and not self.call_from_thread(self.confirm_host_data,
                                tr('La carpeta del host {p1} ya existe. Se renombrará a {p3} y se restaurará al desenlazar.', p1=(host), p3=(exposure.backup_path(host).name)), tr('Apartar y enlazar')):
                            continue
                        exposure.link(self.engine, target, kind, relative, aside=aside)
                        log(tr('Enlace creado en el host: {p1}', p1=(host)))
            elif action == 'host-open':
                from .host_apps import launch
                launch(self.engine, profile, target, arguments, coupled=coupled)
            elif action == 'host-remove':
                from .host_apps import remove
                remove(self.engine.root, target)
                log(tr('Referencia eliminada. El ejecutable y los datos del host se conservan.'))
            elif action == 'host-link':
                from .host_apps import shortcut
                path = shortcut(self.engine, profile, target, badge, coupled=coupled,
                    arguments=arguments, color=color)
                log(tr('Acceso directo al host creado: {p1}', p1=(path.name)))
            elif action == 'create':
                profile = self.engine.create_profile(target)
                log(tr('Profile {p1} creado', p1=(profile)))
            elif action == 'export-profile':
                from .profile_transfer import export_profile
                path = export_profile(self.engine, profile, target[0], log=log)
                log(tr('Profile exportado: {p1}', p1=(path)))
            elif action == 'import-profile':
                from .profile_transfer import import_profile
                profile = import_profile(self.engine, target[0], target[1],
                    confirm_plan=lambda records, items=(): self.call_from_thread(self.confirm_import, target[1], records, items),
                    confirm_unverified=confirm, log=log)
            elif action == 'toggle':
                self.engine.set_active(profile, target, active)
            elif action == 'remove-component':
                from .components import remove
                remove(self.engine, target, log)
            elif action == 'update-component':
                from .components import update
                update(self.engine, target, log, confirm_unverified=confirm)
                self.downloads.forget('update', target)
            elif action == 'update-all':
                from .components import update_all
                for installation in update_all(self.engine, target, log, confirm_unverified=confirm):
                    self.downloads.forget('update', installation)
            elif action == 'install':
                self.install_from_catalog(target, log, confirm)
                if self.engine.installed().get(target):
                    self.downloads.forget('install', target)
            elif action == 'versions':
                self.install_version_from_catalog(target, log, confirm)
            elif action == 'release-version':
                from .components import release_version
                release_version(self.engine, target, log)
            elif action == 'delete-profile':
                removed = profile
                profile = self.engine.delete_profile(profile)
                log(tr('Profile {p1} eliminado. Se conservan componentes, home y workspace.', p1=(removed)))
            elif action == 'mito-shortcut':
                from .shortcuts import mito_link
                with mutation(self.engine.root):
                    path = mito_link(self.engine, profile, badge, color=color)
                log(tr('Acceso a MITO creado en el escritorio: {p1}', p1=(path.name)))
            elif action == 'launch':
                self.engine.launch(profile, target, arguments=arguments, coupled=coupled, shortcut=shortcut)
            elif action == 'application-shortcut':
                from .shortcuts import application_link
                with mutation(self.engine.root):
                    path = application_link(self.engine, profile, target, badge,
                        coupled=coupled, arguments=arguments, shortcut=shortcut, color=color)
                log(tr('Acceso directo creado en el escritorio: {p1}', p1=(path.name)))
            elif action == 'refresh':
                with mutation(self.engine.root):
                    for bucket, source in self.engine.catalog.sources().items():
                        log(tr('Actualizando {p1}…', p1=(bucket)))
                        log(tr('{p0}: {p2} manifiestos', p0=(bucket), p2=(self.engine.catalog.import_bucket(bucket, source))))
                self.entries = []
        except Cancelled:
            log(tr('Instalación cancelada. No se ha publicado nada.'), level='error')
        except Exception as exc:
            capture(self.engine.root, exc, operation='ui.' + action,
                    component=target if isinstance(target, str) and '/' in target else None)
            log(tr('Error: {p1}', p1=(exc)), level='error')
            from .transfer import DownloadIntegrityError
            if isinstance(exc, DownloadIntegrityError):
                # Not a MITO fault: explain it in a centered dialog, not only in the log.
                self.call_from_thread(self.push_screen, DownloadIntegrityDialog(exc))
        finally:
            scope.close()
            self.call_from_thread(self.close_operation_dialog)
            self.call_from_thread(self.operation_done, profile)

    async def operation_done(self, profile):
        for widget in self.query('Button, Checkbox, Select'):
            widget.disabled = False
        select = self.query_one('#profile', Select)
        with select.prevent(Select.Changed):
            select.set_options([(p, p) for p in self.engine.profiles()])
            if profile in self.engine.profiles():
                select.value = profile
        self.stop_busy_indicator()
        await self.refresh_home()
        for checkbox in self.query(Checkbox):
            checkbox.disabled = not self.current_profile()
        if self.query_one('#pages', ContentSwitcher).current == 'catalog-page':
            self.load_catalog()

    def action_quit(self):
        if self.busy:
            self.notify(tr('Espera a que termine la operación antes de salir'))
            return
        pending = self.downloads.active()
        if pending:
            from .downloads_ui import QuitDownloadsDialog
            # Downloads only fill the cache: leaving pauses them and they resume later.
            self.push_screen(QuitDownloadsDialog(pending), lambda leave: self.quit_now() if leave else None)
            return
        self.quit_now()

    def quit_now(self):
        if self.busy:
            self.notify(tr('Espera a que termine la operación antes de salir'))
        elif self.mcp.state != 'disabled':
            self.mcp_quit = True
            self.mcp.stop()
        else:
            self.exit()

    def mcp_monitor_enabled(self):
        from .configuration import Configuration
        return Configuration(self.engine).values.get('mcp.jobs.monitor', 'false') == 'true'

    def refresh_mcp_monitor(self):
        enabled = self.mcp_monitor_enabled()
        self.query_one('#mcp-jobs-button', Button).display = enabled
        if not enabled:
            from .mcp_ui import MCPJobsDialog
            if isinstance(self.screen, MCPJobsDialog): self.screen.dismiss()

    def start_mcp(self, port):
        self.mcp.start(port)
        if self.mcp_timer is None:
            self.mcp_timer = self.set_interval(.25, self.poll_mcp)

    async def poll_mcp(self):
        import queue
        changed = False
        while True:
            try: message = self.mcp.events.get_nowait()
            except queue.Empty: break
            kind = message.get('type')
            if kind == 'confirm': self.mcp_confirmations.append(message)
            if kind == 'changed': changed = True
            if kind == 'output':
                from .mcp_ui import MCPJobsDialog
                for screen in self.screen_stack:
                    if isinstance(screen, MCPJobsDialog): screen.show_output(message)
            if kind == 'stopped':
                self.mcp_confirmations.clear()
                if self.mcp_timer: self.mcp_timer.stop(); self.mcp_timer = None
                if message.get('error'): self.write_log(message['error'], 'error')
                if self.mcp_quit: self.exit(); return
        self.query_one('#mcp-button', Button).label = 'MCP - ' + ('enabled' if self.mcp.state == 'enabled' else 'disabled')
        if changed: self.refresh_mcp_monitor()
        self.mcp_dirty |= changed
        if self.mcp_dirty and not self.busy:
            select = self.query_one('#profile', Select); old = select.value
            names = self.engine.profiles()
            select.set_options([(name, name) for name in names])
            select.value = old if old in names else (names[0] if names else Select.BLANK)
            await self.refresh_home()
            if self.query_one('#pages', ContentSwitcher).current == 'catalog-page':
                self.load_catalog()
            self.mcp_dirty = False
        if self.mcp_confirmations and not self.mcp_confirming and self.mcp.state == 'enabled':
            message = self.mcp_confirmations.pop(0); self.mcp_confirming = True
            def answer(approved):
                self.mcp_confirming = False
                self.mcp.approve(message['job_id'], approved)
            self.push_screen(UnverifiedDownloadsDialog(message['downloads']), answer)

    def on_unmount(self):
        self.mcp.stop()
