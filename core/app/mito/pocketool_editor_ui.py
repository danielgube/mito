"""Pocketool authoring dialogs. Files are saved before any optional Git operation."""
from pathlib import Path
from rich.text import Text

from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from .dialog_header import DialogHeader
from .i18n import tr
from .localized_ui import ModalScreen
from .storage import YitoError, identifier, inside
from . import pocketool_authoring as author
from .pocketools import sources


class WorkDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    WorkDialog { align: center middle; background: #00000088; }
    .pocketool-panel { width: 90%; max-width: 120; height: auto; max-height: 95%; padding: 1 2; border: round #b5dc64; background: #153429; }
    .pocketool-panel Horizontal { height: auto; }
    .pocketool-panel Button { margin: 0 1 1 0; }
    .pocketool-panel Input, .pocketool-panel Select { margin-bottom: 1; }
    .pocketool-panel Static { height: auto; }
    .pocketool-error { height: auto; max-height: 8; }
    .pocketool-error.error { color: #ff9090; }
    """

    def __init__(self, engine):
        super().__init__()
        self.engine, self.busy = engine, False

    def action_close(self):
        if not self.busy:
            self.dismiss(None)

    def error(self, text):
        field = self.query_one('.pocketool-error', Static)
        field.set_class(bool(text), 'error')
        field.update(text)

    def status(self, text):
        field = self.query_one('.pocketool-error', Static)
        field.remove_class('error')
        field.update(text)

    def perform(self, operation, callback):
        if self.busy:
            return
        self.busy = True
        self.status(tr('Procesando…'))
        self.controls = [(node, node.disabled) for node in self.query('Button, Input, Select, TextArea, Checkbox')]
        for node, _ in self.controls:
            node.disabled = True
        self.background(operation, callback)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def background(self, operation, callback):
        result, error = None, None
        try:
            result = operation()
        except Exception as exc:
            error = str(exc)
            from .error_tracking import capture
            capture(self.engine.root, exc, operation='pocketools.authoring')
        self.app.call_from_thread(self.completed, result, error, callback)

    def completed(self, result, error, callback):
        self.busy = False
        if not self.is_mounted:
            return
        for node, disabled in self.controls:
            node.disabled = disabled
        self.error(error or '')
        if error is None:
            callback(result)


class PackageDialog(WorkDialog):
    def compose(self):
        with VerticalScroll(classes='pocketool-panel'):
            yield DialogHeader(tr('Crear carpeta de Pocketools'), 'package-close')
            yield Input(placeholder=tr('Nombre de la carpeta'), id='package-name')
            yield Static(tr('La carpeta se creará dentro de pocketools de MITO.'))
            yield Static(str(self.engine.root / 'pocketools'), id='package-destination', markup=False)
            yield Input(placeholder=tr('Descripción del paquete'), id='package-info')
            yield Checkbox(tr('Gestionar con Git'), id='package-git')
            yield Input(placeholder=tr('URL Git HTTP/HTTPS'), id='package-url', disabled=True)
            yield Static(tr('El repositorio remoto debe existir. MITO no crea repositorios en el servidor.'))
            yield Static('', classes='pocketool-error', markup=False)
            with Horizontal():
                yield Button(tr('Crear carpeta'), id='package-save', variant='primary')
                yield Button(tr('Cancelar'), id='package-cancel')

    def on_checkbox_changed(self, event):
        if self.is_mounted and event.checkbox.id == 'package-git':
            self.query_one('#package-url', Input).disabled = self.busy or not event.value

    def on_input_changed(self, event):
        if self.is_mounted and event.input.id == 'package-name':
            try:
                name = identifier(event.value.strip())
            except YitoError:
                name = ''
            self.query_one('#package-destination', Static).update(str(self.engine.root / 'pocketools' / name))

    def on_button_pressed(self, event):
        event.stop()
        if self.busy:
            return
        if event.button.id in ('package-close', 'package-cancel'):
            self.action_close()
        elif event.button.id == 'package-save':
            name = self.query_one('#package-name', Input).value.strip()
            description = self.query_one('#package-info', Input).value
            url = self.query_one('#package-url', Input).value.strip()
            git = self.query_one('#package-git', Checkbox).value
            self.perform(lambda: author.create_package(self.engine, name, description, git, url), self.dismiss)


class DiscardDialog(ModalScreen):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    DiscardDialog { align: center middle; background: #00000088; }
    #pocketool-discard { width: 62; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #pocketool-discard Horizontal { height: auto; }
    """

    def compose(self):
        with Vertical(id='pocketool-discard'):
            yield Static(tr('Hay cambios sin guardar. ¿Descartarlos?'))
            with Horizontal():
                yield Button(tr('Cancelar'), id='discard-cancel')
                yield Button(tr('Descartar'), id='discard-confirm', variant='warning')

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'discard-confirm')


class CommandEditor(WorkDialog):
    DEFAULT_CSS = """
    #pocketool-editor { height: 95%; }
    #command-content { height: 1fr; min-height: 6; margin-bottom: 1; }
    #command-name { width: 2fr; }
    #command-kind { width: 1fr; }
    """

    def __init__(self, engine, package, filename=None, content='', revision=None, profile=None):
        super().__init__(engine)
        self.package, self.original, self.revision = package, filename, revision
        self.folder = author.source_folder(engine, package)
        names = engine.profiles()
        self.profile = profile if profile in names else names[0] if names else None
        self.templates = {kind: author.script_template(engine, kind, self.profile) for kind in ('cmd', 'ps1')}
        self.content = content if filename else self.templates['cmd']
        self.initial = (Path(filename).stem if filename else '', Path(filename).suffix[1:].lower() if filename else 'cmd', self.content)
        self.saved = False

    def compose(self):
        with VerticalScroll(classes='pocketool-panel', id='pocketool-editor'):
            yield DialogHeader(tr('Editar comando'), 'command-close')
            yield Static(f'{self.package} · {self.folder}', markup=False)
            with Horizontal():
                yield Input(value=self.initial[0], placeholder=tr('Nombre sin extensión'), id='command-name')
                yield Select([('CMD (.cmd)', 'cmd'), ('PowerShell (.ps1)', 'ps1')], value=self.initial[1], allow_blank=False, id='command-kind')
            yield TextArea(self.content, soft_wrap=False, show_line_numbers=True, tab_behavior='indent', id='command-content')
            yield Static(tr('UTF-8 · CMD: %~dp0 · PowerShell: $PSScriptRoot'), markup=False)
            yield Static('', classes='pocketool-error', markup=False)
            with Horizontal():
                yield Button(tr('Abrir carpeta'), id='command-folder')
                yield Button(tr('Guardar'), id='command-save', variant='primary')
                yield Button(tr('Guardar y revisar Git'), id='command-git', disabled=not author.is_repository(self.folder))
                yield Button(tr('Cerrar'), id='command-close-bottom')

    def values(self):
        return (self.query_one('#command-name', Input).value.strip(),
                self.query_one('#command-kind', Select).value,
                self.query_one('#command-content', TextArea).text)

    def action_close(self):
        if self.busy:
            return
        if self.values() != self.initial:
            def discarded(yes):
                if yes:
                    self.dismiss(self.saved)
            self.app.push_screen(DiscardDialog(), discarded)
        else:
            self.dismiss(self.saved)

    def on_select_changed(self, event):
        if self.is_mounted and event.select.id == 'command-kind' and not self.original:
            editor = self.query_one('#command-content', TextArea)
            if editor.text in self.templates.values():
                editor.load_text(self.templates[event.value])

    def on_button_pressed(self, event):
        event.stop()
        if self.busy:
            return
        action = event.button.id
        if action in ('command-close', 'command-close-bottom'):
            self.action_close()
        elif action == 'command-folder':
            self.perform(lambda: author.open_source(self.engine, self.package), lambda _: None)
        elif action in ('command-save', 'command-git'):
            name, kind, content = self.values()
            def save():
                if author.source_folder(self.engine, self.package) != self.folder:
                    raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
                return author.save_script(self.engine, self.package, name, kind, content, self.original, self.revision,
                                          expected_folder=self.folder)
            self.perform(save, lambda result: self.saved_result(result, action == 'command-git'))

    def saved_result(self, result, git):
        previous = self.original
        self.original, self.revision, self.saved = result['filename'], result['revision'], True
        self.initial = self.values()
        if result['publish_error']:
            self.error(tr('Guardado. La copia de MITO no se pudo actualizar: {p1}', p1=result['publish_error']))
        else:
            self.status(tr('Guardado y disponible en MITO.'))
        if git:
            preferred = {self.original, 'pocketool.json'}
            if previous:
                preferred.add(previous)
            for filename in tuple(preferred):
                if filename.endswith('.ps1'):
                    preferred.add(str(Path(filename).with_suffix('.cmd')))
            self.app.push_screen(GitDialog(self.engine, self.package, preferred))


class GitDialog(WorkDialog):
    DEFAULT_CSS = """
    #pocketool-git { height: 90%; }
    #git-files { height: 1fr; min-height: 5; }
    #git-files Checkbox { width: 100%; }
    """

    def __init__(self, engine, package, preferred=()):
        super().__init__(engine)
        self.package, self.preferred = package, set(preferred)
        self.folder = author.source_folder(engine, package)
        self.reviewed = None

    def compose(self):
        with VerticalScroll(classes='pocketool-panel', id='pocketool-git'):
            yield DialogHeader(tr('Revisar Git'), 'git-close')
            yield Static(str(self.folder), markup=False)
            yield Static('', id='git-branch', markup=False)
            yield Static(tr('Selecciona los archivos del commit. Los demás cambios se conservan.'))
            yield VerticalScroll(id='git-files')
            yield Input(value=f'Pocketools: {self.package}', placeholder=tr('Mensaje del commit'), id='git-message')
            yield Input(placeholder=tr('URL Git HTTP/HTTPS'), id='git-url')
            yield Static(tr('La subida incluye todos los commits pendientes de esta rama.'))
            yield Static('', classes='pocketool-error', markup=False)
            with Horizontal():
                yield Button(tr('Revisar cambios'), id='git-refresh')
                yield Button(tr('Crear commit'), id='git-commit', disabled=True)
                yield Button(tr('Commit y subir'), id='git-push', disabled=True)
                yield Button(tr('Subir commits'), id='git-upload', disabled=True)

    def on_mount(self):
        self.refresh_review()

    def refresh_review(self):
        from .pocketool_git import review
        self.reviewed = None
        for identity in ('git-commit', 'git-push', 'git-upload'):
            self.query_one('#' + identity, Button).disabled = True
        self.perform(lambda: review(self.engine, self.folder), self.show_review)

    def show_review(self, reviewed):
        self.busy = True
        self.query_one('#git-branch', Static).update(tr('Rama: {p1}', p1=reviewed['branch']))
        field = self.query_one('#git-url', Input)
        field.value, field.disabled = reviewed['url'], bool(reviewed['url'])
        self.run_worker(self.mount_changes(reviewed), group='git-view')

    async def mount_changes(self, reviewed):
        listing = self.query_one('#git-files', VerticalScroll)
        await listing.remove_children()
        for index, item in enumerate(reviewed['changes']):
            await listing.mount(Checkbox(Text(f'{item["status"]}  {item["path"]}'),
                value=item['path'] in self.preferred, id=f'git-file-{index}'))
        if not reviewed['changes']:
            await listing.mount(Static(tr('Sin cambios. Puedes reintentar la subida de commits pendientes.')))
        self.reviewed, self.busy = reviewed, False
        self.query_one('#git-commit', Button).disabled = not bool(reviewed['changes'])
        self.query_one('#git-push', Button).disabled = not bool(reviewed['changes'])
        self.query_one('#git-upload', Button).disabled = not bool(reviewed['head'])

    def on_button_pressed(self, event):
        event.stop()
        if self.busy:
            return
        if event.button.id == 'git-close':
            self.action_close()
        elif event.button.id == 'git-refresh':
            self.refresh_review()
        elif event.button.id in ('git-commit', 'git-push', 'git-upload') and self.reviewed is not None:
            from .pocketool_git import commit
            selected = [item['path'] for i, item in enumerate(self.reviewed['changes'])
                        if self.query_one(f'#git-file-{i}', Checkbox).value]
            message = self.query_one('#git-message', Input).value
            url = self.query_one('#git-url', Input).value.strip()
            push = event.button.id != 'git-commit'
            if event.button.id == 'git-upload':
                selected = []
            elif not selected:
                self.error(tr('Selecciona los archivos que quieres incluir en el commit.'))
                return
            self.perform(lambda: commit(self.engine, self.folder, self.reviewed, selected, message, url, push), self.git_done)

    def git_done(self, result):
        self.reviewed = None  # A second click must first re-read HEAD and status.
        self.query_one('#git-commit', Button).disabled = True
        self.query_one('#git-push', Button).disabled = True
        self.query_one('#git-upload', Button).disabled = True
        if result['push_error']:
            self.error(tr('Los archivos y commits locales se conservan. Falló la subida: {p1}', p1=result['push_error']))
        else:
            self.status(tr('Cambios subidos.') if result['pushed'] else tr('Commit creado.'))


class PackageCommandsDialog(WorkDialog):
    DEFAULT_CSS = """
    #commands-location { height: auto; align-vertical: middle; margin-bottom: 1; }
    #commands-path { width: 1fr; margin-right: 2; }
    #commands-folder { margin: 0; }
    #commands-entry { margin-bottom: 1; }
    #commands-profile-label { margin-top: 1; margin-bottom: 1; }
    #commands-profile { margin-bottom: 2; }
    #commands-new { background: #24679a; color: #ffffff; }
    #commands-new:hover { background: #317ca4; }
    """

    def __init__(self, engine, package, profile=None):
        super().__init__(engine)
        self.package, self.profile = package, profile
        self.folder = None
        try:
            self.folder = author.source_folder(engine, package)
        except (YitoError, OSError, ValueError):
            pass

    def compose(self):
        with VerticalScroll(classes='pocketool-panel'):
            yield DialogHeader(self.package, 'commands-close')
            with Horizontal(id='commands-location'):
                yield Static(str(self.folder) if self.folder else sources(self.engine.root)[self.package], id='commands-path', markup=False)
                yield Button(tr('Abrir carpeta'), id='commands-folder', disabled=self.folder is None)
            installed = inside(self.engine.root / 'pocketools', self.package)
            filenames = author.commands(self.folder or installed)
            yield Select([(name, name) for name in filenames], id='commands-entry', prompt=tr('Selecciona un comando'))
            names = self.engine.profiles()
            yield Static(tr('Profile de referencia y acceso directo'), id='commands-profile-label')
            yield Select([(name, name) for name in names], value=self.profile if self.profile in names else Select.NULL,
                         id='commands-profile', prompt=tr('Selecciona un profile'))
            with Horizontal():
                yield Button(tr('Nuevo comando'), id='commands-new', variant='primary', disabled=self.folder is None)
                yield Button(tr('Editar'), id='commands-edit', disabled=True)
                yield Button(tr('Acceso directo'), id='commands-shortcut', disabled=True)
            with Horizontal():
                yield Button(tr('Revisar Git'), id='commands-git', disabled=self.folder is None or not author.is_repository(self.folder))
            if self.folder is None:
                yield Static(tr('Editar copia local conserva los archivos y descarga el historial Git. Después, gestiona las actualizaciones desde Git.'))
                yield Button(tr('Editar copia local'), id='commands-source')
            yield Static('', classes='pocketool-error', markup=False)

    def update_selection(self):
        if not self.is_mounted:
            return
        filename = self.query_one('#commands-entry', Select).value
        selected = isinstance(filename, str) and bool(filename)
        profile = self.query_one('#commands-profile', Select).value
        self.query_one('#commands-edit', Button).disabled = self.busy or not selected or self.folder is None
        self.query_one('#commands-shortcut', Button).disabled = self.busy or not selected or not isinstance(profile, str) or not profile

    def on_mount(self):
        self.update_selection()

    def on_select_changed(self, event):
        if event.select.id in ('commands-entry', 'commands-profile'):
            self.update_selection()

    def completed(self, result, error, callback):
        super().completed(result, error, callback)
        self.update_selection()

    async def refresh_commands(self, result=None):
        if self.query('#commands-profile'):
            selected = self.query_one('#commands-profile', Select).value
            self.profile = selected if isinstance(selected, str) and selected else None
        try:
            self.folder = author.source_folder(self.engine, self.package)
        except (YitoError, OSError, ValueError):
            self.folder = None
        await self.recompose()
        self.update_selection()

    def on_button_pressed(self, event):
        event.stop()
        if self.busy:
            return
        action = event.button.id
        try:
            if action == 'commands-close':
                self.action_close()
            elif action == 'commands-source':
                self.perform(lambda: author.edit_local_copy(self.engine, self.package),
                             lambda _: self.run_worker(self.refresh_commands(), group='commands-view'))
            elif action == 'commands-new':
                selected = self.query_one('#commands-profile', Select).value
                self.app.push_screen(CommandEditor(self.engine, self.package,
                    profile=selected if isinstance(selected, str) else None), self.refresh_commands)
            elif action == 'commands-folder':
                self.perform(lambda: author.open_source(self.engine, self.package), lambda _: None)
            elif action == 'commands-git':
                self.app.push_screen(GitDialog(self.engine, self.package))
            elif action in ('commands-edit', 'commands-shortcut'):
                filename = self.query_one('#commands-entry', Select).value
                if not isinstance(filename, str) or not filename:
                    raise YitoError(tr('Selecciona un comando'))
                if action == 'commands-edit':
                    selected = self.query_one('#commands-profile', Select).value
                    self.perform(lambda: author.read_script(self.engine, self.package, filename),
                        lambda result: self.app.push_screen(CommandEditor(self.engine, self.package, filename, *result,
                            profile=selected if isinstance(selected, str) else None), self.refresh_commands))
                else:
                    profile = self.query_one('#commands-profile', Select).value
                    if not isinstance(profile, str) or not profile:
                        raise YitoError(tr('Selecciona un profile'))
                    from .pocketool_execution import target
                    target(self.engine, self.package, filename)
                    from .ui import ShortcutDialog
                    self.app.push_screen(ShortcutDialog(application=True, engine=self.engine, pocketool=True),
                                         lambda result: self.create_shortcut(profile, filename, result))
        except (YitoError, OSError, ValueError) as exc:
            self.error(str(exc))

    def create_shortcut(self, profile, filename, result):
        if result is None:
            return
        from .pocketool_execution import shortcut
        badge, color, _, arguments = result
        self.perform(lambda: shortcut(self.engine, profile, self.package, filename, badge, color, arguments),
                     lambda path: self.status(tr('Acceso creado: {p1}', p1=path)))
