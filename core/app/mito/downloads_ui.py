from .i18n import tr
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static
from .dialog_header import DialogHeader
from .localized_ui import ModalScreen


class InstallModeDialog(ModalScreen):
    """Dismiss: 'now', 'background' o None."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    InstallModeDialog { align: center middle; background: #00000088; }
    #install-mode { width: 72; height: auto; max-height: 85%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #install-mode Static { height: auto; margin-bottom: 1; }
    #install-mode Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, title, now_label):
        super().__init__()
        self.title_text, self.now_label = title, now_label

    def compose(self):
        with VerticalScroll(id='install-mode'):
            yield Static(self.title_text, markup=False)
            yield Static(tr('En segundo plano puedes seguir usando MITO. Cuando termine, instala desde Descargas sin esperar a la red. Si cierras MITO, la descarga continúa al volver a abrirlo.'))
            yield Button(self.now_label, id='install-mode-now', variant='primary')
            yield Button(tr('Descargar en segundo plano'), id='install-mode-background', variant='success')
            yield Button(tr('Cancelar'), id='install-mode-cancel')

    def on_mount(self):
        self.query_one('#install-mode-now', Button).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss({'install-mode-now': 'now', 'install-mode-background': 'background'}.get(event.button.id))


def describe(item):
    status = item['status']
    if status == 'pending':
        return tr('En cola')
    if status == 'downloading':
        progress = item.get('progress') or {}
        text = progress.get('text') or tr('Preparando…')
        received, total = progress.get('received'), progress.get('total')
        if progress.get('unit', 'bytes') == 'bytes' and received is not None:
            done = f'{received / 1048576:.1f} MiB'
            if total:
                return f'{text} · {received * 100 // total}% ({done} / {total / 1048576:.1f} MiB)'
            return f'{text} · {done}'
        return text
    if status == 'done':
        return tr('Descargada; lista para instalar sin esperar a la red')
    if status == 'failed':
        return tr('Error: {p1}', p1=item.get('message', ''))
    return tr('Cancelada')


class DownloadsDialog(ModalScreen):
    """Dismiss: None o (kind, target) para instalar o actualizar lo descargado."""
    BINDINGS = [('escape', 'cancel', 'Cerrar')]
    DEFAULT_CSS = """
    DownloadsDialog { align: center middle; background: #00000088; }
    #downloads-dialog { width: 95%; max-width: 120; height: 80%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #downloads-note { height: auto; margin-bottom: 1; }
    #downloads-rows { height: 1fr; }
    .download-row { height: auto; margin-bottom: 1; }
    .download-text { width: 1fr; height: auto; }
    .download-row Button { width: auto; min-width: 12; margin-left: 1; }
    """

    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.shown = None

    def compose(self):
        with Vertical(id='downloads-dialog'):
            yield DialogHeader(tr('DESCARGAS EN SEGUNDO PLANO'), 'downloads-close')
            yield Static(tr('Se descargan de una en una mientras usas MITO. Instalar o actualizar sigue siendo un paso explícito.'),
                         id='downloads-note')
            yield VerticalScroll(id='downloads-rows')

    async def on_mount(self):
        await self.refresh_rows()
        self.set_interval(1, self.refresh_rows)

    def rows(self, items):
        widgets = []
        for item in items:
            label = tr('Instalar {p1}', p1=item['target']) if item['kind'] == 'install' else tr('Actualizar {p1}', p1=item['target'])
            buttons = []
            if item['status'] in ('pending', 'downloading'):
                buttons.append(Button(tr('Cancelar'), id=f'download-cancel-{item["id"]}', variant='error'))
            elif item['status'] == 'done':
                buttons.append(Button(tr('Instalar') if item['kind'] == 'install' else tr('Actualizar'),
                                      id=f'download-run-{item["id"]}', variant='success'))
                buttons.append(Button(tr('Quitar'), id=f'download-remove-{item["id"]}'))
            else:
                buttons.append(Button(tr('Reintentar'), id=f'download-retry-{item["id"]}'))
                buttons.append(Button(tr('Quitar'), id=f'download-remove-{item["id"]}'))
            widgets.append(Horizontal(Static(f'{label} · {describe(item)}', classes='download-text', markup=False),
                                      *buttons, classes='download-row'))
        if not widgets:
            widgets.append(Static(tr('No hay descargas.')))
        return widgets

    async def refresh_rows(self):
        items = self.queue.snapshot()
        shown = [(item['id'], item['status'], describe(item)) for item in items]
        if shown == self.shown:
            return
        container = self.query_one('#downloads-rows', VerticalScroll)
        structure = [(item_id, status) for item_id, status, _ in shown]
        if self.shown is not None and structure == [(item_id, status) for item_id, status, _ in self.shown]:
            # Only progress changed: update texts without rebuilding buttons (keeps focus).
            for row, item in zip(container.query('.download-row'), items):
                label = tr('Instalar {p1}', p1=item['target']) if item['kind'] == 'install' else tr('Actualizar {p1}', p1=item['target'])
                row.query_one('.download-text', Static).update(f'{label} · {describe(item)}')
        else:
            await container.remove_children()
            await container.mount(*self.rows(items))
        self.shown = shown

    def action_cancel(self):
        self.dismiss(None)

    async def on_button_pressed(self, event):
        event.stop()
        identifier = event.button.id or ''
        if identifier == 'downloads-close':
            self.dismiss(None)
            return
        items = {item['id']: item for item in self.queue.snapshot()}
        for prefix in ('download-cancel-', 'download-run-', 'download-remove-', 'download-retry-'):
            if identifier.startswith(prefix):
                item = items.get(identifier.removeprefix(prefix))
                break
        else:
            return
        if item is None:
            return
        if prefix == 'download-cancel-':
            self.queue.cancel(item['id'])
        elif prefix == 'download-remove-':
            self.queue.remove(item['id'])
        elif prefix == 'download-retry-':
            self.queue.enqueue(item['kind'], item['target'])
        else:
            self.dismiss((item['kind'], item['target']))
            return
        await self.refresh_rows()


class QuitDownloadsDialog(ModalScreen):
    """Dismiss True para salir pausando las descargas."""
    BINDINGS = [('escape', 'cancel', 'Cancelar')]
    DEFAULT_CSS = """
    QuitDownloadsDialog { align: center middle; background: #00000088; }
    #quit-downloads { width: 70; height: auto; padding: 1 2; border: round #FFD166; background: #153429; }
    #quit-downloads Static { height: auto; margin-bottom: 1; }
    #quit-downloads Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, count):
        super().__init__()
        self.count = count

    def compose(self):
        with VerticalScroll(id='quit-downloads'):
            yield Static(tr('Hay {p1} descargas en segundo plano.', p1=self.count), markup=False)
            yield Static(tr('Si sales, se pausan y continúan la próxima vez que abras MITO. No se instala ni se pierde nada.'))
            yield Button(tr('Seguir en MITO'), id='quit-downloads-stay', variant='primary')
            yield Button(tr('Salir y pausar las descargas'), id='quit-downloads-leave', variant='warning')

    def on_mount(self):
        self.query_one('#quit-downloads-stay', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == 'quit-downloads-leave')
