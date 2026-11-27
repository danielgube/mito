"""Opt-in MCP control and client configuration in Textual."""
import json
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Select, Static, TextArea
from .localized_ui import ModalScreen
from .dialog_header import DialogHeader
from .i18n import tr


class MCPDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    MCPDialog { align: center middle; background: $background 70%; }
    #mcp-dialog { width: 90; height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #mcp-body { height: 1fr; }
    #mcp-body Horizontal { height: auto; }
    #mcp-copy-row { align-horizontal: right; }
    #mcp-copy-row #mcp-copy, #mcp-dialog #mcp-close { margin-right: 0; }
    #mcp-dialog Button { margin-right: 1; }
    #mcp-state, #mcp-help { height: auto; margin-bottom: 1; }
    #mcp-config { height: 10; }
    #mcp-port { width: 18; }
    """

    def compose(self):
        with Vertical(id='mcp-dialog'):
            yield DialogHeader('MCP', 'mcp-close')
            with VerticalScroll(id='mcp-body'):
                yield Static('', id='mcp-state', markup=False)
                yield Static(tr('Activa MCP y copia la configuración en el cliente. Puerto 0: automático. Cerrar la pestaña MCP detiene el servidor.'), markup=False)
                with Horizontal():
                    yield Button('enabled', id='mcp-enable', variant='success')
                    yield Button('disabled', id='mcp-disable')
                    yield Input(str(self.app.mcp.settings['port']), id='mcp-port', type='integer', placeholder=tr('Puerto'))
                yield Select([(n, n) for n in ('Codex', 'Claude Code', 'VS Code', 'HTTP')], value='Codex', allow_blank=False, id='mcp-client')
                yield Static('', id='mcp-help', markup=False)
                yield TextArea('', read_only=True, id='mcp-config')
                with Horizontal(id='mcp-copy-row'):
                    yield Button(tr('Copiar configuración'), id='mcp-copy')

    def on_mount(self):
        self._config = None
        self.refresh_state(); self.set_interval(.5, self.refresh_state)

    def refresh_state(self):
        service = self.app.mcp
        self.query_one('#mcp-state', Static).update(f'MCP - {service.state}\n{service.url}\n{service.error}')
        self.query_one('#mcp-enable', Button).disabled = service.state != 'disabled'
        self.query_one('#mcp-disable', Button).disabled = service.state in ('disabled', 'stopping')
        self.query_one('#mcp-port', Input).disabled = service.state != 'disabled'
        client = self.query_one('#mcp-client', Select).value
        snippet = service.snippets(client)
        if snippet != self._config:
            self.query_one('#mcp-config', TextArea).load_text(snippet); self._config = snippet
        hints = {'Codex': '~/.codex/config.toml', 'Claude Code': 'PowerShell / CMD',
                 'VS Code': 'MCP: Open User Configuration', 'HTTP': 'Streamable HTTP'}
        self.query_one('#mcp-help', Static).update(tr('Configuración') + ': ' + hints[client])

    def action_close(self): self.dismiss()

    def on_button_pressed(self, event):
        action = event.button.id
        event.stop()
        if action == 'mcp-close': self.action_close()
        elif action == 'mcp-enable':
            try: self.app.start_mcp(int(self.query_one('#mcp-port', Input).value))
            except (ValueError, OSError) as exc:
                from .error_tracking import capture
                capture(self.app.engine.root, exc, operation='mcp.start')
                self.app.notify(str(exc), severity='error')
        elif action == 'mcp-disable': self.app.mcp.stop()
        elif action == 'mcp-copy': self.app.copy_log(self._config or '')


class MCPJobsDialog(ModalScreen):
    BINDINGS = [('escape', 'close', 'Cerrar')]
    DEFAULT_CSS = """
    MCPJobsDialog { align: center middle; background: $background 70%; }
    #mcp-jobs-dialog { width: 90; height: 90%; padding: 1 2; border: round #b5dc64; background: #153429; }
    #mcp-jobs-body { height: 1fr; }
    #mcp-jobs-body Horizontal { height: auto; }
    #mcp-jobs-body Button { margin-right: 1; }
    #mcp-output { height: 1fr; min-height: 8; }
    """

    def compose(self):
        with Vertical(id='mcp-jobs-dialog'):
            yield DialogHeader('MCP · Jobs', 'mcp-jobs-close')
            with VerticalScroll(id='mcp-jobs-body'):
                yield Select([], prompt='job_id', id='mcp-job')
                with Horizontal():
                    yield Button(tr('Actualizar'), id='mcp-read')
                    yield Button(tr('Cancelar'), id='mcp-cancel-job')
                    yield Select([('stdout', 'stdout'), ('stderr', 'stderr')], value='stdout', allow_blank=False, id='mcp-stream')
                yield TextArea('', read_only=True, id='mcp-output')

    def on_mount(self):
        self._jobs = None
        self.refresh_state(); self.set_interval(.5, self.refresh_state)

    def refresh_state(self):
        service = self.app.mcp
        for button in self.query('#mcp-read, #mcp-cancel-job'):
            button.disabled = service.state != 'enabled'
        options = [(f'{j["job_id"][:8]} · {j["name"]} · {j["profile"] or "—"} · {j["state"]}', j['job_id']) for j in service.jobs]
        if options != self._jobs:
            select = self.query_one('#mcp-job', Select); old = select.value
            select.set_options(options)
            if old in [v for _, v in options]: select.value = old
            self._jobs = options

    def action_close(self): self.dismiss()

    def on_select_changed(self, event):
        if event.select.id in ('mcp-job', 'mcp-stream'):
            selection = (self.query_one('#mcp-job', Select).value, self.query_one('#mcp-stream', Select).value)
            if selection != getattr(self, '_selection', None):
                self._selection = selection
                self.query_one('#mcp-output', TextArea).load_text('')

    def on_button_pressed(self, event):
        action = event.button.id
        event.stop()
        if action == 'mcp-jobs-close': self.action_close()
        elif action in ('mcp-read', 'mcp-cancel-job'):
            job_id = self.query_one('#mcp-job', Select).value
            if job_id is Select.NULL: return
            if action == 'mcp-cancel-job': self.app.mcp.cancel(job_id)
            else:
                self.app.mcp._send({'type': 'output', 'job_id': job_id,
                    'stream': self.query_one('#mcp-stream', Select).value})

    def show_output(self, message):
        if (message.get('job', {}).get('job_id') != self.query_one('#mcp-job', Select).value
                or message.get('output', {}).get('stream') != self.query_one('#mcp-stream', Select).value):
            return
        self.query_one('#mcp-output', TextArea).load_text(
            json.dumps(message.get('job'), ensure_ascii=False, indent=2)+'\n\n'+message.get('output', {}).get('text', ''))
