from .i18n import tr
import json
import subprocess
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Static
from .dialog_header import DialogHeader
from .localized_ui import ModalScreen
from .storage import YitoError


def choose_path(initial_directory, folder):
    if folder:
        script = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'MITO - Carpeta de configuración'
$dialog.ShowNewFolderButton = $false
$dialog.SelectedPath = [Console]::In.ReadToEnd()
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -InputObject $dialog.SelectedPath -Compress
    }
} finally { $dialog.Dispose() }
"""
        labels = ('MITO - Carpeta de configuración',)
    else:
        script = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'MITO - Archivo de configuración'
$dialog.InitialDirectory = [Console]::In.ReadToEnd()
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -InputObject $dialog.FileName -Compress
    }
} finally { $dialog.Dispose() }
"""
        labels = ('MITO - Archivo de configuración',)
    from .native_ui import localize_script
    script = localize_script(script, *labels)
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', script],
                            input=str(initial_directory), capture_output=True, text=True, encoding='utf-8',
                            check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.stdout.strip() else None


def human_size(value):
    if value is None:
        return tr('no existe')
    if value < 1024:
        return f'{value} B'
    if value < 1024 * 1024:
        return f'{value / 1024:.1f} KiB'
    return f'{value / 1024 / 1024:.1f} MiB'


class FileRow(Horizontal):
    def __init__(self, entry, size):
        super().__init__(classes='file-row')
        self.entry, self.size = entry, size

    def compose(self):
        from .profile_files import label
        yield Static(label(self.entry), classes='file-label', markup=False)
        yield Static(self.size, classes='file-size', markup=False)
        yield Button(tr('Quitar'), classes='file-remove', variant='error')


class ProfileFilesDialog(ModalScreen):
    """Tabla manual de archivos de configuración; quitar nunca borra el archivo."""
    BINDINGS = [('escape', 'cancel', 'Cerrar')]
    DEFAULT_CSS = """
    ProfileFilesDialog { align: center middle; background: #00000088; }
    #profile-files-dialog { width: 95%; max-width: 136; height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #profile-files-context { height: auto; margin-bottom: 1; }
    #profile-files-rows { height: 1fr; min-height: 3; }
    .file-row { height: 3; align-vertical: middle; }
    .file-label { width: 1fr; height: 3; content-align: left middle; }
    .file-size { width: 14; height: 3; content-align: right middle; margin: 0 1; }
    .file-remove { width: 10; min-width: 10; }
    #profile-files-add-row { height: 3; margin-top: 1; }
    #profile-files-path { width: 1fr; margin: 0 1 0 0; }
    #profile-files-add-row Button { width: auto; min-width: 10; margin: 0 1 0 0; }
    #profile-files-error { height: auto; color: #ff7070; margin-top: 1; }
    #profile-files-save { width: 100%; margin-top: 1; }
    """

    def __init__(self, engine, profile):
        super().__init__()
        from .profile_files import entries
        self.engine, self.profile = engine, profile
        self.data = engine.profile(profile)
        self.locks = engine.selected(self.data)
        self.initial = entries(self.data.get('files', []))

    def size_text(self, entry):
        from .profile_files import size
        try:
            return human_size(size(self.engine, self.profile, self.data, self.locks, entry))
        except (OSError, YitoError):
            return '?'

    def compose(self):
        with Vertical(id='profile-files-dialog'):
            yield DialogHeader(tr('ARCHIVOS DE CONFIGURACIÓN'), 'profile-files-close')
            yield Static(tr('Profile: {p1} · Archivos y carpetas que viajan tal cual al exportar. Sólo configuración: máximo 2 MiB y 1000 archivos.', p1=(self.profile)),
                         id='profile-files-context', markup=False)
            with VerticalScroll(id='profile-files-rows'):
                for entry in self.initial:
                    yield FileRow(entry, self.size_text(entry))
            with Horizontal(id='profile-files-add-row'):
                yield Input(placeholder=tr('Ruta de un archivo o carpeta'), id='profile-files-path')
                yield Button(tr('Archivo…'), id='profile-files-browse-file')
                yield Button(tr('Carpeta…'), id='profile-files-browse-folder')
                yield Button(tr('Añadir'), id='profile-files-add')
            yield Static('', id='profile-files-error', markup=False)
            yield Button(tr('Guardar'), id='profile-files-save', variant='primary')

    def current(self):
        return [row.entry for row in self.query(FileRow)]

    def action_cancel(self):
        self.dismiss(False)

    @work(thread=True, exclusive=True, group='file-picker', exit_on_error=False)
    def choose(self, folder):
        app = self.app
        try:
            path, error = choose_path(self.engine.root / 'home', folder), ''
        except (OSError, ValueError, subprocess.SubprocessError):
            path, error = None, tr('No se pudo abrir el selector. Puedes escribir la ruta o reintentar.')
        app.call_from_thread(self.chosen, path, error)

    def chosen(self, path, error):
        if not self.is_mounted:
            return
        for button in self.query('#profile-files-add-row Button'):
            button.disabled = False
        self.query_one('#profile-files-error', Static).update(error)
        if path:
            self.query_one('#profile-files-path', Input).value = path

    async def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        error = self.query_one('#profile-files-error', Static)
        error.update('')
        if action == 'profile-files-close':
            self.action_cancel()
        elif action in ('profile-files-browse-file', 'profile-files-browse-folder'):
            for button in self.query('#profile-files-add-row Button'):
                button.disabled = True
            self.choose(action == 'profile-files-browse-folder')
        elif event.button.has_class('file-remove'):
            await event.button.parent.remove()
        elif action == 'profile-files-add':
            from .profile_files import entries, translate
            field = self.query_one('#profile-files-path', Input)
            if not field.value.strip():
                error.update(tr('Indica una ruta de archivo.'))
                return
            try:
                entry = translate(self.engine, self.profile, field.value)
                entries(self.current() + [entry])
            except (OSError, ValueError, YitoError) as exc:
                error.update(str(exc))
                return
            row = FileRow(entry, self.size_text(entry))
            await self.query_one('#profile-files-rows').mount(row)
            row.scroll_visible(immediate=True)
            field.value = ''
        elif action == 'profile-files-save':
            from .profile_files import save
            try:
                save(self.engine, self.profile, self.current())
            except (YitoError, OSError, ValueError) as exc:
                from .error_tracking import capture
                capture(self.engine.root, exc, operation='profile.files.save')
                error.update(str(exc))
                return
            self.dismiss(True)
