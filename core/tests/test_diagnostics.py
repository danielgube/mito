import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from mito import diagnostics

# Temporary roots are usually longer than the portable limit; these tests cover
# other bootstrap phases, so they raise the threshold (test_path_limit covers it).
TEMPORARY_ROOT = dict(os.environ, MITO_ROOT_PATH_LIMIT='1000')


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_rotation_keeps_three_backups_and_valid_json_lines(self):
        with patch.object(diagnostics, 'MAX_BYTES', 600):
            for index in range(12):
                diagnostics.write(self.root, 'error', 'test', str(index) + 'x' * 250)
        folder = self.root / 'logs'
        self.assertEqual(len(list(folder.glob('mito.log*'))), 4)
        for path in folder.glob('mito.log*'):
            self.assertLessEqual(path.stat().st_size, 600)
            for line in path.read_text().splitlines():
                self.assertEqual(json.loads(line)['operation'], 'test')
        self.assertTrue(json.loads((folder / 'mito.log').read_text().splitlines()[-1])['message'].startswith('11'))

    def test_concurrent_writers_keep_complete_records(self):
        workers = [threading.Thread(target=lambda: [diagnostics.write(self.root, 'error', 'thread', 'entry') for _ in range(20)]) for _ in range(4)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        lines = (self.root / 'logs/mito.log').read_text().splitlines()
        self.assertEqual(len(lines), 80)
        self.assertTrue(all(json.loads(line)['message'] == 'entry' for line in lines))

    def test_fallback_and_failure_do_not_raise(self):
        blocked = self.root / 'blocked'
        blocked.write_text('file, not directory')
        fallback = self.root / 'fallback'
        with patch.object(diagnostics, 'locations', return_value=(blocked, fallback)), patch('sys.stderr'):
            self.assertEqual(diagnostics.write(self.root, 'error', 'test', 'message'), fallback / 'mito.log')
        with patch.object(diagnostics, 'locations', return_value=(blocked, blocked)), patch('sys.stderr'):
            self.assertIsNone(diagnostics.write(self.root, 'error', 'test', 'message'))

    def test_scrub_keeps_explanation_but_removes_known_secrets(self):
        text = diagnostics.scrub(self.root,
            'Download failed https://user:password@example.com/path?token=SECRET '
            'password="two words" Authorization: Bearer SECRET phc_EXAMPLE '
            + str(self.root / 'file') + ' C:/Users/Someone/file')
        for secret in ('SECRET', 'two words', 'user:password', 'phc_EXAMPLE', 'Someone', str(self.root)):
            self.assertNotIn(secret, text)
        self.assertIn('Download failed', text)
        self.assertIn('example.com/path', text)

    def test_subprocess_error_does_not_log_command_arguments(self):
        exc = subprocess.CalledProcessError(7, ['tool', '--password', 'secret'])
        self.assertEqual(diagnostics.message(self.root, exc), 'Child process failed with exit code 7')

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_bootstrap_welcome_requires_explicit_yes(self):
        source = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'
        functions = source.read_text(encoding='utf-8-sig').split("$ErrorActionPreference = 'Stop'", 1)[0]
        script = self.root / 'welcome-check.ps1'
        # Fijar la salida del proceso hijo a UTF-8: con -X utf8 el padre decodifica
        # la salida capturada como UTF-8 y la codepage legacy de la consola rompería el hilo lector.
        script.write_text('[Console]::OutputEncoding = New-Object Text.UTF8Encoding' + "\n" + functions + '''
$manifest = @{tools=@(@{id='7zip';version='1'},@{id='windows-terminal';version='2'})}
foreach ($response in @('', 'n', 'yes', 's', 'si', 'sí', ' S ')) {
    function Read-Host { return $response }
    $accepted = Confirm-YitoBootstrap $manifest
    $expected = $response.Trim().ToLowerInvariant() -in @('s','si','sí')
    if ($accepted -ne $expected) { throw ('Incorrect consent: ' + $response) }
}
''', encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('My Integrated Terminal Orchestrator', result.stdout)
        self.assertIn('Windows Terminal', result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_bootstrap_cancellation_creates_no_tools_or_pending_events(self):
        import shutil
        source = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'
        core = self.root / 'core'
        core.mkdir()
        shutil.copy2(source, core / 'bootstrap.ps1')
        (core / 'bootstrap.lock.json').write_text('{"tools": []}')
        (self.root / 'config.properties').write_text('posthog.enabled=false')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(core / 'bootstrap.ps1')],
                                capture_output=True, text=True, timeout=20, env=TEMPORARY_ROOT)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((core / 'tools').exists())
        self.assertFalse((self.root / 'logs/pending').exists())
        record = json.loads((self.root / 'logs/mito.log').read_text().splitlines()[-1])
        self.assertEqual(record['phase'], 'confirmation')
        self.assertEqual(record['level'], 'info')

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_python_retries_powershell_pending_event_without_real_network(self):
        from mito import error_tracking as tracking
        tracking.flush()
        tracking._attempted.clear()
        (self.root / 'config.properties').write_text(
            'posthog.enabled=true\nposthog.host=https://eu.i.posthog.com\nposthog.api_key=phc_fixture')
        source = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'
        functions = source.read_text(encoding='utf-8-sig').split("$ErrorActionPreference = 'Stop'", 1)[0]
        script = self.root / 'queue-fixture.ps1'
        root = str(self.root).replace("'", "''")
        script.write_text(functions + f"\nInitialize-YitoDiagnostics '{root}'\n"
            "try { throw 'offline fixture' } catch { Save-YitoBootstrapError $_ 'download-python' }\n",
            encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        pending = list((self.root / 'logs/pending').glob('*.json'))
        self.assertEqual(len(pending), 1)
        with patch.object(tracking, '_send', return_value=200) as send:
            tracking.resume(self.root); tracking.flush()
        send.assert_called_once()
        props = send.call_args.args[3]['properties']
        self.assertEqual(props['mito_phase'], 'download-python')
        self.assertEqual(props['$exception_list'][0]['value'], 'offline fixture')
        self.assertFalse(pending[0].exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_real_bootstrap_records_failure_before_python_exists(self):
        import shutil
        source = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'
        core = self.root / 'core'
        core.mkdir()
        shutil.copy2(source, core / 'bootstrap.ps1')
        (core / 'bootstrap.lock.json').write_text('invalid JSON')
        (self.root / 'config.properties').write_text('posthog.enabled=false')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(core / 'bootstrap.ps1')],
                                capture_output=True, text=True, timeout=20, env=TEMPORARY_ROOT)
        self.assertEqual(result.returncode, 1)
        records = [json.loads(line) for line in (self.root / 'logs/mito.log').read_text(encoding='utf-8-sig').splitlines()]
        self.assertEqual(records[-1]['phase'], 'read-bootstrap-lock')
        self.assertEqual(records[-1]['level'], 'error')
        self.assertFalse((core / 'tools/python/python.exe').exists())
        self.assertFalse((self.root / 'logs/pending').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
    def test_powershell_bootstrap_log_without_python_or_network(self):
        source = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'
        functions = source.read_text(encoding='utf-8-sig').split("$ErrorActionPreference = 'Stop'", 1)[0]
        # Load only definitions. Never run the real bootstrap or send telemetry.
        script = self.root / 'bootstrap-log-check.ps1'
        root = str(self.root).replace("'", "''")
        script.write_text(functions + f"\nInitialize-YitoDiagnostics '{root}'\n"
            "Write-YitoDiagnostic 'info' 'startup' 'Starting fixture'\n"
            "try { throw 'fixture password=SECRET' } catch { Save-YitoBootstrapError $_ 'download-python' }\n",
            encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (self.root / 'logs/mito.log').read_text(encoding='utf-8-sig').splitlines()
        error = json.loads(lines[-1])
        self.assertEqual(error['phase'], 'download-python')
        self.assertEqual(error['level'], 'error')
        self.assertIn('fixture', error['message'])
        self.assertNotIn('SECRET', '\n'.join(lines))
        self.assertIn('windows', error)
        diagnostics.write(self.root, 'info', 'python', 'Python takes over')
        self.assertEqual(json.loads((self.root / 'logs/mito.log').read_text().splitlines()[-1])['operation'], 'python')


if __name__ == '__main__':
    unittest.main()
