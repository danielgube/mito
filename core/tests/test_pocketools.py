from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.engine import Engine
from mito.pocketools import git_snapshot, info, paths, sources, update
from mito.storage import YitoError, write_json


class PocketoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = (Path(self.tmp.name)/'mito').resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.source = Path(self.tmp.name)/'source'
        self.source.mkdir()
        write_json(self.source/'pocketool.json', {'info':'Corporate tools'})
        (self.source/'hello.cmd').write_text('@echo off\necho hello\n')
        (self.root/'config.properties').write_text(f'pocketool.tools={self.source}\n')

    def test_shared_path_update_removes_stale_files_and_keeps_subfolders(self):
        (self.source/'support').mkdir()
        (self.source/'support/data.txt').write_text('data')
        (self.source/'.git').mkdir()
        (self.source/'.git/config').write_text('private repository metadata')
        self.assertEqual(paths(self.root), [])
        update(self.engine, 'tools', log=lambda _:None)
        destination = self.root/'pocketools/tools'
        self.assertEqual(info(destination), 'Corporate tools')
        self.assertFalse((destination/'.git').exists())
        self.assertEqual((destination/'support/data.txt').read_text(), 'data')
        for profile in ('one','two'):
            self.engine.save_profile(profile, {'workspace':profile, 'apps':[], 'env':{}})
            environment = self.engine.environment(profile)
            self.assertIn(str(destination), environment['PATH'].split(os.pathsep))
            self.assertNotIn(str(destination/'support'), environment['PATH'].split(os.pathsep))
        (self.source/'hello.cmd').unlink()
        (self.source/'new.cmd').write_text('@echo off\necho new\n')
        update(self.engine, 'tools', log=lambda _:None)
        self.assertFalse((destination/'hello.cmd').exists())
        self.assertTrue((destination/'new.cmd').exists())
        (self.root/'config.properties').write_text('')
        self.assertEqual(paths(self.root), [])
        self.assertTrue(destination.exists())

    def test_invalid_metadata_preserves_previous_package(self):
        update(self.engine, 'tools', log=lambda _:None)
        for value in ({}, {'info':42}, {'info':' '}, []):
            write_json(self.source/'pocketool.json', value)
            with self.assertRaises(YitoError):
                update(self.engine, 'tools', log=lambda _:None)
            self.assertEqual(info(self.root/'pocketools/tools'), 'Corporate tools')
        (self.source/'pocketool.json').unlink()
        with self.assertRaises(YitoError):
            update(self.engine, 'tools', log=lambda _:None)
        self.assertTrue((self.root/'pocketools/tools/hello.cmd').exists())

    def test_source_names_and_junctions_are_rejected(self):
        for config in ('pocketool../escape=https://example.org/repo.git',
                       'pocketool.x=relative/path',
                       'pocketool.X=https://example.org/a\npocketool.x=https://example.org/b'):
            (self.root/'config.properties').write_text(config)
            with self.assertRaises(YitoError):
                sources(self.root)
        (self.root/'config.properties').write_text(f'pocketool.tools={self.source}\n')
        external = Path(self.tmp.name)/'external'
        external.mkdir()
        linked = self.source/'linked'
        subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(linked),str(external)], check=True, capture_output=True)
        try:
            with self.assertRaisesRegex(YitoError, 'enlaces'):
                update(self.engine, 'tools', log=lambda _:None)
            self.assertFalse((self.root/'pocketools/tools').exists())
        finally:
            os.rmdir(linked)

    def test_git_snapshot_exports_without_checkout_and_uses_validated_zip(self):
        stage = self.root/'core/staging/fixture'
        stage.mkdir()
        payload = stage/'payload'
        def git(arguments, **kwargs):
            if 'archive' in arguments:
                output = next(arg.split('=',1)[1] for arg in arguments if arg.startswith('--output='))
                with zipfile.ZipFile(output,'w') as archive:
                    archive.writestr('pocketool.json','{"info":"Git snapshot"}')
                    archive.writestr('tool.ps1', 'throw "must not execute during sync"')
            return subprocess.CompletedProcess(arguments,0,'','')
        with patch('mito.pocketools.git_executable',return_value='git.exe'), patch('mito.pocketools.subprocess.run',side_effect=git) as run:
            git_snapshot(self.engine,'http://example.org/tools.git',stage,payload)
        self.assertEqual(info(payload),'Git snapshot')
        clone = run.call_args_list[0].args[0]
        self.assertIn('--no-checkout',clone)
        self.assertIn('--no-recurse-submodules',clone)
        self.assertEqual(run.call_args_list[0].kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
        self.assertEqual(len(run.call_args_list),2)
