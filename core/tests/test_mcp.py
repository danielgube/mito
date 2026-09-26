import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from mito.engine import Engine
from mito.jobs import Jobs, TERMINAL, command
from mito.mcp_tools import Tools
from mito.mcp_service import MCPService
from mito.storage import mutation, write_json, YitoError


def wait_job(jobs, job_id, timeout=20):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = jobs.get(job_id).snapshot()
        if value['state'] in TERMINAL: return value
        time.sleep(.03)
    raise AssertionError('job did not finish: '+str(value))


class MCPToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name)); self.engine.initialize()
        self.engine.create_profile('default')
        self.jobs = Jobs(self.engine, 'test')
        self.tools = Tools(self.engine, self.jobs)

    def tearDown(self):
        self.jobs.close(); self.tmp.cleanup()

    def test_profiles_revision_and_delete_preserve_workspace(self):
        created = self.tools.call('profile_create', {'profile': 'agent', 'request_id': 'create'})
        self.assertEqual(wait_job(self.jobs, created['job_id'])['state'], 'succeeded')
        again = self.tools.call('profile_create', {'profile': 'agent', 'request_id': 'create'})
        self.assertEqual(again['job_id'], created['job_id'])
        with self.assertRaises(YitoError):
            self.tools.call('profile_create', {'profile': 'other', 'request_id': 'create'})
        value = self.tools.profile_get('agent')
        job = self.tools.profile_configure('agent', value['revision'], {'HELLO': 'world'})
        self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'succeeded')
        stale = self.tools.profile_configure('agent', value['revision'], {'HELLO': 'lost'})
        self.assertEqual(wait_job(self.jobs, stale['job_id'])['state'], 'failed')
        (self.engine.root/'workspaces/agent/keep.txt').write_text('keep')
        deleted = self.tools.profile_delete('agent')
        self.assertEqual(wait_job(self.jobs, deleted['job_id'])['state'], 'succeeded')
        self.assertTrue((self.engine.root/'workspaces/agent/keep.txt').exists())
        last = self.tools.profile_delete('default')
        self.assertEqual(wait_job(self.jobs, last['job_id'])['state'], 'failed')

    def test_command_profile_output_and_nonzero(self):
        data = self.engine.profile('default'); data['env'] = {'EXAMPLE': 'yes'}; self.engine.save_profile('default', data)
        code = "import os,sys; print(os.environ['EXAMPLE']); print(os.environ['APPDATA']); print(os.getcwd()); print('problem',file=sys.stderr)"
        job = self.tools.command_start('default', argv=[sys.executable, '-c', code])
        result = wait_job(self.jobs, job['job_id'])
        self.assertEqual(result['state'], 'succeeded', result)
        out = self.jobs.read(job['job_id'])['text']
        self.assertIn('yes', out); self.assertIn(str(self.engine.root/'home/AppData/Roaming'), out)
        self.assertIn(str(self.engine.root/'workspaces/default'), out)
        self.assertIn('problem', self.jobs.read(job['job_id'], 'stderr')['text'])
        job = self.tools.command_start('default', argv=[sys.executable, '-c', 'raise SystemExit(7)'])
        result = wait_job(self.jobs, job['job_id'])
        self.assertEqual(result['state'], 'failed'); self.assertEqual(result['exit_code'], 7)

    def test_timeout_cancels_child_tree(self):
        marker = self.engine.root/'workspaces/default/pid.txt'
        code = "import subprocess,time,pathlib,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); pathlib.Path('pid.txt').write_text(str(p.pid)); time.sleep(60)"
        job = self.tools.command_start('default', argv=[sys.executable, '-c', code], timeout_seconds=1)
        result = wait_job(self.jobs, job['job_id'])
        self.assertEqual(result['error']['code'], 'COMMAND_TIMEOUT', result)
        from mito.processes import ProcessHandle
        try: handle = ProcessHandle(int(marker.read_text()))
        except OSError: return
        try: self.assertFalse(handle.alive())
        finally: handle.close()

    def test_script_and_argument_literals(self):
        script = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Write-Output 'a & % ! é'; exit 0"
        job = self.tools.command_start('default', script=script, shell='powershell')
        self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'succeeded')
        self.assertIn('a & % ! é', self.jobs.read(job['job_id'])['text'])
        job = self.tools.command_start('default', argv=[sys.executable, '-X', 'utf8', '-c', 'import sys; print(sys.argv[1])', 'a & % ! é'])
        self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'succeeded')
        self.assertIn('a & % ! é', self.jobs.read(job['job_id'])['text'])

    def test_bounds_schema_and_rejected_cwd(self):
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError): self.tools.call('profile_create', {'profile': 'test', 'unexpected': True})
        with self.assertRaises(ValidationError): self.tools.call('command_start', {'profile': 'default', 'argv': 'not a list'})
        job = self.tools.command_start('default', argv=[sys.executable, '-c', 'print(1)'], cwd='..')
        self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'failed')
        job = self.jobs.submit('output', {}, lambda j: j.output('é'*100))
        wait_job(self.jobs, job['job_id'])
        first = self.jobs.read(job['job_id'], limit=5)
        self.assertEqual(first['text'], 'éé'); self.assertEqual(first['next_cursor'], 4)
        self.assertEqual(self.jobs.read(job['job_id'], cursor=2, limit=4)['text'], 'éé')

    def test_batch_argv_preserves_percent_and_metacharacters(self):
        path = self.engine.root/'workspaces/default/tool %PATH% & !.cmd'
        path.write_text('@echo off\nsetlocal DisableDelayedExpansion\n"'+sys.executable+
            '" -X utf8 -c "import sys,json; print(json.dumps(sys.argv[1:]))" %*\n')
        arguments = ['run', '--flag', 'a & %PATH% !', 'two words', 'é', '',
            'a(b)', 'a^b', 'x|y', 'x<y', 'x>y', 'a=b', 'a;b', 'a,b']
        job = self.tools.command_start('default', argv=[str(path), *arguments])
        result = wait_job(self.jobs, job['job_id'])
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual(json.loads(self.jobs.read(job['job_id'])['text']), arguments)

    def test_stop_cancels_commands_and_waiting_confirmation(self):
        command_job = self.tools.command_start('default', argv=[sys.executable, '-c', 'import time; time.sleep(60)'], timeout_seconds=0)
        approval_job = self.jobs.submit('approval', {}, lambda job: job.confirm(['unsigned']))
        time.sleep(.2); self.jobs.stop()
        for job in (command_job, approval_job):
            self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'cancelled')

    def test_output_is_bounded_and_old_jobs_are_not_replayed(self):
        job = self.jobs.submit('output', {}, lambda value: value.output(b'x'*(11*1024*1024)))
        result = wait_job(self.jobs, job['job_id'])
        self.assertTrue(result['output_truncated'])
        self.assertEqual(result['output_bytes']['stdout'], 10*1024*1024)
        folder = self.jobs.folder.parent/('a'*32)/('b'*32)
        write_json(folder/'job.json', {'state': 'running'})
        Jobs._recover(self.jobs.folder.parent)
        self.assertEqual(json.loads((folder/'job.json').read_text())['state'], 'interrupted')

    def test_waiting_approval_does_not_hold_mutation_lock(self):
        job = self.jobs.submit('approval', {}, lambda j: j.confirm(['unsigned dependency']))
        deadline = time.monotonic()+5
        while self.jobs.get(job['job_id']).record['state'] != 'awaiting_confirmation' and time.monotonic()<deadline: time.sleep(.01)
        with mutation(self.engine.root): pass
        self.jobs.approve(job['job_id'], False)
        self.assertEqual(wait_job(self.jobs, job['job_id'])['state'], 'cancelled')

    def test_install_revalidates_after_approval_outside_lock(self):
        folder = self.engine.root/'core/buckets'; folder.mkdir(exist_ok=True)
        manifest = {'version': '1', 'url': 'https://example.com/tool.exe', 'hash': None}
        write_json(folder/'test.json', {'source': 'unused', 'apps': {'tool': manifest}})
        def confirm(downloads):
            with mutation(self.engine.root):
                updated = dict(manifest, version='2')
                write_json(folder/'test.json', {'source': 'unused', 'apps': {'tool': updated}})
            return True
        with patch('mito.components.publish_plan') as publish:
            with self.assertRaisesRegex(YitoError, 'STATE_CONFLICT'):
                self.engine.install('test/tool', confirm_unverified=confirm)
            publish.assert_not_called()

    def test_install_dependency_confirmation_activate_and_run(self):
        import hashlib
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import io
        import zipfile
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w') as archive:
            archive.writestr('fixture.cmd', '@echo off\necho %MCP_FIXTURE%\n')
        data = payload.getvalue(); requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            manifest = {'version': '1.0', 'url': base+'/tool.zip', 'hash': hashlib.sha256(data).hexdigest(),
                'depends': 'test/dependency', 'bin': 'fixture.cmd', 'env_set': {'MCP_FIXTURE': 'installed'}}
            write_json(self.engine.root/'core/buckets/test.json', {'apps': {
                'tool': manifest, 'dependency': {'version': '1.0', 'url': base+'/dependency.zip', 'hash': None}}})
            plan = self.tools.component_plan('test/tool')
            self.assertEqual(len(plan['unverified']), 1)
            for approve in (False, True):
                job = self.tools.component_install('test/tool')
                deadline = time.monotonic()+10
                while self.jobs.get(job['job_id']).snapshot()['state'] != 'awaiting_confirmation' and time.monotonic()<deadline: time.sleep(.03)
                self.assertEqual(self.jobs.get(job['job_id']).snapshot()['state'], 'awaiting_confirmation')
                self.assertEqual(requests, [])
                with mutation(self.engine.root): pass
                self.jobs.approve(job['job_id'], approve)
                result = wait_job(self.jobs, job['job_id'])
                self.assertEqual(result['state'], 'succeeded' if approve else 'cancelled', result)
            self.assertEqual(len(requests), 2)
            self.assertEqual(self.engine.profile('default')['apps'], [])
            activated = self.tools.profile_activate('default', 'test/tool')
            self.assertEqual(wait_job(self.jobs, activated['job_id'])['state'], 'succeeded')
            executed = self.tools.command_start('default', argv=[str(self.engine.root/'core/apps/test/tool/fixture.cmd')])
            result = wait_job(self.jobs, executed['job_id'])
            self.assertEqual(result['state'], 'succeeded', (result, self.jobs.read(executed['job_id'], 'stderr')))
            self.assertIn('installed', self.jobs.read(executed['job_id'])['text'])
        finally:
            server.shutdown(); server.server_close(); thread.join(5)


class MCPHTTPTests(unittest.TestCase):
    def test_occupied_port_and_invalid_saved_settings(self):
        import socket
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize()
            write_json(engine.root/'core/state/mcp/settings.json', {'port': 'wrong'})
            service = MCPService(engine)
            self.assertEqual(service.state, 'disabled'); self.assertTrue(service.error)
            spawned = []
            def launch(engine, pipe, owner, session):
                code = f'from pathlib import Path; from mito.engine import Engine; from mito.mcp_server import run; run(Engine(Path({str(engine.root)!r})), {pipe!r}, {owner}, {session!r})'
                spawned.append(subprocess.Popen([sys.executable, '-B', '-I', '-c', code], creationflags=subprocess.CREATE_NO_WINDOW))
            with socket.socket() as occupied:
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                occupied.bind(('127.0.0.1', 0)); occupied.listen()
                service.start(occupied.getsockname()[1], launch=launch)
                try:
                    service.thread.join(15)
                    self.assertFalse(service.thread.is_alive())
                    self.assertEqual(service.state, 'disabled')
                    self.assertTrue(service.error)
                finally:
                    service.stop(); service.thread.join(5)
                    for process in spawned:
                        if process.poll() is None: process.kill()
                        process.wait()

    def test_startup_cancel_and_instance_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize()
            service = MCPService(engine)
            with mutation(engine.root, 'mcp.lock'):
                service.start(launch=lambda *args: self.fail('must not launch'))
                service.thread.join(5)
                self.assertEqual(service.state, 'disabled')
                self.assertTrue(service.error)
            entered = threading.Event()
            service.start(launch=lambda *args: entered.set())
            self.assertTrue(entered.wait(5))
            service.stop(); service.thread.join(5)
            self.assertFalse(service.thread.is_alive())
            self.assertEqual(service.state, 'disabled')
            with mutation(engine.root, 'mcp.lock'): pass

    def test_owner_crash_kills_server_and_command_descendants(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mito.processes import ProcessHandle
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = '''import os,sys,subprocess,time,json
from pathlib import Path
from mito.engine import Engine
from mito.mcp_service import MCPService
root=Path(sys.argv[1]); engine=Engine(root); engine.initialize(); engine.create_profile('default')
service=MCPService(engine)
def launch(engine,pipe,owner,session):
    code=f'from pathlib import Path; from mito.engine import Engine; from mito.mcp_server import run; run(Engine(Path({str(root)!r})), {pipe!r}, {owner}, {session!r})'
    process=subprocess.Popen([sys.executable,'-B','-I','-c',code],creationflags=subprocess.CREATE_NO_WINDOW)
    (root/'server.pid').write_text(str(process.pid))
service.start(launch=launch)
while service.state=='starting': time.sleep(.05)
(root/'ready.json').write_text(json.dumps({'url':service.url,'error':service.error}))
while True: time.sleep(1)
'''
            owner = subprocess.Popen([sys.executable, '-B', '-I', '-c', script, tmp], creationflags=subprocess.CREATE_NO_WINDOW)
            handles = []
            try:
                deadline = time.monotonic()+35
                while not (root/'ready.json').exists() and time.monotonic()<deadline: time.sleep(.05)
                ready = json.loads((root/'ready.json').read_text())
                self.assertTrue(ready['url'], ready)
                handles.append(ProcessHandle(int((root/'server.pid').read_text())))
                async def check():
                    async with streamable_http_client(ready['url']) as (read, write, _):
                        async with ClientSession(read, write) as client:
                            await client.initialize()
                            code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(60)"
                            result = await client.call_tool('command_start', {'profile': 'default', 'argv': [sys.executable, '-c', code]})
                            self.assertFalse(result.isError, result)
                            deadline = time.monotonic()+10
                            marker = root/'workspaces/default/child.pid'
                            while not marker.exists() and time.monotonic()<deadline: await asyncio.sleep(.05)
                            handles.append(ProcessHandle(int(marker.read_text())))
                asyncio.run(check())
                owner.kill(); owner.wait(5)
                deadline = time.monotonic()+10
                while any(h.alive() for h in handles) and time.monotonic()<deadline: time.sleep(.05)
                self.assertTrue(all(not h.alive() for h in handles))
            finally:
                if owner.poll() is None: owner.kill()
                owner.wait()
                for handle in handles: handle.close()

    def test_real_sdk_http_and_manager_owned_process(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize(); engine.create_profile('default')
            service = MCPService(engine); spawned = []
            def launch(engine, pipe, owner, session):
                code = f'from pathlib import Path; from mito.engine import Engine; from mito.mcp_server import run; raise SystemExit(run(Engine(Path({str(engine.root)!r})), {pipe!r}, {owner}, {session!r}))'
                spawned.append(subprocess.Popen([sys.executable, '-B', '-I', '-c', code], creationflags=subprocess.CREATE_NO_WINDOW))
            self.assertEqual(service.state, 'disabled'); self.assertIsNone(service.thread)
            service.start(launch=launch)
            try:
                deadline = time.monotonic()+35
                while service.state == 'starting' and time.monotonic()<deadline: time.sleep(.1)
                self.assertEqual(service.state, 'enabled', service.error)
                async def check():
                    async with httpx.AsyncClient(trust_env=False) as client:
                        bad = await client.post(service.url, headers={'Origin': 'https://evil.example'}, json={})
                        self.assertEqual(bad.status_code, 403)
                        bad = await client.post(service.url, headers={'Host': 'evil.example'}, json={})
                        self.assertEqual(bad.status_code, 421)
                    async with streamable_http_client(service.url) as (read, write, _):
                        async with ClientSession(read, write) as client:
                            await client.initialize()
                            listed = await client.list_tools()
                            self.assertIn('command_start', [t.name for t in listed.tools])
                            status = await client.call_tool('mito_status', {})
                            self.assertFalse(status.isError)
                            self.assertEqual(status.structuredContent['root'], str(engine.root))
                            result = await client.call_tool('profile_create', {'profile': 'agent'})
                            self.assertFalse(result.isError)
                            deadline = time.monotonic()+10
                            while time.monotonic() < deadline:
                                creation = await client.call_tool('job_get', {'job_id': result.structuredContent['job_id']})
                                if creation.structuredContent['state'] in TERMINAL: break
                                await asyncio.sleep(.05)
                            self.assertEqual(creation.structuredContent['state'], 'succeeded', creation)
                            long_job = await client.call_tool('command_start', {'profile': 'default',
                                'argv': [sys.executable, '-c', 'import time; time.sleep(60)'], 'timeout_seconds': 0})
                            job_id = long_job.structuredContent['job_id']
                            deadline = time.monotonic()+10
                            pid = None
                            while time.monotonic() < deadline:
                                current = await client.call_tool('job_get', {'job_id': job_id})
                                pid = current.structuredContent.get('pid')
                                if pid: break
                                await asyncio.sleep(.05)
                            self.assertIsNotNone(pid, current.structuredContent)
                            from mito.processes import ProcessHandle
                            child = ProcessHandle(pid)
                            try:
                                # Stop with a connected MCP client and a running command.
                                service.stop()
                                await asyncio.to_thread(service.thread.join, 15)
                                self.assertFalse(child.alive())
                            finally: child.close()
                asyncio.run(check())
            finally:
                service.stop()
                service.thread.join(20)
                for process in spawned:
                    if process.poll() is None: process.kill()
                    process.wait()
            self.assertFalse(service.thread.is_alive(), service.error)
            self.assertEqual(service.state, 'disabled')
            self.assertEqual(MCPService(engine).state, 'disabled')


if __name__ == '__main__': unittest.main()
