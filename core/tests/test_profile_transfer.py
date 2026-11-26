import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.engine import Engine
from mito.profile_transfer import export_profile, import_profile, read_export
from mito.storage import YitoError, write_json


class ProfileTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = Engine(self.root/'instance')
        self.engine.initialize()
        self.manifest = {'version':'1','url':'https://example.org/app.zip','hash':'0'*64,'bin':'app.exe'}
        self.bucket({'tool':self.manifest})
        self.file = self.root/'shared.json'
        write_json(self.file, {'apps':['main/tool']})

    def bucket(self, apps):
        write_json(self.engine.root/'core/buckets/main.json', {'apps':apps})

    def fetch(self, url, path, expected, **kwargs):
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('app.exe', b'fixture')

    def test_export_has_only_active_catalog_names(self):
        self.engine.create_profile('private-name', 'private-workspace')
        self.bucket({'tool':dict(self.manifest, notes='SECRET_TOKEN',env_set={'APP_SECRET':'SECRET_TOKEN'})})
        with patch('mito.engine.download', self.fetch):
            self.engine.install('main/tool', 'private-name', log=lambda _:None)
        profile = self.engine.profile('private-name')
        profile['env'] = {'TOKEN':'SECRET_TOKEN'}
        self.engine.save_profile('private-name',profile)
        path = export_profile(self.engine,'private-name',self.root/'export.json')
        self.assertEqual(json.loads(path.read_text()), {'apps':['main/tool']})
        self.assertNotIn('SECRET',path.read_text())
        with self.assertRaises(FileExistsError):
            export_profile(self.engine,'private-name',path)

    def test_import_review_reuse_and_empty_environment(self):
        self.bucket({'tool':dict(self.manifest,version='2')})
        with patch('mito.engine.download', self.fetch):
            existing = self.engine.install('main/tool', log=lambda _:None)
        seen = []
        def review(records):
            seen.append(records)
            return True
        with patch('mito.engine.download') as fetch:
            name = import_profile(self.engine,self.file,'new',review,log=lambda _:None)
            fetch.assert_not_called()
        profile = self.engine.profile(name)
        self.assertEqual(profile, {'workspace':'new','apps':list(existing),'env':{}})
        self.assertEqual(next(iter(seen[0].values()))['version'],'2')

    def test_cancel_review_and_unsigned_dependency_leave_no_profile(self):
        dep = dict(self.manifest); dep.pop('hash')
        self.bucket({'tool':dict(self.manifest,depends='dep'),'dep':dep})
        with patch('mito.engine.download') as fetch:
            with self.assertRaises(YitoError):
                import_profile(self.engine,self.file,'new',lambda _:False,log=lambda _:None)
            with self.assertRaises(YitoError):
                import_profile(self.engine,self.file,'new',lambda _:True,lambda _:False,log=lambda _:None)
            fetch.assert_not_called()
        self.assertNotIn('new',self.engine.profiles())
        with patch('mito.engine.download',self.fetch):
            import_profile(self.engine,self.file,'new',lambda _:True,lambda _:True,log=lambda _:None)
        self.assertEqual(len(self.engine.profile('new')['apps']),2)

    def test_failure_and_existing_name_do_not_replace_profiles(self):
        with patch('mito.engine.download',side_effect=OSError('download failed')):
            with self.assertRaises(OSError):
                import_profile(self.engine,self.file,'new',lambda _:True,log=lambda _:None)
        self.assertNotIn('new',self.engine.profiles())
        self.engine.create_profile('existing')
        before = self.engine.profile('existing')
        with patch('mito.engine.download') as fetch, self.assertRaises(YitoError):
            import_profile(self.engine,self.file,'existing',lambda _:True)
        fetch.assert_not_called()
        self.assertEqual(self.engine.profile('existing'),before)

    def test_pinned_version_is_exported_and_reproduced_as_pinned(self):
        self.engine.create_profile('source')
        old = dict(self.manifest, version='0.9')
        pin = {'version':'0.9','source':'git','commit':'a'*40}
        with patch('mito.engine.download', self.fetch):
            self.engine.install('main/tool', 'source', log=lambda _:None, manifests={'main/tool':old}, pins={'main/tool':pin})
        # Las integraciones con el host son estado de la instancia, nunca del archivo.
        write_json(self.engine.root/'core/state/host-exposed-links.json', {'main/tool':{'data':[{'relative':'Tool','backup':None}]}})
        write_json(self.engine.root/'core/state/host-applications.json', {'0123456789abcdef':'C:\\Host\\app.exe'})
        path = export_profile(self.engine,'source',self.root/'pinned.json')
        self.assertEqual(json.loads(path.read_text()), {'apps':['main/tool'],'pinned':{'main/tool':'0.9'}})
        self.assertNotIn('Host', path.read_text())
        self.assertEqual(read_export(path), {'apps':['main/tool'],'pinned':{'main/tool':'0.9'}})
        # Otra instancia: el catálogo va por la 1, pero se instala y retiene la 0.9.
        other = Engine(self.root/'other')
        other.initialize()
        write_json(other.root/'core/buckets/main.json', {'apps':{'tool':self.manifest}})
        seen = []
        def resolve(root, catalog, key, version, log=print):
            seen.append((key, version))
            return old, pin
        with patch('mito.versions.resolve', resolve), patch('mito.engine.download', self.fetch):
            import_profile(other, path, 'copy', lambda records: seen.append(dict(records)) or True, log=lambda _:None)
        self.assertEqual(seen[0], ('main/tool','0.9'))
        record = other.installed()['main/tool']
        self.assertEqual((record['version'], record['pinned']), ('0.9', pin))
        self.assertEqual(seen[1]['main/tool']['pinned'], pin)

    def other_instance(self, name='other'):
        other = Engine(self.root/name)
        other.initialize()
        write_json(other.root/'core/buckets/main.json', {'apps':{'tool':self.manifest}})
        return other

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_files_table_accepts_only_relative_paths_in_declared_roots(self):
        from mito.profile_files import entries
        valid = [{'root':'home','path':'.m2/settings.xml'}, {'root':'workspace','path':'.vscode'},
                 {'root':'persist','app':'Main/Tool','path':'data/User'}]
        self.assertEqual(entries(valid)[2], {'root':'persist','app':'main/tool','path':'data/User'})
        for value in ([{'root':'home','path':'../outside'}], [{'root':'home','path':'C:/x'}],
                      [{'root':'home','path':'a\\b'}], [{'root':'home','path':'/abs'}],
                      [{'root':'home','path':'tmp/x'}], [{'root':'home','path':'Profiles/p/persist'}],
                      [{'root':'host','path':'x'}], [{'root':'persist','path':'x'}],
                      [{'root':'home','app':'main/tool','path':'x'}], [{'root':'home','path':'a:b'}],
                      [{'root':'home','path':'.npmrc'},{'root':'home','path':'.NPMRC'}],
                      [{'root':'home','path':'.m2'},{'root':'home','path':'.m2/settings.xml'}],
                      [{'root':'home','path':'x','extra':1}], {'root':'home','path':'x'}):
            with self.subTest(value=value), self.assertRaises(YitoError):
                entries(value)

    def test_translate_maps_real_paths_to_roots(self):
        from mito.profile_files import translate
        self.engine.create_profile('source')
        home = self.engine.root/'home'
        self.assertEqual(translate(self.engine,'source',home/'.m2'/'settings.xml'), {'root':'home','path':'.m2/settings.xml'})
        self.assertEqual(translate(self.engine,'source',self.engine.root/'workspaces'/'source'/'.vscode'),
                         {'root':'workspace','path':'.vscode'})
        for path in (self.root/'outside.txt', home, home/'tmp'/'x', home/'profiles'/'source'/'persist'/'x'):
            with self.subTest(path=path), self.assertRaises(YitoError):
                translate(self.engine,'source',path)

    def test_export_without_files_keeps_legacy_json(self):
        self.engine.create_profile('source')
        path = export_profile(self.engine,'source',self.root/'legacy.json',log=lambda _:None)
        self.assertEqual(json.loads(path.read_text()), {'apps':[]})

    def test_files_roundtrip_without_overwriting_shared_files_by_default(self):
        from mito.profile_files import save
        self.engine.create_profile('source')
        home, workspace = self.engine.root/'home', self.engine.root/'workspaces'/'source'
        self.write(home/'.m2'/'settings.xml', b'<settings/>')
        self.write(home/'.npmrc', b'registry=source')
        self.write(workspace/'.vscode'/'settings.json', b'{}')
        self.write(workspace/'.vscode'/'sub'/'launch.json', b'[]')
        save(self.engine,'source',[{'root':'home','path':'.m2/settings.xml'},{'root':'home','path':'.npmrc'},
                                   {'root':'workspace','path':'.vscode'},{'root':'home','path':'missing.cfg'}])
        self.assertEqual(self.engine.profile('source')['env'], {})
        logs = []
        path = export_profile(self.engine,'source',self.root/'source.mito.zip',log=logs.append)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), ['files/home/.m2/settings.xml','files/home/.npmrc',
                'files/workspace/.vscode/settings.json','files/workspace/.vscode/sub/launch.json','mito-profile.json'])
            manifest = json.loads(archive.read('mito-profile.json'))
        self.assertEqual(manifest['format'], 1)
        self.assertNotIn('env', manifest)
        self.assertNotIn({'root':'home','path':'missing.cfg'}, manifest['files'])
        self.assertTrue(any('missing.cfg' in line for line in logs))
        with self.assertRaises(FileExistsError):
            export_profile(self.engine,'source',path,log=lambda _:None)
        # Destino: .npmrc ya existe con otro contenido y es compartido por sus profiles.
        other = self.other_instance()
        self.write(other.root/'home'/'.npmrc', b'registry=local')
        seen = []
        def review(records, items=None):
            seen.append(items)
            return True
        import_profile(other, path, 'copy', review, log=lambda _:None)
        states = {item['path']: (item['state'], item['own']) for item in seen[0]}
        self.assertEqual(states['.npmrc'], ('different', False))
        self.assertEqual(states['.vscode/settings.json'], ('new', True))
        self.assertEqual((other.root/'home'/'.npmrc').read_bytes(), b'registry=local')
        self.assertEqual((other.root/'home'/'.m2'/'settings.xml').read_bytes(), b'<settings/>')
        self.assertEqual((other.root/'workspaces'/'copy'/'.vscode'/'sub'/'launch.json').read_bytes(), b'[]')
        self.assertEqual(other.profile('copy')['files'], manifest['files'])
        self.assertEqual(other.profile('copy')['env'], {})
        # Sobrescribir sólo lo marcado, archivo a archivo.
        target = str(other.root/'home'/'.npmrc')
        import_profile(other, path, 'second', lambda records, items=None: [target], log=lambda _:None)
        self.assertEqual((other.root/'home'/'.npmrc').read_bytes(), b'registry=source')

    def test_cancelled_file_review_creates_nothing(self):
        from mito.profile_files import save
        self.engine.create_profile('source')
        self.write(self.engine.root/'home'/'.gitconfig', b'[user]')
        save(self.engine,'source',[{'root':'home','path':'.gitconfig'}])
        path = export_profile(self.engine,'source',self.root/'source.zip',log=lambda _:None)
        other = self.other_instance()
        with self.assertRaises(YitoError):
            import_profile(other, path, 'copy', lambda records, items=None: False, log=lambda _:None)
        self.assertFalse((other.root/'home'/'.gitconfig').exists())
        self.assertNotIn('copy', other.profiles())

    def test_export_limit_rejects_heavy_content_without_leaving_files(self):
        from mito.profile_files import save, MAX_BYTES
        self.engine.create_profile('source')
        self.write(self.engine.root/'home'/'.m2'/'repository'/'lib.jar', b'0' * (MAX_BYTES + 1))
        save(self.engine,'source',[{'root':'home','path':'.m2'}])
        destination = self.root/'heavy.zip'
        with self.assertRaises(YitoError):
            export_profile(self.engine,'source',destination,log=lambda _:None)
        self.assertFalse(destination.exists())

    def test_export_never_follows_junctions(self):
        import subprocess
        from mito.profile_files import save
        self.engine.create_profile('source')
        outside = self.root/'host-data'
        self.write(outside/'secret.txt', b'host')
        config = self.engine.root/'home'/'.config'
        self.write(config/'app.json', b'{}')
        link = config/'host'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest('No se pudo crear una junction')
        import os
        # rmdir quita sólo el punto de reanálisis, nunca el contenido del destino.
        self.addCleanup(lambda: link.exists() and os.rmdir(link))
        save(self.engine,'source',[{'root':'home','path':'.config'}])
        logs = []
        path = export_profile(self.engine,'source',self.root/'links.zip',log=logs.append)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()), ['files/home/.config/app.json','mito-profile.json'])
        self.assertTrue(any('.config/host' in line for line in logs))
        save(self.engine,'source',[{'root':'home','path':'.config/host/secret.txt'}])
        with self.assertRaises(YitoError):
            export_profile(self.engine,'source',self.root/'through.zip',log=lambda _:None)

    def test_import_rejects_unsafe_archives(self):
        manifest = {'format':1,'apps':[],'files':[{'root':'home','path':'.npmrc'}]}
        cases = {
            'undeclared': [('files/home/other.cfg', b'x')],
            'traversal': [('files/home/../escape', b'x')],
            'outside files': [('readme.txt', b'x')],
            'unknown root': [('files/host/.npmrc', b'x')],
            'duplicate case': [('files/home/.npmrc', b'x'), ('files/home/.NPMRC', b'x')],
        }
        for case, members in cases.items():
            path = self.root/f'{case}.zip'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('mito-profile.json', json.dumps(manifest))
                for name, content in members:
                    archive.writestr(name, content)
            with self.subTest(case=case), self.assertRaises(YitoError):
                read_export(path)
        for bad in ({'format':2,'apps':[]}, {'format':1,'apps':[],'env':{'A':'b'}},
                    {'format':1,'apps':[],'files':[{'root':'persist','app':'main/tool','path':'x'}]}):
            path = self.root/'manifest.zip'
            path.unlink(missing_ok=True)
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('mito-profile.json', json.dumps(bad))
            with self.subTest(manifest=bad), self.assertRaises(YitoError):
                read_export(path)

    def test_persist_destinations_follow_generic_isolation(self):
        from mito.profile_files import plan
        other = self.other_instance()
        isolated = {'key':'main/tool','manifest':dict(self.manifest, persist='data', shortcuts=[['app.exe','Tool']])}
        shared = {'key':'main/lib','manifest':dict(self.manifest, persist='data')}
        self.write(other.root/'home'/'persist'/'main'/'lib'/'data'/'conf.txt', b'local')
        items = plan(other, 'copy', {'main/tool':isolated, 'main/lib':shared},
                     {('persist','main/tool','data/conf.txt'): b'remote', ('persist','main/lib','data/conf.txt'): b'remote'})
        by_app = {item['app']: item for item in items}
        self.assertEqual(by_app['main/tool']['target'], other.root/'home/profiles/copy/persist/main/tool/data/conf.txt')
        self.assertTrue(by_app['main/tool']['own'])
        self.assertEqual((by_app['main/lib']['state'], by_app['main/lib']['own']), ('different', False))

    def test_failed_write_rolls_back_created_and_overwritten_files(self):
        import os
        from mito import profile_files
        other = self.other_instance()
        home = other.root/'home'
        self.write(home/'keep.cfg', b'local')
        contents = {('home', None, 'keep.cfg'): b'remote', ('home', None, 'new/a.cfg'): b'a',
                    ('home', None, 'new/b.cfg'): b'b'}
        items = profile_files.plan(other, 'copy', {}, contents)
        real, calls = os.replace, []
        def failing(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('disk full')
            return real(source, target)
        with patch('mito.profile_files.os.replace', failing), self.assertRaises(OSError):
            profile_files.write(other, 'copy', {}, items, {str(home/'keep.cfg')}, lambda _:None)
        self.assertEqual((home/'keep.cfg').read_bytes(), b'local')
        self.assertFalse((home/'new').exists())

    def test_import_rejects_profiles_with_extra_data_and_invalid_identifiers(self):
        for data in ({'apps':[], 'env':{'TOKEN':'secret'}}, {'apps':['../app']}, {'apps':['https://example.org']}, {'apps':{}},
                     {'apps':['main/tool'],'pinned':{'main/other':'1'}}, {'apps':['main/tool'],'pinned':{'main/tool':'../1'}},
                     {'apps':['main/tool'],'pinned':{'main/tool':1}}, {'pinned':{}}):
            write_json(self.file,data)
            with self.subTest(data=data), self.assertRaises(YitoError):
                read_export(self.file)
