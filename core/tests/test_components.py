import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mito.engine import Engine
from mito.catalog import fingerprint
from mito.storage import YitoError, write_json
from mito.components import available_update, install_version, release_version, remove, update, version_key


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.engine=Engine(self.root)
        self.engine.initialize()
        import zipfile
        def download(url, target, *args, **kwargs):
            with zipfile.ZipFile(target, 'w') as output:
                output.writestr('tool.exe', b'new')
        patcher = patch('mito.engine.download', side_effect=download)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.old=self.record('1.9')
        self.old_id=self.engine.install_id('main/tool',self.old)
        for name in ('one','two'):
            self.engine.save_profile(name,{'workspace':name,'apps':[self.old_id],'env':{}})

    def tearDown(self):self.temp.cleanup()

    def record(self,version):
        m={'version':version,'url':'http://example.org/tool.zip','hash':'0'*64,'persist':'data'}
        record={'key':'main/tool','version':version,'manifest':m,'manifest_sha256':fingerprint(m),'path':'core/apps/main/tool'}
        if not (self.root/record['path']/'.mito.json').exists():
            write_json(self.root/record['path']/'.mito.json',{'key':'main/tool','record':record,'manifest_sha256':fingerprint(m)})
        return record

    def test_numeric_versions_and_unsupported_update(self):
        self.assertGreater(version_key('1.10'),version_key('1.9'))
        self.assertGreater(version_key('2.0'),version_key('2.0-rc1'))
        self.assertEqual(version_key('1.0.0'),version_key('1'))
        m=dict(self.old['manifest'],version='2.0',installer={'file':'setup.exe'})
        found=available_update(self.engine,self.old,{'main/tool':m})
        self.assertEqual(found['version'],'2.0')
        self.assertNotEqual(found['status'],'Compatible')
        self.assertIsNone(available_update(self.engine,self.old,{'main/tool':self.old['manifest']}))

    def test_refresh_only_installed_keys_once_without_changing_payloads_or_profiles(self):
        self.record('1.8')
        for name in self.engine.profiles():
            self.engine.save_profile(name, {'workspace': name, 'apps': [], 'env': {}})
        old_inventory = self.engine.installed()
        newer = dict(self.old['manifest'], version='1.10')
        write_json(self.root/'core/buckets/main.json', {'source':'https://github.com/example/catalog',
            'apps': {'tool':self.old['manifest'], 'uninstalled':self.old['manifest']}})
        def fetch(url, destination, **kwargs):
            self.assertEqual(url, 'https://raw.githubusercontent.com/example/catalog/HEAD/bucket/tool.json')
            write_json(destination, newer)
        with patch('mito.catalog.download', side_effect=fetch) as download:
            self.assertEqual(self.engine.catalog.refresh_installed(old_inventory), 1)
            download.assert_called_once()
        self.assertEqual(self.engine.installed(), old_inventory)
        self.assertTrue(all(not self.engine.profile(n)['apps'] for n in self.engine.profiles()))
        self.assertEqual(available_update(self.engine, self.old)['version'], '1.10')
        self.assertEqual(self.engine.catalog.resolve('main/uninstalled')[1], self.old['manifest'])

    def test_refresh_local_partial_failure_preserves_previous_manifest(self):
        source = self.root/'source/bucket'
        newer = dict(self.old['manifest'], version='2.0')
        write_json(source/'tool.json', newer)
        write_json(source/'broken.json', ['invalid'])
        write_json(self.root/'core/buckets/main.json', {'source':str(source.parent),
            'apps': {'tool':self.old['manifest'], 'broken':self.old['manifest']}})
        logs = []
        inventory = dict(self.engine.installed(), broken={'key':'main/broken'}, missing={'key':'main/missing'})
        self.assertEqual(self.engine.catalog.refresh_installed(inventory, logs.append), 1)
        self.assertEqual(len(logs), 2)
        self.assertEqual(self.engine.catalog.resolve('main/broken')[1], self.old['manifest'])
        with patch('mito.catalog.download', side_effect=OSError('offline')):
            (self.root/'config.properties').write_text('bucket.main=https://github.com/example/catalog')
            self.assertEqual(self.engine.catalog.refresh_installed(self.engine.installed(), logs.append), 0)
        self.assertEqual(self.engine.catalog.resolve('main/tool')[1], newer)

    def test_refresh_selected_uninstalled_key_preserves_other_entries_and_inventory(self):
        source = self.root/'source'
        newer = dict(self.old['manifest'], version='3.0')
        write_json(source/'new.json', newer)
        write_json(self.root/'core/buckets/main.json', {'source': str(source),
            'apps': {'tool': self.old['manifest'], 'new': self.old['manifest']}})
        inventory = self.engine.installed()
        self.assertEqual(self.engine.catalog.refresh_keys(['main/new']), 1)
        self.assertEqual(self.engine.catalog.resolve('main/new')[1], newer)
        self.assertEqual(self.engine.catalog.resolve('main/tool')[1], self.old['manifest'])
        self.assertEqual(self.engine.installed(), inventory)

    def test_remove_retains_persist_and_other_profiles(self):
        self.engine.environment('one')
        persisted=self.root/'home/persist/main/tool/data/keep.txt'
        persisted.write_text('user data')
        remove(self.engine,self.old_id,log=lambda _:None)
        self.assertEqual(persisted.read_text(),'user data')
        self.assertFalse((self.root/self.old['path']).exists())
        for name in self.engine.profiles():self.assertFalse(self.engine.profile(name)['apps'])
        self.assertFalse(self.engine.installed())

    def test_update_switches_profiles_only_after_install_succeeds(self):
        new=self.record('1.10')
        new_id=self.engine.install_id('main/tool',new)
        write_json(self.root/'core/buckets/main.json',{'apps':{'tool':new['manifest']}})
        with patch.object(self.engine,'install',side_effect=YitoError('download failed')):
            with self.assertRaises(YitoError):update(self.engine,self.old_id)
        self.assertIn(self.old_id,self.engine.profile('one')['apps'])
        self.assertEqual(update(self.engine,self.old_id,log=lambda _:None),new_id)
        for name in self.engine.profiles():
            self.assertIn(new_id,self.engine.profile(name)['apps'])
        self.assertEqual(list(self.engine.installed()), [self.old_id])
        self.assertEqual(self.engine.installed()[self.old_id]['version'], '1.10')

    def test_update_preserves_other_components_and_inactive_profiles(self):
        other = dict(self.old, key='versions/tool', path='core/apps/versions/tool')
        write_json(self.root/other['path']/'.mito.json', {'key':other['key'], 'record':other, 'manifest_sha256':other['manifest_sha256']})
        self.engine.save_profile('two', {'workspace':'two', 'apps':['versions/tool'], 'env':{}})
        new = self.record('2.0')
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':new['manifest']}})
        update(self.engine, self.old_id, log=lambda _:None)
        self.assertEqual(self.engine.profile('two')['apps'], ['versions/tool'])
        self.assertEqual(self.engine.installed()['versions/tool']['version'], '1.9')
        self.assertEqual(self.engine.installed()[self.old_id]['version'], '2.0')

    def test_update_layout_change_is_rejected_before_install(self):
        new = dict(self.old['manifest'], version='2.0', persist='other-data')
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':new}})
        with patch.object(self.engine, 'install') as install:
            with self.assertRaisesRegex(YitoError, 'migración'):
                update(self.engine, self.old_id)
            install.assert_not_called()

    def test_update_rollback_restores_profiles_and_payload(self):
        new = self.record('2.0')
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':new['manifest']}})
        original = self.engine.save_profile
        def save(name, data):
            if name == 'two' and self.engine.installed()[self.old_id]['version'] == '2.0':
                raise OSError('write failed')
            original(name, data)
        with patch.object(self.engine, 'save_profile', side_effect=save):
            with self.assertRaisesRegex(OSError, 'write failed'):
                update(self.engine, self.old_id, log=lambda _:None)
        for name in self.engine.profiles():
            self.assertEqual(self.engine.profile(name)['apps'], [self.old_id])
        self.assertEqual(self.engine.installed()[self.old_id]['version'], '1.9')
        self.assertTrue((self.root/self.old['path']).exists())

    def test_update_locked_binary_keeps_old_references(self):
        import ctypes
        from ctypes import wintypes
        binary = self.root/self.old['path']/'tool.exe'
        binary.write_bytes(b'old')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(binary), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        new = self.record('2.0')
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':new['manifest']}})
        try:
            with self.assertRaisesRegex(YitoError, 'Cierra la aplicación'):
                update(self.engine, self.old_id, log=lambda _:None)
            self.assertEqual(self.engine.profile('one')['apps'], [self.old_id])
            self.assertTrue(binary.exists())
        finally:
            kernel.CloseHandle(handle)

    def test_update_installs_new_payload_and_preserves_shared_persist(self):
        import zipfile
        import shutil
        self.engine.environment('one')
        data = self.root/'home/persist/main/tool/data/user.txt'
        data.write_text('keep')
        archive = self.root/'download.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('tool.exe', b'new binary')
        manifest = dict(self.old['manifest'], version='2.0', bin='tool.exe')
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':manifest}})
        with patch('mito.engine.download', side_effect=lambda url, target, *args, **kwargs: shutil.copyfile(archive,target)):
            new_id = update(self.engine,self.old_id,log=lambda _:None)
        inventory = self.engine.installed()
        self.assertEqual(list(inventory), [new_id])
        payload = self.root/inventory[new_id]['path']
        self.assertEqual((payload/'tool.exe').read_bytes(), b'new binary')
        self.assertTrue((payload/'data').is_junction())
        self.assertEqual((payload/'data/user.txt').read_text(), 'keep')

    def test_update_migrates_registered_shortcut_and_rolls_it_back_on_failure(self):
        from mito.storage import read_json
        new = self.record('2.0')
        new_id = self.engine.install_id('main/tool', new)
        write_json(self.root/'core/buckets/main.json', {'apps':{'tool':new['manifest']}})
        path = self.root/'existing.lnk'
        path.write_bytes(b'old shortcut')
        registry = self.root/'core/state/application-shortcuts/old.json'
        data = {'path':str(path), 'profile':'one', 'installation':self.old_id, 'badge':'TEST',
            'coupled':True, 'arguments':['--name','two words']}
        write_json(registry, data)
        def link(path, argv, root, icon):
            self.assertIn('main/tool', argv)
            self.assertIn('--coupled', argv)
            self.assertEqual(argv[-3:], ['--','--name','two words'])
            path.write_bytes(b'new shortcut')
        with patch.object(self.engine, 'application_target', return_value=('tool.exe', [])), patch('mito.shortcuts.application_icon', return_value=self.root/'icon.ico'), patch('mito.shortcuts.write_link', side_effect=link):
            with patch('mito.components.refresh_launchers', side_effect=[OSError('launcher failed'), None]):
                with self.assertRaisesRegex(OSError, 'launcher failed'):
                    update(self.engine, self.old_id, log=lambda _:None)
            self.assertEqual(path.read_bytes(), b'old shortcut')
            self.assertEqual(read_json(registry), data)
            self.assertEqual(self.engine.profile('one')['apps'], [self.old_id])
            update(self.engine, self.old_id, log=lambda _:None)
        self.assertEqual(path.read_bytes(), b'new shortcut')
        records = [read_json(p) for p in registry.parent.glob('*.json')]
        self.assertEqual(records, [{'path':str(path), 'profile':'one', 'app':'main/tool',
            'badge':'TEST', 'coupled':True, 'arguments':['--name','two words']}])

    def test_install_version_pins_until_released_and_survives_dependents(self):
        quiet=lambda _:None
        newer=dict(self.old['manifest'],version='2.0')
        write_json(self.root/'core/buckets/main.json',{'apps':{'tool':newer}})
        self.assertEqual(available_update(self.engine,self.old)['version'],'2.0')
        older=dict(self.old['manifest'],version='1.5')
        pin={'version':'1.5','source':'git','commit':'a'*40}
        install_version(self.engine,'main/tool',older,pin,log=quiet)
        lock=self.engine.installed()['main/tool']
        self.assertEqual((lock['version'],lock['pinned']),('1.5',pin))
        self.assertIsNone(available_update(self.engine,lock))
        for name in self.engine.profiles():self.assertIn(self.old_id,self.engine.profile(name)['apps'])
        # Una dependencia instalada después no sustituye la versión retenida por la del catálogo.
        dependent={'version':'1.0','url':'http://example.org/dep.zip','hash':'0'*64,'depends':'main/tool'}
        write_json(self.root/'core/buckets/main.json',{'apps':{'tool':newer,'dep':dependent}})
        self.engine.install('main/dep',log=quiet)
        self.assertEqual(self.engine.installed()['main/tool']['version'],'1.5')
        self.assertTrue(release_version(self.engine,'main/tool',log=quiet))
        lock=self.engine.installed()['main/tool']
        self.assertNotIn('pinned',lock)
        self.assertEqual(lock['version'],'1.5')
        self.assertEqual(available_update(self.engine,lock)['version'],'2.0')
        self.assertFalse(release_version(self.engine,'main/tool',log=quiet))

    @unittest.skipUnless(os.name == 'nt', 'Bloqueo de archivos de Windows')
    def test_remove_in_use_reports_open_application_and_keeps_payload(self):
        # Un archivo abierto sin FILE_SHARE_DELETE equivale a la app abierta en la bandeja.
        payload=self.root/self.old['path']
        locked=payload/'tool.exe'
        locked.write_bytes(b'fixture')
        with locked.open('rb'):
            with self.assertRaises(YitoError) as raised:remove(self.engine,self.old_id,log=lambda _:None)
        self.assertIn('antes de eliminar', str(raised.exception))
        self.assertTrue(payload.exists())
        self.assertIn(self.old_id,self.engine.profile('one')['apps'])
        self.assertFalse(list((self.root/'core/staging').glob('remove-*')))
        remove(self.engine,self.old_id,log=lambda _:None)
        self.assertFalse(payload.exists())

    def test_failed_remove_profile_write_restores_payload(self):
        with patch.object(self.engine,'save_profile',side_effect=OSError('write failed')):
            with self.assertRaises(OSError):remove(self.engine,self.old_id)
        self.assertTrue((self.root/self.old['path']).exists())
        self.assertIn(self.old_id,self.engine.profile('one')['apps'])
