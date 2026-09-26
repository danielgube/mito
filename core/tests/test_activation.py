import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.archives import extract
from mito.engine import Engine
from mito.storage import YitoError, read_json, write_json
from mito.terminal import command


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.engine.save_profile('test', {'workspace':'test', 'apps':[], 'env':{}})

    def tearDown(self):
        self.temp.cleanup()

    def install(self, version='1', persist=None):
        m={'version':version,'url':'https://example.org/tool.zip','hash':'0'*64,'bin':'tool.exe','env_set':{'JAVA_HOME':'$dir'}}
        if persist:
            m['persist']=persist
        write_json(self.root/'core/buckets/main.json',{'apps':{'tool':m}})
        def fetch(url,path,expected, **kwargs):
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('tool.exe',b'fixture')
                z.writestr('conf/settings.ini',b'default')
                z.writestr('user.ini',b'file default')
        with patch('mito.engine.download',fetch):
            self.engine.install('main/tool', log=lambda _:None)
        return next(k for k,v in self.engine.installed().items() if v['version']==version)

    def test_install_replaces_version_without_activating_component(self):
        first, second = self.install('1'), self.install('2')
        self.assertEqual(first, second)
        self.assertEqual(list(self.engine.installed()), ['main/tool'])
        self.assertEqual(self.engine.installed()[first]['version'], '2')
        self.assertEqual(self.engine.installed()[first]['path'], 'core/apps/main/tool')
        self.assertEqual(self.engine.profile('test')['apps'], [])
        self.engine.set_active('test', first, True)
        self.engine.set_active('test', second, True)
        self.assertEqual(self.engine.profile('test')['apps'], [first])
        self.engine.environment('test')
        self.engine.set_active('test', first, False)
        self.engine.environment('test')
        self.assertEqual(len(self.engine.installed()), 1)
        self.assertFalse((self.root/'core/state/shims/test/tool.cmd').exists())

    def test_persist_file_directory_and_relocation(self):
        installation=self.install(persist=['conf','user.ini'])
        with patch('mito.terminal.prepare'):
            self.engine.set_active('test',installation,True)
        self.engine.environment('test')
        home=self.root/'home/persist/main/tool'
        (home/'conf/settings.ini').write_text('custom')
        (home/'user.ini').write_text('custom file')
        # Rename the entire test instance. Junctions now point to a stale root.
        moved=self.root.parent/(self.root.name+'-moved')
        self.assertTrue(self.root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.assertEqual(moved.parent,self.root.parent)
        self.root.rename(moved)
        try:
            engine=Engine(moved)
            engine.environment('test')
            payload=moved/engine.installed()[installation]['path']
            self.assertEqual((payload/'conf/settings.ini').read_text(),'custom')
            self.assertEqual((payload/'user.ini').read_text(),'custom file')
            self.assertTrue((payload/'conf').resolve().is_relative_to(moved/'home'))
        finally:
            moved.rename(self.root)

    def test_tar_rejects_symlinks(self):
        archive=self.root/'bad.tar'
        with tarfile.open(archive,'w') as tar:
            entry=tarfile.TarInfo('link')
            entry.type=tarfile.SYMTYPE
            entry.linkname='../outside'
            tar.addfile(entry)
        with self.assertRaises(YitoError):
            extract(archive,self.root/'out',self.root,'bad.tar')
        self.assertFalse((self.root/'out').exists())

    def test_tar_and_standalone_file(self):
        archive=self.root/'good.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            entry=tarfile.TarInfo('folder/file.txt')
            entry.size=4
            tar.addfile(entry,io.BytesIO(b'test'))
        extract(archive,self.root/'out',self.root,'good.tar.gz')
        self.assertEqual((self.root/'out/folder/file.txt').read_bytes(),b'test')
        exe=self.root/'download'
        exe.write_bytes(b'MZ fixture')
        extract(exe,self.root/'exe',self.root,'tool.exe')
        self.assertEqual((self.root/'exe/tool.exe').read_bytes(),exe.read_bytes())

    def test_jar_is_preserved_unless_url_alias_requests_zip_extraction(self):
        archive = self.root/'download'
        with zipfile.ZipFile(archive, 'w') as jar:
            jar.writestr('Main.class', b'fixture')
        extract(archive, self.root/'jar', self.root, 'Tool.JAR')
        self.assertEqual((self.root/'jar/Tool.JAR').read_bytes(), archive.read_bytes())
        extract(archive, self.root/'zip', self.root, 'tool.zip')
        self.assertEqual((self.root/'zip/Main.class').read_bytes(), b'fixture')

    def test_deflate64_zip_uses_private_extractor(self):
        runtime_root = Path(__file__).resolve().parents[2]
        sevenzip = runtime_root/'core/tools/7zip/7z.exe'
        if not sevenzip.exists():
            self.skipTest('Requiere el 7-Zip privado del bootstrap')
        source = self.root/'fixture.txt'
        source.write_bytes(b'Deflate64 fixture ' * 1000)
        archive = self.root/'deflate64.zip'
        subprocess.run([str(sevenzip), 'a', '-tzip', '-mm=Deflate64', str(archive), str(source)], check=True, capture_output=True)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.infolist()[0].compress_type, 9)
        extract(archive, self.root/'out', runtime_root, 'fixture.zip')
        self.assertEqual((self.root/'out/fixture.txt').read_bytes(), source.read_bytes())

    def test_extensionless_7zip_detected_by_signature(self):
        runtime_root = Path(__file__).resolve().parents[2]
        source = self.root/'fixture.txt'
        source.write_bytes(b'extensionless archive fixture')
        archive = self.root/'download'
        subprocess.run([str(runtime_root/'core/tools/7zip/7z.exe'), 'a', '-t7z',
                        str(archive.with_suffix('.7z')), str(source)], check=True, capture_output=True)
        archive.with_suffix('.7z').rename(archive)
        extract(archive, self.root/'out', runtime_root, 'download')
        self.assertEqual((self.root/'out/fixture.txt').read_bytes(), source.read_bytes())

    def test_zip_fallback_never_bypasses_path_validation(self):
        archive = self.root/'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../escape.txt', b'bad')
        with patch('mito.archives.extract_7zip') as fallback, self.assertRaises(YitoError):
            extract(archive, self.root/'out', self.root, 'bad.zip')
        fallback.assert_not_called()
        self.assertFalse((self.root/'out').exists())

    def test_rar_rejects_unsafe_entries_before_extraction(self):
        archive = self.root/'download'
        archive.write_bytes(b'RAR fixture listing is mocked')
        executable = self.root/'core/tools/7zip/7z.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        for listing in ('Path = ../escape\nSize = 1\n',
                        'Path = settings\nSize = 1\nSymbolic Link = outside\n',
                        'Path = settings\nSize = 1\nEncrypted = +\n'):
            with self.subTest(listing=listing), patch('mito.archives.subprocess.run', return_value=subprocess.CompletedProcess([],0,listing)) as run:
                with self.assertRaises(YitoError):
                    extract(archive,self.root/'out',self.root,'download.rar')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][1], 'l')
                self.assertFalse((self.root/'out').exists())

    def test_terminal_uses_bundled_executable_and_reactivates_inside_tab(self):
        executable=self.root/'core/tools/windows-terminal/WindowsTerminal.exe'
        executable.parent.mkdir(parents=True)
        executable.touch()
        argv=command(self.engine,'test')
        self.assertEqual(argv[0],str(executable))
        self.assertEqual(argv[-3:],['shell','--profile','test'])
        self.assertIn(str(self.root/'core/tools/python/python.exe'),argv)

    def test_bin_fixed_arguments_are_preserved(self):
        binary=list(self.engine.bins({'bin':[['tool.exe','alias','--name "two words" --flag']]}))[0]
        self.assertEqual(binary[2],['--name','two words','--flag'])
