from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.bucket_resources import sources, prepare
from mito.catalog import Catalog
from mito.engine import Engine
from mito.storage import YitoError, write_json


class BucketResourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.catalog = Catalog(self.root)
        self.origin = self.root/'source'
        (self.origin/'scripts/tool').mkdir(parents=True)
        (self.origin/'scripts/tool/template.reg').write_text('fixture', encoding='utf-8')
        self.manifest = {'version': '1', 'url': 'https://example.org/tool.zip', 'hash': 'a'*64,
                         'pre_install': ['Copy-Item "$bucketsdir\\$bucket\\scripts\\$app\\template.reg" "$dir"']}
        write_json(self.catalog.folder/'test.json', {'source': str(self.origin), 'apps': {'tool': self.manifest}})

    def test_resolves_literal_current_and_cross_bucket_references(self):
        write_json(self.catalog.folder/'other.json', {'source': str(self.origin), 'apps': {}})
        self.assertEqual(sources(self.catalog, 'test/tool', self.manifest), {'test': str(self.origin)})
        for script in ('$bucketsdir/other/scripts/tool/x', '${bucketsdir}/${bucket}/scripts/x'):
            result = sources(self.catalog, 'test/tool', dict(self.manifest, pre_install=script))
            self.assertEqual(result, {'other' if '/other/' in script else 'test': str(self.origin)})
        for script in ('$bucketsdir/$computed/scripts/x', '$bucketsdir/missing/scripts/x'):
            with self.assertRaises(YitoError):
                sources(self.catalog, 'test/tool', dict(self.manifest, pre_install=script))
        self.assertNotEqual(self.catalog.compatibility('test/tool', dict(
            self.manifest, pre_install='$bucketsdir/$computed/scripts/x')), 'Compatible')

    def test_local_resources_are_copied_and_shared_between_hook_phases(self):
        destination = self.root/'stage/hook-buckets'
        destination.parent.mkdir()
        prepare(self.root, sources(self.catalog, 'test/tool', self.manifest), destination, lambda _: None)
        (self.origin/'scripts/tool/template.reg').write_text('changed')
        self.assertEqual((destination/'test/scripts/tool/template.reg').read_text(), 'fixture')

    def test_remote_snapshot_and_traversal_rejection(self):
        for bad in (False, True):
            def download(url, target, **kwargs):
                with zipfile.ZipFile(target, 'w') as output:
                    output.writestr('repo/scripts/tool/template.reg', b'fixture')
                    if bad:
                        output.writestr('repo/scripts/../../escape', b'bad')
            destination = self.root/('bad' if bad else 'good')
            with patch('mito.bucket_resources.download', side_effect=download):
                if bad:
                    with self.assertRaises(YitoError):
                        prepare(self.root, {'test': 'https://github.com/example/repo'}, destination, lambda _: None)
                    self.assertFalse((destination/'test/scripts/tool/template.reg').exists())
                else:
                    prepare(self.root, {'test': 'https://github.com/example/repo'}, destination, lambda _: None)
                    self.assertEqual((destination/'test/scripts/tool/template.reg').read_bytes(), b'fixture')

    def test_unverified_resources_in_dependency_require_approval_before_download(self):
        write_json(self.catalog.folder/'test.json', {'source': 'https://github.com/example/repo',
                   'apps': {'tool': self.manifest,
                            'parent': {'version': '1', 'url': 'https://example.org/parent.zip',
                                       'hash': 'b'*64, 'depends': 'test/tool'}}})
        engine = Engine(self.root)
        plan = engine.installation_plan('test/parent')
        self.assertEqual(plan['unverified'], ['test/tool · recursos del catálogo test'])
        with patch('mito.engine.download') as payload, patch('mito.bucket_resources.download') as resources:
            for confirm in (None, lambda _: False):
                with self.assertRaisesRegex(YitoError, 'sin hash no confirmadas'):
                    engine.install('test/parent', log=lambda _: None, confirm_unverified=confirm)
            payload.assert_not_called()
            resources.assert_not_called()

    def test_resources_prepare_pre_install_and_installer_with_real_powershell(self):
        engine = Engine(self.root)
        self.manifest['installer'] = {'script': [
            'if (!(Test-Path "$dir/template.reg")) { throw "pre_install did not run" }',
            'Copy-Item "$bucketsdir/test/scripts/tool/template.reg" "$dir/second.reg"']}
        write_json(self.catalog.folder/'test.json', {'source': str(self.origin), 'apps': {'tool': self.manifest}})
        def download(url, target, *args, **kwargs):
            with zipfile.ZipFile(target, 'w') as output:
                output.writestr('tool.exe', b'inert')
        with patch('mito.engine.download', side_effect=download), patch('mito.terminal.prepare'):
            engine.install('test/tool', log=lambda _: None)
        payload = self.root/'core/apps/test/tool'
        self.assertEqual((payload/'template.reg').read_text(), 'fixture')
        self.assertEqual((payload/'second.reg').read_text(), 'fixture')

    def test_resource_failure_preserves_previous_installation(self):
        engine = Engine(self.root)
        original = dict(self.manifest, pre_install=None)
        write_json(self.catalog.folder/'test.json', {'source': str(self.origin), 'apps': {'tool': original}})
        def download(url, target, *args, **kwargs):
            with zipfile.ZipFile(target, 'w') as output:
                output.writestr('tool.exe', b'original')
        with patch('mito.engine.download', side_effect=download), patch('mito.terminal.prepare'):
            engine.install('test/tool', log=lambda _: None)
            before = engine.installed()
            write_json(self.catalog.folder/'test.json', {'source': str(self.root/'missing'),
                       'apps': {'tool': dict(self.manifest, version='2')}})
            with self.assertRaises(YitoError):
                engine.install('test/tool', log=lambda _: None)
        self.assertEqual(engine.installed(), before)
        self.assertEqual((self.root/'core/apps/test/tool/tool.exe').read_bytes(), b'original')
