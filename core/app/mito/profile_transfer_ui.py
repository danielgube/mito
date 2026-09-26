from .i18n import tr
import json
import subprocess
from textual import work
from .localized_ui import ModalScreen
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static


def choose_export_file(initial_directory):
    script = r"""
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'MITO - Importar profile'
$dialog.Filter = 'Profiles MITO (*.mito.json;*.json)|*.mito.json;*.json|Todos los archivos (*.*)|*.*'
$dialog.InitialDirectory = [Console]::In.ReadToEnd()
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        ConvertTo-Json -InputObject $dialog.FileName -Compress
    }
} finally { $dialog.Dispose() }
"""
    from .native_ui import localize_script
    script = localize_script(script, 'MITO - Importar profile',
        'Profiles MITO (*.mito.json;*.json)|*.mito.json;*.json|Todos los archivos (*.*)|*.*')
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', script],
                            input=str(initial_directory), capture_output=True, text=True, encoding='utf-8',
                            check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.stdout.strip() else None


class TransferPathDialog(ModalScreen):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    TransferPathDialog { align: center middle; background: #00000088; }
    #transfer-path { width: 86; height: auto; max-height: 85%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #transfer-path Input, #transfer-path Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, root, profile=None):
        super().__init__()
        self.root, self.profile = root, profile

    def compose(self):
        with VerticalScroll(id='transfer-path'):
            yield Static(tr('EXPORTAR PROFILE') if self.profile else tr('IMPORTAR PROFILE'))
            yield Static(tr('Sólo componentes activos. Sin variables, sesiones ni datos de aplicaciones.'))
            value = str(self.root/'exports'/f'{self.profile}.mito.json') if self.profile else ''
            yield Input(value=value, placeholder=tr('Ruta del archivo JSON'), id='transfer-file')
            if not self.profile:
                yield Button(tr('Examinar…'), id='transfer-browse')
                yield Input(placeholder=tr('Nombre del nuevo profile'), id='transfer-name')
                yield Static(tr('Se revisarán las versiones disponibles en tus catálogos antes de instalar.'))
            yield Static('', id='transfer-error', markup=False)
            yield Button(tr('Exportar') if self.profile else tr('Revisar importación'), id='transfer-accept', variant='primary')
            yield Button(tr('Cancelar'), id='transfer-cancel')

    def on_mount(self):
        if not self.profile:
            self.browse()

    def browse(self):
        self.query_one('#transfer-browse', Button).disabled = True
        self.choose_file()

    @work(thread=True, exclusive=True, group='file-picker', exit_on_error=False)
    def choose_file(self):
        app = self.app
        try:
            path = choose_export_file(self.root)
            error = ''
        except (OSError, ValueError, subprocess.SubprocessError):
            path, error = None, tr('No se pudo abrir el selector. Puedes escribir la ruta o reintentar.')
        app.call_from_thread(self.file_selected, path, error)

    def file_selected(self, path, error):
        if not self.is_mounted:
            return
        self.query_one('#transfer-browse', Button).disabled = False
        self.query_one('#transfer-error', Static).update(error)
        if path:
            self.query_one('#transfer-file', Input).value = path
            self.query_one('#transfer-name', Input).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'transfer-browse':
            self.browse()
            return
        if event.button.id == 'transfer-cancel':
            self.action_cancel()
            return
        path = self.query_one('#transfer-file', Input).value.strip().strip('"')
        if not path:
            self.query_one('#transfer-error', Static).update(tr('Indica una ruta de archivo.'))
            return
        name = self.query_one('#transfer-name', Input).value.strip() if not self.profile else self.profile
        if not name:
            self.query_one('#transfer-error', Static).update(tr('Indica el nombre del nuevo profile.'))
            return
        self.dismiss((path, name))


class ImportReviewDialog(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    ImportReviewDialog { align: center middle; background: #00000088; }
    #import-review { width: 90; height: auto; max-height: 85%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #import-review Static { height: auto; margin-bottom: 1; }
    #import-review Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, name, records, installed):
        super().__init__()
        self.profile_name, self.records, self.installed = name, records, installed

    def compose(self):
        with VerticalScroll(id='import-review'):
            yield Static(tr('IMPORTAR EN {p1}', p1=(self.profile_name)), markup=False)
            yield Static(tr('Componentes y dependencias que quedarán activos:'))
            for key, record in self.records.items():
                previous = self.installed.get(key)
                state = tr('Instalar') if previous is None else tr('Ya instalado')
                if previous and previous['manifest_sha256'] != record['manifest_sha256']:
                    state = tr('Sustituir {p1} en todos los profiles que lo usan', p1=(previous['version']))
                yield Static(f'{record["key"]} · {record["version"]} · {state}', markup=False)
            if not self.records:
                yield Static(tr('Se creará un profile vacío.'))
            yield Static(tr('Sin datos ni variables del origen. Se aplicarán tus variables globales locales.'))
            yield Button(tr('Cancelar'), id='import-cancel')
            yield Button(tr('Instalar y crear profile'), id='import-confirm', variant='primary')

    def on_mount(self):
        self.query_one('#import-cancel', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'import-confirm')
