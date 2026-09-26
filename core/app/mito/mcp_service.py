"""Explicit manager-owned server lifetime; imports no MCP SDK while disabled."""
from contextlib import ExitStack
import json
import os
import queue
import re
import threading
import time
import uuid

from .storage import mutation, read_json, write_json, YitoError


def send(connection, value):
    connection.send_bytes(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def receive(connection):
    value = json.loads(connection.recv_bytes(2*1024*1024).decode('utf-8'))
    if not isinstance(value, dict): raise ValueError('Invalid control message')
    return value


class MCPService:
    def __init__(self, engine):
        self.engine = engine
        self.state = 'disabled'; self.error = ''; self.url = ''; self.jobs = []
        self.events = queue.Queue(); self.connection = None; self.thread = None
        self.send_lock = threading.Lock(); self.stop_requested = threading.Event()
        self.settings_path = engine.root/'core/state/mcp/settings.json'
        self.settings = {'id': uuid.uuid4().hex[:12], 'port': 0}
        if self.settings_path.exists():
            try:
                saved = read_json(self.settings_path)
                if (not isinstance(saved, dict) or not isinstance(saved.get('id'), str)
                        or not re.fullmatch('[a-f0-9]{12}', saved['id'])
                        or type(saved.get('port')) is not int or not 0 <= saved['port'] <= 65535):
                    raise ValueError('Invalid MCP port/identity; enable with a valid port to repair settings')
                self.settings = saved
            except (OSError, ValueError) as exc:
                self.error = str(exc)

    def start(self, port=None, launch=None):
        if self.thread and self.thread.is_alive(): return
        if port is not None:
            if type(port) is not int or not 0 <= port <= 65535: raise ValueError('Invalid port')
            self.settings['port'] = port
        self.stop_requested.clear(); self.error = ''; self.jobs = []; self.url = ''; self.state = 'starting'
        self.events = queue.Queue()
        self.thread = threading.Thread(target=self._run, args=(launch,), name='mito-mcp-control', daemon=True)
        self.thread.start()

    def _run(self, launch):
        from multiprocessing.connection import Client
        from .processes import ProcessJob, ProcessHandle, pipe_client_pid, pipe_listener
        lifetime = ExitStack()
        owner_job = None; listener = None; process = None
        address = r'\\.\pipe\mito-mcp-' + uuid.uuid4().hex
        session = uuid.uuid4().hex
        try:
            lifetime.enter_context(mutation(self.engine.root, 'mcp.lock'))
            owner_job = ProcessJob()
            listener = pipe_listener(address)
            if launch is None:
                from .terminal import launch_mcp
                launch = launch_mcp
            launch(self.engine, address, os.getpid(), session)
            accepted = queue.Queue()
            def accept():
                try: accepted.put(listener.accept())
                except Exception as exc: accepted.put(exc)
            thread = threading.Thread(target=accept, daemon=True); thread.start()
            deadline = time.monotonic()+30
            while thread.is_alive() and time.monotonic() < deadline and not self.stop_requested.wait(.1):
                pass
            if thread.is_alive():
                # Wake the pending accept; no persistent listener remains after a failed launch.
                with Client(address, family='AF_PIPE') as wake: pass
                thread.join(3)
                item = accepted.get(timeout=3)
                if not isinstance(item, Exception): item.close()
                raise YitoError('MCP startup cancelled or timed out')
            connection = accepted.get()
            if isinstance(connection, Exception): raise connection
            self.connection = connection
            pid = pipe_client_pid(connection)
            process = ProcessHandle(pid, assign=True)
            owner_job.assign(process.handle)
            listener.close(); listener = None
            self._send({'type': 'start', 'port': self.settings['port'], 'session': session})
            stop_sent = False
            while process.alive():
                if self.state == 'starting' and time.monotonic() > deadline:
                    raise YitoError('MCP server did not become ready in time')
                if connection.poll(.1):
                    message = receive(connection)
                    if message.get('type') == 'ready':
                        self.settings['port'] = message['port']
                        write_json(self.settings_path, self.settings)
                        self.url = f'http://127.0.0.1:{message["port"]}/mcp'
                        self.state = 'enabled' if not self.stop_requested.is_set() else 'stopping'
                    elif message.get('type') == 'jobs': self.jobs = message['jobs']
                    elif message.get('type') == 'error': self.error = message['message']
                    self.events.put(message)
                if self.stop_requested.is_set() and not stop_sent:
                    self.state = 'stopping'; self._send({'type': 'stop'}); stop_sent = True
        except (EOFError, BrokenPipeError):
            pass
        except Exception as exc:
            self.error = str(exc)
        finally:
            if owner_job: owner_job.close()
            if process: process.close()
            if self.connection:
                self.connection.close(); self.connection = None
            if listener: listener.close()
            lifetime.close()
            self.state = 'disabled'
            self.events.put({'type': 'stopped', 'error': self.error})

    def _send(self, message):
        with self.send_lock:
            try:
                if self.connection: send(self.connection, message)
            except (OSError, EOFError):
                self.stop_requested.set()

    def approve(self, job_id, answer):
        self._send({'type': 'approve', 'job_id': job_id, 'answer': answer is True})

    def cancel(self, job_id):
        self._send({'type': 'cancel', 'job_id': job_id})

    def stop(self):
        self.stop_requested.set()

    def snippets(self, client):
        url = self.url or f'http://127.0.0.1:{self.settings["port"] or "PORT"}/mcp'
        name = 'mito_' + self.settings['id']
        if client == 'Codex': return f'[mcp_servers.{name}]\nurl = "{url}"'
        if client == 'Claude Code': return f'claude mcp add --transport http --scope user {name} {url}'
        if client == 'VS Code': return json.dumps({'servers': {name: {'type': 'http', 'url': url}}}, indent=2)
        return f'Streamable HTTP\n{url}\nAuthentication: none'
