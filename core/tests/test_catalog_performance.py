import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.catalog import Catalog
from mito.storage import YitoError, inside, write_json


class CatalogPerformanceTests(unittest.TestCase):
    def test_classification_does_not_resolve_host_paths(self):
        manifest = dict(version='1', url='https://example.org/tool.zip',
                        bin=[['bin/tool.exe', 'tool']], persist='data',
                        env_add_path='bin', extract_dir='payload')
        with patch.object(Path, 'resolve', side_effect=AssertionError('filesystem access')):
            self.assertEqual(Catalog(Path('C:/mito')).compatibility('main/tool', manifest), 'Compatible')
            for path in ('../escape', 'C:/absolute', '/rooted', '\\rooted', 'file:stream', 'CON', 'trailing.'):
                self.assertNotEqual(Catalog(Path('C:/mito')).compatibility(
                    'main/tool', dict(manifest, extract_dir=path)), 'Compatible')

    def test_inside_still_checks_physical_confinement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Path, 'resolve', side_effect=[root.parent, root]):
                with self.assertRaises(YitoError):
                    inside(root, 'safe-looking')

    def test_archive_layouts_and_no_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'source.zip'
            for folder in ('repo/', 'repo/bucket/'):
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr(folder + 'tool.json', '\ufeff' + json.dumps({'version': '1'}))
                    z.writestr('repo/docs/ignored.json', 'invalid json')
                self.assertEqual(Catalog._archive_manifests(archive), {'tool': {'version': '1'}})
                self.assertEqual(list(Path(tmp).iterdir()), [archive])

    def test_bad_archives_preserve_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = Catalog(root)
            previous = root / 'core/buckets/main.json'
            write_json(previous, {'apps': {'old': {'version': '1'}}})
            before = previous.read_bytes()
            for bad in ('repo/../escape', 'repo/CON', 'repo/bucket/TOOL.json', 'repo/link'):
                def download(url, target, **kwargs):
                    with zipfile.ZipFile(target, 'w') as z:
                        z.writestr('repo/bucket/tool.json', '{"version":"2"}')
                        entry = zipfile.ZipInfo(bad)
                        if bad.endswith('/link'):
                            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                        z.writestr(entry, '{}')
                with self.subTest(bad=bad), patch('mito.catalog.download', side_effect=download):
                    with self.assertRaises(YitoError):
                        catalog.import_bucket('main', 'https://github.com/example/catalog')
                    self.assertEqual(previous.read_bytes(), before)
