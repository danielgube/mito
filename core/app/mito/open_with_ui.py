"""Dialog to manage the Windows «Open with» extensions of a component."""
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from .i18n import tr


class OpenWithDialog(ModalScreen[object]):
    """Returns {entry label: [extensions]} for every entry; [] removes it."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    OpenWithDialog { align: center middle; background: $background 70%; }
    #open-with-dialog { width: 76; height: auto; padding: 1 2; border: round #b5dc64; background: #153429; }
    #open-with-dialog Static { height: auto; margin-bottom: 1; }
    #open-with-dialog Horizontal { height: auto; }
    #open-with-dialog Button { margin-left: 1; }
    #open-with-table { height: 8; margin-bottom: 1; }
    #open-with-suggestions { margin-bottom: 1; }
    #open-with-preset, #open-with-common { width: 1fr; }
    #open-with-extensions { width: 1fr; }
    #open-with-table-actions { margin-bottom: 1; }
    #open-with-actions { align-horizontal: right; }
    #open-with-error { color: #ff9090; }
    """

    def __init__(self, name, entries=(), current=None):
        super().__init__()
        self.application_name = name
        self.entries = list(entries)
        current = current or {}
        labels = [label for label, _, _ in self.entries]
        self.values = {label: list(current.get(label, [])) for label in labels}
        self.entry = next((label for label in labels if self.values[label]), labels[0] if labels else None)

    def compose(self):
        from .open_with import COMMON, PRESETS
        with Vertical(id='open-with-dialog'):
            yield Label(tr('Abrir con'), classes='title')
            yield Static(self.application_name, markup=False)
            if len(self.entries) > 1:
                yield Select([(label, label) for label, _, _ in self.entries],
                             value=self.entry, allow_blank=False, id='open-with-entry')
            yield Label(tr('Extensiones registradas'))
            yield DataTable(id='open-with-table', cursor_type='row', zebra_stripes=True, show_header=False)
            with Horizontal(id='open-with-suggestions'):
                yield Select([(f'{name} ({len(values)})', ' '.join(values)) for name, values in PRESETS],
                             prompt=tr('Presets'), allow_blank=True, id='open-with-preset')
                yield Select([(extension, extension) for extension in COMMON], prompt=tr('Habituales'),
                             allow_blank=True, id='open-with-common')
            with Horizontal(id='open-with-table-actions'):
                yield Input(placeholder=tr('Extensiones (por ejemplo: pdf epub)'), id='open-with-extensions')
                yield Button(tr('Añadir'), id='open-with-add')
                yield Button(tr('Quitar'), id='open-with-remove')
            yield Static(tr('Aparecerá en «Abrir con» del menú contextual de esos archivos y se abrirá con el entorno del profile. No cambia la aplicación predeterminada.'), markup=False)
            yield Static('', id='open-with-error', markup=False)
            with Horizontal(id='open-with-actions'):
                yield Button(tr('Cancelar'), id='open-with-cancel')
                yield Button(tr('Guardar'), id='open-with-confirm', variant='primary')

    def on_mount(self):
        self.query_one('#open-with-table', DataTable).add_column('', key='extension')
        self.refresh_table()
        self.query_one('#open-with-extensions', Input).focus()

    def refresh_table(self):
        table = self.query_one('#open-with-table', DataTable)
        table.clear()
        for extension in self.values.get(self.entry, []):
            table.add_row(extension, key=extension)

    def error(self, message=''):
        self.query_one('#open-with-error', Static).update(message)

    def add(self, text):
        from .open_with import extensions
        from .storage import YitoError
        try:
            parsed = extensions(text)
        except YitoError as exc:
            self.error(str(exc))
            return False
        current = self.values.setdefault(self.entry, [])
        current.extend(extension for extension in parsed if extension not in current)
        self.error()
        self.refresh_table()
        return True

    def remove_selected(self):
        table = self.query_one('#open-with-table', DataTable)
        current = self.values.get(self.entry, [])
        if table.row_count and 0 <= table.cursor_row < len(current):
            current.pop(table.cursor_row)
            self.refresh_table()

    def on_select_changed(self, event: Select.Changed):
        event.stop()
        if event.select.id in ('open-with-common', 'open-with-preset'):
            if event.value is not Select.BLANK:
                self.add(event.value)
                event.select.clear()
        elif event.select.id == 'open-with-entry':
            self.entry = event.value
            self.error()
            self.refresh_table()

    def action_cancel(self):
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        if event.value.strip() and self.add(event.value):
            event.input.value = ''

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'open-with-add':
            field = self.query_one('#open-with-extensions', Input)
            if self.add(field.value):
                field.value = ''
        elif event.button.id == 'open-with-remove':
            self.remove_selected()
        elif event.button.id == 'open-with-confirm':
            field = self.query_one('#open-with-extensions', Input)
            # Text left in the field counts as added, so it is not silently lost.
            if field.value.strip() and not self.add(field.value):
                return
            self.dismiss({label: list(values) for label, values in self.values.items()})
        else:
            self.dismiss(None)
