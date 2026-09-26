from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from mito.engine import Engine
from mito.catalog import fingerprint
from mito.isolation import isolated, payload_path, persist_root
from mito.components import remove, update
from mito.storage import write_json


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.engine=Engine(self.root)
        self.engine.initialize()
        m={'version':'1','persist':'data','shortcuts':[['editor.exe','Editor']],
           'bin':'editor.exe','env_add_path':'.','env_set':{'APP_ROOT':'$dir','APP_DATA':'$persist_dir'}}
        self.lock={'key':'main/editor','version':'1','manifest':m,'manifest_sha256':fingerprint(m),'path':'core/apps/main/editor'}
        self.id=self.engine.install_id(self.lock['key'],self.lock)
        base=self.root/self.lock['path']
        (base/'data').mkdir(parents=True)
        (base/'data/default.txt').write_text('default')
        (base/'editor.exe').write_bytes(b'original')
        write_json(base/'.mito.json',{'key':self.lock['key'],'manifest_sha256':fingerprint(m),'record':self.lock})
        for name in ('one','two'):
            self.engine.save_profile(name,{'workspace':name,'apps':[self.id],'env':{}})

    def tearDown(self):self.temp.cleanup()

    def test_rule_and_independent_data_binaries_environment_and_launch(self):
        self.assertTrue(isolated(self.lock))
        for field in ('persist','shortcuts'):
            m=dict(self.lock['manifest']);m.pop(field)
            self.assertFalse(isolated(dict(self.lock,manifest=m)))
        first=self.engine.environment('one')
        second=self.engine.environment('two')
        one=payload_path(self.root,self.lock,'one');two=payload_path(self.root,self.lock,'two')
        self.assertNotEqual(one,two)
        self.assertNotEqual(first['APP_ROOT'],second['APP_ROOT'])
        self.assertNotEqual(first['APP_DATA'],second['APP_DATA'])
        self.assertEqual(first['HOME'],second['HOME'])
        self.assertEqual(first['APP_ROOT'],str(one))
        (one/'editor.exe').write_bytes(b'changed')
        self.assertEqual((two/'editor.exe').read_bytes(),b'original')
        (one/'data/default.txt').write_text('one')
        self.assertEqual((two/'data/default.txt').read_text(),'default')
        self.assertEqual((self.root/self.lock['path']/'data/default.txt').read_text(),'default')
        with patch.object(self.engine,'environment',return_value=first),patch('mito.engine.subprocess.Popen') as launch:
            self.engine.launch('one',self.id)
        self.assertEqual(launch.call_args.args[0],[str(one/'editor.exe')])
        self.engine.set_active('one',self.id,False)
        self.engine.set_active('one',self.id,True)
        self.engine.environment('one')
        self.assertEqual((one/'data/default.txt').read_text(),'one')

    def test_remove_cleans_all_copies_preserves_profile_persist(self):
        self.engine.environment('one');self.engine.environment('two')
        remove(self.engine,self.id,log=lambda _:None)
        for name in ('one','two'):
            self.assertFalse(payload_path(self.root,self.lock,name).exists())
            self.assertTrue((persist_root(self.root,self.lock,name)/'data/default.txt').is_file())

    def test_update_replaces_active_and_inactive_copies_preserving_data(self):
        for name in ('one', 'two'):
            self.engine.environment(name)
            (persist_root(self.root,self.lock,name)/'data/default.txt').write_text(name)
        self.engine.set_active('two', self.id, False)
        m = dict(self.lock['manifest'], version='2', url='https://example.org/editor.zip', hash='0'*64)
        new = dict(self.lock, version='2', manifest=m, manifest_sha256=fingerprint(m), path='core/apps/main/editor')
        new_id = self.engine.install_id(new['key'], new)
        import zipfile
        def download(url, target, *args, **kwargs):
            with zipfile.ZipFile(target, 'w') as output:
                output.writestr('editor.exe', b'new')
        write_json(self.root/'core/buckets/main.json', {'apps':{'editor':m}})
        with patch('mito.engine.download', side_effect=download):
            self.assertEqual(update(self.engine,self.id,log=lambda _:None),new_id)
        self.assertEqual(new['path'], self.lock['path'])
        self.assertEqual(self.engine.profile('one')['apps'], [new_id])
        self.assertEqual(self.engine.profile('two')['apps'], [])
        for name in ('one','two'):
            self.assertEqual(payload_path(self.root,self.lock,name), payload_path(self.root,new,name))
            target = payload_path(self.root,new,name)
            self.assertEqual((target/'editor.exe').read_bytes(), b'new')
            self.assertTrue((target/'data').is_junction())
            self.assertEqual((target/'data/default.txt').read_text(), name)

    def test_existing_shared_junction_is_not_copied_to_profiles(self):
        from mito.persistence import prepare
        base=self.root/self.lock['path']
        prepare(self.root,base,self.lock['key'],self.lock['manifest'])
        shared=self.root/'home/persist/main/editor/data'
        (shared/'private.txt').write_text('old shared')
        self.engine.environment('one')
        copy=payload_path(self.root,self.lock,'one')
        self.assertNotEqual((copy/'data').resolve(),shared.resolve())
        self.assertFalse((copy/'data/private.txt').exists())
        self.assertTrue((shared/'private.txt').exists())

    def test_relocation_rebuilds_profile_junction(self):
        self.engine.environment('one')
        moved=self.root.parent/(self.root.name+'-moved')
        try:
            # Resolved source and destination are explicit sibling temp workspaces.
            self.root.rename(moved)
            engine=Engine(moved)
            engine.environment('one')
            copy=payload_path(moved,self.lock,'one')
            self.assertEqual((copy/'data').resolve(),persist_root(moved,self.lock,'one')/'data')
        finally:
            if moved.exists():moved.rename(self.root)
