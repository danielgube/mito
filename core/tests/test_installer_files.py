import io
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from mito.engine import Engine
from mito.storage import YitoError, write_json
from mito.transfer import download


class InstallerFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mito installer spaces ')
        self.root = Path(self.temp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.engine.save_profile('test', {'workspace':'test', 'apps':[], 'env':{}})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('sdk/tool.py', b'# never executed')
        self.script = '''param($destination)
if (!(Test-Path "$destination/prepared")) { throw 'pre_install missing' }
Set-Content "$destination/tool.ps1" '& python "$PSScriptRoot/tool.py" @args'
@{destination=$destination; arguments=@($args); names=@($fname); home=$env:USERPROFILE} | ConvertTo-Json | Set-Content "$destination/arguments.json"
'''
        self.payloads = {'/sdk.zip':archive.getvalue(), '/pyshim.ps1':self.script.encode()}
        payloads = self.payloads
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payloads[self.path])
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def manifest(self, **changes):
        return dict(version='1', url=[f'http://127.0.0.1:{self.server.server_port}{name}' for name in self.payloads],
                    hash=[hashlib.sha256(value).hexdigest() for value in self.payloads.values()],
                    extract_dir='sdk', bin='tool.ps1', pre_install='Set-Content "$dir/prepared" ready',
                    installer={'file':'pyshim.ps1','args':'$dir'}, **changes)

    def install(self, manifest):
        write_json(self.root/'core/buckets/test.json', {'apps':{'tool':manifest}})
        # Keep test HTTP local and independent of host proxy configuration.
        with patch('mito.engine.download', side_effect=lambda url,path,expected,**kw: download(url,path,expected)), patch('mito.terminal.prepare'):
            records = self.engine.install('tool','test',log=lambda _:None)
        return self.root/records['test/tool']['path']

    def test_mixed_downloads_file_arguments_and_architecture(self):
        for args in ('$dir "two words"', ['$dir','', 'two words', '$(throw "injection")', '${version}', '$global', '$dir_suffix']):
            with self.subTest(args=args):
                manifest = self.manifest()
                manifest['architecture'] = {'64bit':{'installer':{'file':'pyshim.ps1','args':args}}}
                manifest['installer'] = {'file':'blocked.exe'}
                payload = self.install(manifest)
                self.assertEqual((payload/'pyshim.ps1').read_bytes(), self.script.encode())
                self.assertEqual((payload/'tool.py').read_bytes(), b'# never executed')
                self.assertIn('& python', (payload/'tool.ps1').read_text())
                observed = json.loads((payload/'arguments.json').read_text())
                # Context points to staging during execution, including spaces.
                self.assertIn('mito installer spaces ', observed['destination'])
                self.assertEqual(observed['arguments'], ['two words'] if isinstance(args,str) else
                                 ['', 'two words', '$(throw "injection")', '1', 'False', '$dir_suffix'])
                self.assertEqual(observed['names'], ['sdk.zip','pyshim.ps1'])
                self.assertEqual(observed['home'], str(self.root/'home'))

    def test_bad_hash_prevents_all_hooks(self):
        manifest = self.manifest()
        manifest['hash'][1] = '0'*64
        with patch('mito.hooks.run_pre_install') as pre, patch('mito.hooks.run_installer') as installer:
            with self.assertRaisesRegex(YitoError, 'Hash incorrecto'):
                self.install(manifest)
            pre.assert_not_called()
            installer.assert_not_called()
        self.assertFalse(self.engine.installed())

    def test_unverified_script_requires_confirmation_before_download(self):
        manifest = self.manifest()
        manifest['hash'][1] = None
        write_json(self.root/'core/buckets/test.json', {'apps':{'tool':manifest}})
        with patch('mito.engine.download') as fetch, self.assertRaises(YitoError):
            self.engine.install('tool',log=lambda _:None,confirm_unverified=lambda _:False)
        fetch.assert_not_called()

    def test_file_errors_preserve_installed_version(self):
        payload = self.install(self.manifest())
        before = self.engine.installed()
        profile = self.engine.profile('test')
        for script in ('throw "file failed"', 'exit 19', 'Remove-Item "$dir/tool.py"; throw "failed"'):
            self.payloads['/pyshim.ps1'] = script.encode()
            manifest = self.manifest()
            manifest['version'] = '2'
            with self.subTest(script=script), self.assertRaisesRegex(YitoError, 'installer.file falló'):
                self.install(manifest)
            self.assertEqual(self.engine.installed(), before)
            self.assertEqual(self.engine.profile('test'), profile)
            self.assertEqual((payload/'tool.py').read_bytes(), b'# never executed')

    def test_invalid_file_modes_rejected_before_download(self):
        for installer in ({'file':'../outside.ps1'}, {'file':'C:/outside.ps1'}, {'file':'setup.exe'},
                          {'file':'setup.msi'}, {'file':'pyshim.ps1','script':'x'},
                          {'file':'pyshim.ps1','keep':True}, {'file':'pyshim.ps1','args':[7]},
                          {'args':'$dir'}, {'file':False}):
            manifest = self.manifest()
            manifest['installer'] = installer
            with self.subTest(installer=installer), patch('mito.engine.download') as fetch:
                write_json(self.root/'core/buckets/test.json', {'apps':{'tool':manifest}})
                with self.assertRaises(YitoError):
                    self.engine.install('tool', log=lambda _:None)
                fetch.assert_not_called()

    def test_missing_file_and_missing_archive_directory_are_not_ignored(self):
        manifest = self.manifest()
        manifest['installer']['file'] = 'missing.ps1'
        with self.assertRaisesRegex(YitoError, 'No existe missing.ps1'):
            self.install(manifest)
        manifest = self.manifest()
        manifest['extract_dir'] = 'missing'
        with self.assertRaisesRegex(YitoError, 'extract_dir no encontrado'):
            self.install(manifest)
        self.assertFalse(self.engine.installed())
