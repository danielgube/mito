from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.engine import Engine
from mito.catalog import fingerprint
from mito.storage import YitoError, write_json
from mito.storage_migration import migrate
from mito.isolation import prepare_payload, persist_root


class StableStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.engine.save_profile('one', {'workspace':'one', 'apps':[], 'env':{}})
        self.manifest = {'version':'1', 'url':'https://example.org/tool.zip', 'hash':'0'*64, 'bin':'tool.exe'}
        patcher = patch('mito.engine.download', side_effect=self.download)
        patcher.start()
        self.addCleanup(patcher.stop)

    def download(self, url, target, *args, **kwargs):
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('tool.exe', url.encode())

    def catalog(self, apps):
        write_json(self.root/'core/buckets/main.json', {'apps':apps})

    def test_manifest_change_replaces_same_version_and_unsigned_update_requires_confirmation(self):
        self.catalog({'tool':self.manifest})
        self.engine.install('main/tool', 'one', log=lambda _:None)
        old = self.engine.installed()['main/tool']
        changed = dict(self.manifest, url='https://example.org/new.zip', hash=None)
        self.catalog({'tool':changed})
        with self.assertRaisesRegex(YitoError, 'sin hash'):
            self.engine.install('main/tool', log=lambda _:None)
        self.assertEqual(self.engine.installed()['main/tool'], old)
        self.engine.install('main/tool', confirm_unverified=lambda _:True, log=lambda _:None)
        self.assertEqual(list(self.engine.installed()), ['main/tool'])
        self.assertEqual(self.engine.installed()['main/tool']['path'], old['path'])
        self.assertEqual(self.engine.profile('one')['apps'], ['main/tool'])
        self.assertEqual((self.root/old['path']/'tool.exe').read_text(), changed['url'])

    def test_dependency_replacement_does_not_activate_requesting_app_in_other_profiles(self):
        self.catalog({'runtime':self.manifest})
        self.engine.install('main/runtime', 'one', log=lambda _:None)
        self.engine.save_profile('two', {'workspace':'two', 'apps':[], 'env':{}})
        self.catalog({'runtime':dict(self.manifest, version='2', depends='helper'),
                      'helper':self.manifest, 'editor':dict(self.manifest, depends='runtime')})
        self.engine.install('main/editor', 'two', log=lambda _:None)
        self.assertEqual(self.engine.profile('one')['apps'], ['main/runtime', 'main/helper'])
        self.assertEqual(set(self.engine.profile('two')['apps']), {'main/helper','main/runtime','main/editor'})
        self.assertEqual(self.engine.installed()['main/runtime']['version'], '2')

    def test_entire_plan_is_prepared_before_any_replacement(self):
        self.catalog({'tool':self.manifest})
        self.engine.install('main/tool', 'one', log=lambda _:None)
        old = self.engine.installed()
        self.catalog({'tool':dict(self.manifest, version='2'), 'broken':dict(self.manifest, bin='missing.exe')})
        with self.assertRaisesRegex(YitoError, 'Comando ausente'):
            self.engine.install(['main/tool', 'main/broken'], log=lambda _:None)
        self.assertEqual(self.engine.installed(), old)
        self.assertEqual(self.engine.profile('one')['apps'], ['main/tool'])

    def legacy(self, version='1', isolated=False):
        manifest = dict(self.manifest, version=version)
        if isolated:
            manifest.update(persist='data', shortcuts=[['tool.exe','Tool']])
        record = {'key':'main/tool', 'version':version, 'manifest':manifest,
                  'manifest_sha256':fingerprint(manifest), 'path':f'core/apps/main/tool/{version}-{fingerprint(manifest)[:16]}'}
        source = self.root/record['path']
        write_json(source/'.mito.json', {'key':record['key'], 'record':record, 'manifest_sha256':record['manifest_sha256']})
        (source/'tool.exe').write_bytes(b'old')
        return record

    def test_migration_keeps_profile_copies_data_activation_and_is_idempotent(self):
        old = self.legacy(isolated=True)
        old_id = old['key'] + '@' + old['manifest_sha256'][:16]
        self.engine.save_profile('one', {'workspace':'one','apps':[old_id],'env':{'CUSTOM':'value'}})
        copy = prepare_payload(self.root, old, 'one')
        (copy/'data/user.txt').write_text('keep')
        inactive = prepare_payload(self.root, old, 'inactive')
        (inactive/'data/user.txt').write_text('inactive')
        migrate(self.engine, log=lambda _:None)
        migrate(self.engine, log=lambda _:None)
        self.assertEqual(self.engine.profile('one')['apps'], ['main/tool'])
        self.assertEqual(self.engine.profile('one')['env'], {'CUSTOM':'value'})
        self.assertEqual(self.engine.installed()['main/tool']['path'], 'core/apps/main/tool')
        for name, value in [('one','keep'), ('inactive','inactive')]:
            target = self.root/'core/profile-apps'/name/'main/tool'
            self.assertEqual((target/'tool.exe').read_bytes(), b'old')
            self.assertEqual((target/'data/user.txt').read_text(), value)
            self.assertEqual((target/'data').resolve(), persist_root(self.root, old, name)/'data')
        self.assertFalse((self.root/old['path']).exists())
        self.engine.environment('one')

    def test_migration_refuses_ambiguous_versions_without_changes(self):
        first, second = self.legacy('1'), self.legacy('2')
        with self.assertRaisesRegex(YitoError, 'ambigua'):
            migrate(self.engine)
        for record in (first, second):
            self.assertTrue((self.root/record['path']/'tool.exe').exists())
        self.assertFalse((self.root/'core/apps/main/tool/.mito.json').exists())

    def test_failed_copy_preparation_restores_all_old_binaries_and_user_data(self):
        manifest = dict(self.manifest, persist='data', shortcuts=[['tool.exe','Tool']])
        self.catalog({'tool':manifest})
        self.engine.install('main/tool', 'one', log=lambda _:None)
        self.engine.save_profile('two', {'workspace':'two','apps':['main/tool'],'env':{}})
        old = self.engine.installed()['main/tool']
        for name in ('one','two'):
            copy = prepare_payload(self.root, old, name)
            (copy/'tool.exe').write_bytes(name.encode())
            (copy/'data/user.txt').write_text(name)
        self.catalog({'tool':dict(manifest, version='2')})
        def prepare(root, record, name):
            if name == 'two':
                raise OSError('copy failed')
            return prepare_payload(root, record, name)
        with patch('mito.isolation.prepare_payload', side_effect=prepare):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                self.engine.install('main/tool', log=lambda _:None)
        self.assertEqual(self.engine.installed()['main/tool'], old)
        for name in ('one','two'):
            copy = self.root/'core/profile-apps'/name/'main/tool'
            self.assertEqual((copy/'tool.exe').read_bytes(), name.encode())
            self.assertEqual((copy/'data/user.txt').read_text(), name)
            self.assertEqual(self.engine.profile(name)['apps'], ['main/tool'])

    def test_migration_rolls_back_on_profile_write_failure(self):
        old = self.legacy()
        old_id = old['key'] + '@' + old['manifest_sha256'][:16]
        self.engine.save_profile('one', {'workspace':'one','apps':[old_id],'env':{}})
        before = (self.root/old['path']/'.mito.json').read_bytes()
        with patch.object(self.engine, 'save_profile', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                migrate(self.engine)
        self.assertEqual((self.root/old['path']/'.mito.json').read_bytes(), before)
        self.assertEqual(self.engine.profile('one')['apps'], [old_id])
        self.assertFalse((self.root/'core/apps/main/tool/.mito.json').exists())
