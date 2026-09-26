"""Bounded command and operation jobs for one explicit MCP session."""
import codecs
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .storage import YitoError, inside, write_json
from .processes import ProcessJob

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


def error_info(exc):
    prefix = str(exc).partition(':')[0]
    code = prefix if re.fullmatch('[A-Z][A-Z_]{2,60}', prefix) else 'OPERATION_FAILED'
    return {'code': code, 'message': str(exc)}


def batch_command(target, arguments, environment):
    """Expand data once, quoting only where CMD requires it for arguments."""
    values = [str(target), *arguments]
    if any(any(c in value for c in ('"', '\r', '\n', '\0')) for value in values):
        raise YitoError('INVALID_ARGUMENT: BAT/CMD argv cannot contain quotes or newlines; use an explicit script')
    references = []
    for index, value in enumerate(values):
        key = f'MITO_COMMAND_ARG_{index}'
        environment[key] = value
        # BAT files see quotes in %1 (unlike native argv). Simple tokens must
        # remain bare for scripts that compare %1 without stripping quotes.
        # CMD treats an empty environment value as undefined and may leave
        # its reference literal in /c; an empty argument needs literal quotes.
        if not value:
            references.append('""')
            continue
        reference = f'%{key}%'
        references.append(reference if index and re.fullmatch(r'[A-Za-z0-9_./:\\+-]+', value)
            else f'"{reference}"')
    executable = str(Path(os.environ['SystemRoot'])/'System32/cmd.exe')
    return f'"{executable}" /d /v:off /s /c "' + ' '.join(references) + '"'


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, registry, name, arguments, request_id):
        self.registry = registry
        self.cancelled = threading.Event()
        self.approved = threading.Event()
        self.answer = False
        self.lock = threading.RLock()
        self.record = dict(job_id=uuid.uuid4().hex, name=name, profile=arguments.get('profile'),
            request_id=request_id, signature=hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest(),
            state='queued', created=time.time(), exit_code=None, error=None, cancel_requested=False)
        self.folder = registry.folder / self.record['job_id']; self.folder.mkdir()
        self.sizes = {'stdout': 0, 'stderr': 0}; self.truncated = False
        self.save()

    def save(self):
        with self.lock:
            write_json(self.folder/'job.json', self.record)

    def snapshot(self):
        with self.lock:
            return dict(self.record, output_bytes=dict(self.sizes), output_truncated=self.truncated)

    def state(self, state, **values):
        with self.lock:
            self.record.update(state=state, **values); self.save()
        self.registry.notify({'type': 'jobs', 'jobs': self.registry.list()})

    def output(self, data, stream='stdout'):
        if isinstance(data, str):
            data = data.encode('utf-8', 'replace')
        with self.lock:
            room = max(0, 10*1024*1024 - sum(self.sizes.values()))
            with self.registry.output_lock:
                room = min(room, max(0, 64*1024*1024-self.registry.output_size))
                self.registry.output_size += min(room, len(data))
            self.truncated |= len(data) > room
            with (self.folder/stream).open('ab') as dest:
                dest.write(data[:room])
            self.sizes[stream] += min(room, len(data))

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def confirm(self, downloads):
        self.check()
        self.state('awaiting_confirmation', downloads=downloads)
        self.registry.notify({'type': 'confirm', 'job_id': self.record['job_id'], 'downloads': downloads})
        while not self.approved.wait(.2):
            self.check()
        self.check()
        if not self.answer:
            raise Cancelled()
        self.state('running', downloads=[])
        return True


class Jobs:
    def __init__(self, engine, session, notify=lambda message: None):
        self.engine, self.notify = engine, notify
        self.folder = engine.root/'core/state/mcp/jobs'/session
        self._recover(self.folder.parent)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock(); self.items = {}; self.requests = {}; self.stopping = False
        self.output_lock = threading.Lock(); self.output_size = 0
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mito-mcp-job')

    @staticmethod
    def _recover(parent):
        if not parent.exists(): return
        folders = sorted([p for p in parent.iterdir() if re.fullmatch('[a-f0-9]{32}', p.name)
            and p.is_dir() and not p.is_symlink() and not p.is_junction()], key=lambda p: p.stat().st_mtime, reverse=True)
        for index, folder in enumerate(folders):
            if index >= 2 or time.time()-folder.stat().st_mtime > 7*86400:
                # Only generated session directories below this instance's state.
                if folder.resolve().parent == parent.resolve(): shutil.rmtree(folder)
                continue
            for path in folder.glob('*/job.json'):
                if not path.resolve().is_relative_to(folder.resolve()): continue
                try:
                    value = json.loads(path.read_text(encoding='utf-8'))
                    if value.get('state') not in TERMINAL:
                        value.update(state='interrupted', ended=time.time())
                        write_json(path, value)
                except (OSError, ValueError): pass

    def list(self):
        with self.lock:
            return [job.snapshot() for job in self.items.values()]

    def get(self, job_id):
        with self.lock:
            if job_id not in self.items:
                raise YitoError('JOB_NOT_FOUND: ' + job_id)
            return self.items[job_id]

    def submit(self, name, arguments, action):
        request_id = arguments.get('request_id')
        signature = hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.stopping:
                raise YitoError('SERVER_STOPPING')
            if request_id and request_id in self.requests:
                job = self.requests[request_id]
                if job.record['signature'] != signature:
                    raise YitoError('STATE_CONFLICT: request_id reused with different arguments')
                return job.snapshot()
            if len(self.items) >= 128:
                raise YitoError('SESSION_LIMIT: restart MCP after reviewing completed jobs (128 per session)')
            if sum(j.record['state'] not in TERMINAL for j in self.items.values()) >= 32:
                raise YitoError('INSTANCE_BUSY: job queue full')
            job = Job(self, name, arguments, request_id)
            self.items[job.record['job_id']] = job
            if request_id:
                self.requests[request_id] = job
            self.pool.submit(self._run, job, action)
            return job.snapshot()

    def _run(self, job, action):
        try:
            job.check(); job.state('running', started=time.time())
            result = action(job)
            job.state('succeeded', result=result, ended=time.time())
        except Cancelled:
            job.state('cancelled', ended=time.time())
        except Exception as exc:
            from .error_tracking import capture
            capture(self.engine.root, exc, operation='mcp.job')
            job.state('failed', error=error_info(exc), ended=time.time())
        finally:
            self.notify({'type': 'changed'})

    def cancel(self, job_id):
        job = self.get(job_id)
        with job.lock:
            if job.record['state'] not in TERMINAL:
                job.cancelled.set()
                job.record.update(state='cancelling', cancel_requested=True)
                job.save()
        return job.snapshot()

    def approve(self, job_id, answer):
        job = self.get(job_id)
        with job.lock:
            if job.record['state'] == 'awaiting_confirmation':
                job.answer = answer is True; job.approved.set()

    def read(self, job_id, stream='stdout', cursor=0, limit=65536):
        job = self.get(job_id)
        if stream not in ('stdout', 'stderr') or cursor < 0 or not 4 <= limit <= 65536:
            raise YitoError('INVALID_ARGUMENT: output range')
        with job.lock:
            if cursor > job.sizes[stream]:
                raise YitoError('INVALID_ARGUMENT: cursor beyond output')
            path = job.folder/stream
            if path.exists():
                with path.open('rb') as src:
                    src.seek(cursor); data = src.read(limit)
                # Do not split a UTF-8 character when more bytes remain.
                decoder = codecs.getincrementaldecoder('utf-8')('replace')
                text = decoder.decode(data, final=cursor+len(data) == job.sizes[stream])
                used = len(data) - len(decoder.getstate()[0])
            else:
                text, used = '', 0
            return {'text': text, 'next_cursor': cursor+used, 'stream': stream,
                'truncated': job.truncated, 'state': job.record['state']}

    def stop(self):
        self.stopping = True
        for snapshot in self.list():
            self.cancel(snapshot['job_id'])

    def close(self):
        self.stop(); self.pool.shutdown(wait=True)


def command(engine, job, *, profile, argv=None, script=None, shell=None, cwd='.',
            timeout_seconds=300, encoding='utf-8', stdin=None, **unused):
    if (argv is None) == (script is None):
        raise YitoError('INVALID_ARGUMENT: provide exactly one of argv or script')
    if not 0 <= timeout_seconds <= 86400:
        raise YitoError('INVALID_ARGUMENT: timeout_seconds must be 0..86400')
    codecs.lookup(encoding)
    env, workspace = engine.execution_context(profile)
    directory = inside(workspace, cwd)
    if not directory.is_dir():
        raise YitoError('INVALID_ARGUMENT: cwd must exist within workspace')
    powershell = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    if script is not None:
        if shell not in ('cmd', 'powershell'):
            raise YitoError('INVALID_ARGUMENT: script requires cmd or powershell')
        path = job.folder/('command.ps1' if shell == 'powershell' else 'command.cmd')
        if shell == 'powershell' and codecs.lookup(encoding).name == 'utf-8':
            script = '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $OutputEncoding = [Console]::OutputEncoding\n' + script
        path.write_text(script, encoding='utf-8-sig' if shell == 'powershell' else encoding)
        argv = [powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(path)] if shell == 'powershell' else batch_command(path, [], env)
    else:
        if not argv or not all(isinstance(a, str) and '\0' not in a for a in argv):
            raise YitoError('INVALID_ARGUMENT: argv must contain strings')
        target = shutil.which(argv[0], path=env['PATH']) or argv[0]
        suffix = Path(target).suffix.lower()
        if suffix in ('.cmd', '.bat'):
            argv = batch_command(target, argv[1:], env)
        elif suffix == '.ps1':
            argv = [powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', target, *argv[1:]]
        else:
            argv = [target, *argv[1:]]
    job.check(); group = ProcessJob()
    try:
        process = group.spawn(argv, cwd=directory, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        job.record['pid'] = process.pid; job.save()
        def drain(pipe, stream):
            decoder = codecs.getincrementaldecoder(encoding)('replace')
            with pipe:
                while data := pipe.read1(8192):
                    job.output(decoder.decode(data), stream)
                job.output(decoder.decode(b'', final=True), stream)
        readers = [threading.Thread(target=drain, args=(pipe, name), daemon=True)
            for pipe, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr'))]
        for thread in readers: thread.start()
        if stdin is not None:
            def feed():
                try:
                    process.stdin.write(stdin.encode(encoding)); process.stdin.flush()
                except (OSError, ValueError): pass
                finally: process.stdin.close()
            threading.Thread(target=feed, daemon=True).start()
        started = time.monotonic(); timed_out = False
        while process.poll() is None:
            if job.cancelled.wait(.05) or (timeout_seconds and time.monotonic()-started >= timeout_seconds):
                timed_out = not job.cancelled.is_set(); group.terminate(); break
        process.wait(); group.close()
        for thread in readers: thread.join(5)
        job.record['exit_code'] = process.returncode
        if timed_out: raise YitoError('COMMAND_TIMEOUT: command terminated')
        job.check()
        if process.returncode:
            raise YitoError(f'COMMAND_FAILED: exit code {process.returncode}')
        return {'exit_code': process.returncode}
    finally:
        group.close()
