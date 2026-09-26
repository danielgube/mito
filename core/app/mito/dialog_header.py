from .i18n import tr
from textual.containers import Horizontal
from textual.widgets import Button, Static


class DialogHeader(Horizontal):
    DEFAULT_CSS = """
    DialogHeader { height: 3; margin-bottom: 1; align-vertical: middle; }
    DialogHeader .header-spacer { width: 3; }
    DialogHeader .dialog-title { width: 1fr; height: 3; content-align: center middle; text-style: bold; color: #b5dc64; }
    DialogHeader Button.dialog-close { width: 3; min-width: 3; height: 3; margin: 0; padding: 0; border: none; background: transparent; color: #ff7070; text-style: bold; }
    DialogHeader Button.dialog-close:hover, DialogHeader Button.dialog-close:focus { background: #563330; }
    """

    def __init__(self, title, close_id):
        super().__init__()
        self.heading, self.close_id = title, close_id

    def compose(self):
        yield Static('', classes='header-spacer')
        yield Static(self.heading, classes='dialog-title')
        yield Button('×', id=self.close_id, classes='dialog-close', tooltip=tr('Cerrar'))
