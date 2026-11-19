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

    def test_import_rejects_profiles_with_extra_data_and_invalid_identifiers(self):
        for data in ({'apps':[], 'env':{'TOKEN':'secret'}}, {'apps':['../app']}, {'apps':['https://example.org']}, {'apps':{}},
                     {'apps':['main/tool'],'pinned':{'main/other':'1'}}, {'apps':['main/tool'],'pinned':{'main/tool':'../1'}},
                     {'apps':['main/tool'],'pinned':{'main/tool':1}}, {'pinned':{}}):
            write_json(self.file,data)
            with self.subTest(data=data), self.assertRaises(YitoError):
                read_export(self.file)
