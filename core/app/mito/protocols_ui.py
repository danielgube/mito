"""Dialog that claims URL schemes for a component while it stays open."""
import time

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from .i18n import tr

WATCH_SECONDS = 5


class ProtocolDialog(ModalScreen[None]):
    """session is a protocols.Session (describe, claim, check, release).

    A claimed scheme is re-asserted every WATCH_SECONDS while the dialog is
    open; closing the dialog releases every claim made from it."""
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    ProtocolDialog { align: center middle; background: $background 70%; }
    #protocol-dialog { width: 90; height: auto; max-height: 100%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #protocol-dialog Static { height: auto; }
    #protocol-dialog Horizontal { height: auto; }
    #protocol-dialog Button { margin-left: 1; }
    #protocol-rows { height: auto; max-height: 16; margin: 1 0; }
    .protocol-row { height: auto; margin-bottom: 1; }
    .protocol-name { width: 1fr; }
    .protocol-target { color: #a8c0b0; margin-left: 2; }
    .protocol-claimed { color: #b5dc64; margin-left: 2; }
    .protocol-toggle { width: 14; min-width: 14; }
    #protocol-scheme { width: 1fr; }
    #protocol-help { margin-bottom: 1; }
    #protocol-actions { align-horizontal: right; }
    #protocol-error { color: #ff9090; }
    """

    def __init__(self, name, entries=(), rows=(), session=None, watch=WATCH_SECONDS):
        super().__init__()
        self.application_name = name
        self.entries = list(entries)
        self.rows = {row['scheme']: dict(row) for row in rows}
        self.session = session
        self.watch_seconds = watch
        self.notes = {}
        labels = [label for label, _, _ in self.entries]
        self.entry = labels[0] if labels else None

    def compose(self):
        with Vertical(id='protocol-dialog'):
            yield Label(tr('Reclamar protocolo'), classes='title')
            yield Static(self.application_name, markup=False)
            if len(self.entries) > 1:
                yield Select([(label, label) for label, _, _ in self.entries],
                             value=self.entry, allow_blank=False, id='protocol-entry')
            yield VerticalScroll(id='protocol-rows')
            with Horizontal():
                yield Input(placeholder=tr('Esquema (por ejemplo: claude)'), id='protocol-scheme')
                yield Button(tr('Añadir'), id='protocol-add')
            yield Static(tr('Pulsa Reclamar: mientras este diálogo esté abierto, los enlaces del esquema, como el retorno de un inicio de sesión en el navegador, abrirán el componente con el entorno del profile. MITO lo comprueba cada 5 s y lo recupera si otra app lo sobrescribe. Al cerrar se restaura el registro anterior.'),
                         id='protocol-help', markup=False)
            yield Static('', id='protocol-error', markup=False)
            with Horizontal(id='protocol-actions'):
                yield Button(tr('Cerrar'), id='protocol-close', variant='primary')

    async def on_mount(self):
        await self.refresh_rows()
        self.set_interval(self.watch_seconds, self.watch)
        self.query_one('#protocol-scheme', Input).focus()

    def claimed(self, scheme):
        return self.session is not None and scheme in self.session.schemes

    def button_id(self, scheme):
        return 'protocol-toggle-' + ''.join(char if char.isalnum() else '_' for char in scheme)

    async def refresh_rows(self):
        from .protocols import label
        container = self.query_one('#protocol-rows', VerticalScroll)
        await container.remove_children()
        widgets = []
        for scheme in sorted(self.rows):
            row = self.rows[scheme]
            claimed = self.claimed(scheme)
            toggle = Button(tr('Liberar') if claimed else tr('Reclamar'), id=self.button_id(scheme), name=scheme,
                            classes='protocol-toggle', variant='success' if claimed else 'default')
            lines = [Static(row.get('command') or '—', classes='protocol-target', markup=False)]
            if claimed:
                lines.append(Static(self.notes.get(scheme) or tr('Reclamado: se vigila cada 5 s mientras este diálogo siga abierto.'),
                                    classes='protocol-claimed', markup=False))
            widgets.append(Vertical(Horizontal(Static(f'{scheme}://  ·  {label(row["status"])}', classes='protocol-name',
                                                      markup=False), toggle),
                                    *lines, classes='protocol-row'))
        if not widgets:
            widgets.append(Static(tr('No se ha detectado ningún esquema; escríbelo abajo.'), markup=False))
        await container.mount(*widgets)

    def error(self, message=''):
        self.query_one('#protocol-error', Static).update(message)

    async def add(self, text):
        from .storage import YitoError
        try:
            row = self.session.describe(text)
        except YitoError as exc:
            self.error(str(exc))
            return False
        self.rows[row['scheme']] = row
        self.error()
        await self.refresh_rows()
        return True

    async def toggle(self, scheme):
        from .storage import YitoError
        try:
            if self.claimed(scheme):
                self.session.release(scheme)
                self.notes.pop(scheme, None)
                self.rows[scheme] = self.session.describe(scheme)
            else:
                self.rows[scheme] = self.session.claim(scheme, self.entry)
        except (YitoError, OSError, ValueError) as exc:
            self.error(str(exc))
            return
        self.error()
        await self.refresh_rows()

    async def watch(self):
        """Re-assert claimed schemes that another application overwrote."""
        from .storage import YitoError
        if self.session is None:
            return
        changed = False
        for scheme in list(self.session.schemes):
            try:
                if self.session.check(scheme):
                    self.notes[scheme] = tr('Recuperado a las {p1}: otra app lo había sobrescrito.',
                                            p1=time.strftime('%H:%M:%S'))
                    changed = True
            except (YitoError, OSError, ValueError) as exc:
                self.error(str(exc))
        if changed:
            await self.refresh_rows()

    def on_select_changed(self, event: Select.Changed):
        event.stop()
        if event.select.id == 'protocol-entry':
            self.entry = event.value

    def close(self):
        if self.session is not None:
            self.session.release()
        self.dismiss(None)

    def action_close(self):
        self.close()

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        if event.value.strip() and await self.add(event.value):
            event.input.value = ''

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'protocol-add':
            field = self.query_one('#protocol-scheme', Input)
            if await self.add(field.value):
                field.value = ''
        elif event.button.id == 'protocol-close':
            self.close()
        elif event.button.name in self.rows:
            await self.toggle(event.button.name)
