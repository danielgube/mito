"""Versiones previas: histórico Git del bucket y autoupdate, sin red real."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mito import versions
from mito.catalog import Catalog
from mito.storage import YitoError, write_json


MANIFEST = {
    'version': '6.4.0',
    'url': 'https://example.org/old/app-6.4.0.zip',
    'architecture': {
        '64bit': {'url': 'https://releases.example.org/desktop/6.4.0/app-6.4.0-win-x64.zip', 'hash': 'a' * 64},
        'arm64': {'url': 'https://releases.example.org/desktop/6.4.0/app-6.4.0-win-arm64.zip', 'hash': 'b' * 64},
    },
    'bin': 'App.exe',
    'autoupdate': {
        'architecture': {
            '64bit': {'url': 'https://releases.example.org/desktop/$version/app-$version-win-x64.zip'},
            'arm64': {'url': 'https://releases.example.org/desktop/$version/app-$version-win-arm64.zip'},
        },
        'hash': {'url': 'https://github.com/example/app/releases/tag/v$version', 'regex': '(?sm)$basename.*?$sha256'},
    },
}


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_json(self.root / 'core/buckets/extras.json',
                   {'schema': 1, 'source': 'https://github.com/Owner/Bucket', 'apps': {'app': MANIFEST}})
        self.catalog = Catalog(self.root)

    def test_commit_messages_and_version_boundaries(self):
        cases = {
            'app: Update to version 5.1.1, Fix autoupdate': '5.1.1',
            'app@5.4.0: Fix hash (Closes #11460)': '5.4.0',
            'Add App 4.2.0 (#1716)': '4.2.0',
            'app: Update to version 2024.10.1-beta\n\nbody 9.9.9': '2024.10.1-beta',
            'app: Fix autoupdate': None,
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(versions.message_version(message), expected)
        self.assertTrue(versions.mentions('app: Update to version 5.1.1', '5.1.1'))
        self.assertFalse(versions.mentions('app: Update to version 5.1.10', '5.1.1'))
        self.assertFalse(versions.mentions('app: Update to version 15.1.1', '5.1.1'))
        self.assertFalse(versions.mentions('app: Update to version 5.1.1.2', '5.1.1'))

    def test_history_lists_unique_versions_and_picks_corrected_manifest(self):
        commits = [
            {'sha': '1' * 40, 'commit': {'message': 'app: Update to version 6.4.0', 'author': {'date': '2024-05-01T00:00:00Z'}}},
            {'sha': '2' * 40, 'commit': {'message': 'app@5.4.0: Fix hash', 'author': {'date': '2023-02-01T00:00:00Z'}}},
            {'sha': '3' * 40, 'commit': {'message': 'app: Update to version 5.4.0', 'author': {'date': '2023-01-01T00:00:00Z'}}},
            {'sha': '4' * 40, 'commit': {'message': 'app: Fix autoupdate', 'author': {'date': '2022-01-01T00:00:00Z'}}},
        ]
        fixed = dict(MANIFEST, version='5.4.0', url='https://example.org/5.4.0.zip', hash='c' * 64)
        requested = []

        def fetch(root, url, max_bytes=0):
            requested.append(url)
            if url.startswith('https://api.github.com/repos/Owner/Bucket/commits?'):
                self.assertIn('path=bucket%2Fapp.json', url)
                return json.dumps(commits)
            if url == f'https://raw.githubusercontent.com/Owner/Bucket/{"2" * 40}/bucket/app.json':
                return json.dumps(fixed)
            raise AssertionError(url)

        with patch.object(versions, 'fetch_text', side_effect=fetch):
            found = versions.history(self.root, self.catalog, 'extras/app')
            self.assertEqual([item['version'] for item in versions.history_versions(found)], ['6.4.0', '5.4.0'])
            manifest, pin = versions.resolve(self.root, self.catalog, 'extras/app', '5.4.0', found=found, log=lambda _: None)
        self.assertEqual(manifest, fixed)
        self.assertEqual(pin, {'version': '5.4.0', 'source': 'git', 'commit': '2' * 40})
        # Un único listado (menos de 100 commits) y un único manifiesto descargado.
        self.assertEqual(len(requested), 2)

    def test_history_loads_in_batches_and_searches_older_typed_versions(self):
        size = versions.HISTORY_PAGE
        def page_commits(page):
            start = (page - 1) * size
            return [{'sha': f'{start + i:040x}', 'commit': {'message': f'app: Update to version 1.0.{1000 - start - i}',
                                                           'author': {'date': '2024-01-01T00:00:00Z'}}} for i in range(size)]
        pages = []
        old = dict(MANIFEST, version='1.0.880')

        def fetch(root, url, max_bytes=0):
            if url.startswith('https://api.github.com/'):
                page = int(url.rsplit('page=', 1)[1])
                pages.append(page)
                return json.dumps(page_commits(page))
            if url.startswith('https://raw.githubusercontent.com/Owner/Bucket/'):
                return json.dumps(old)
            raise AssertionError(url)

        with patch.object(versions, 'fetch_text', side_effect=fetch):
            found = versions.history(self.root, self.catalog, 'extras/app')
            # Una sola página basta para las primeras 20 versiones.
            self.assertEqual(pages, [1])
            self.assertEqual(len(versions.history_versions(found, versions.HISTORY_BATCH)), 20)
            self.assertTrue(versions.has_more(found))
            # Lo ya descargado cubre la segunda tanda; la tercera pide otra página.
            versions.load_versions(self.root, found, 40)
            self.assertEqual(pages, [1])
            versions.load_versions(self.root, found, 60)
            self.assertEqual(pages, [1, 2])
            # Una versión escrita que no está en lo cargado se busca más atrás.
            manifest, pin = versions.resolve(self.root, self.catalog, 'extras/app', '1.0.880', found=found, log=lambda _: None)
        self.assertEqual(pages, [1, 2, 3])
        self.assertEqual(manifest['version'], '1.0.880')
        self.assertEqual(pin['source'], 'git')

    def test_local_bucket_has_no_history(self):
        write_json(self.root / 'core/buckets/local.json', {'schema': 1, 'source': str(self.root), 'apps': {'app': MANIFEST}})
        self.assertIsNone(versions.history(self.root, self.catalog, 'local/app'))

    def test_autoupdate_substitutes_version_and_extracts_hash(self):
        digest = 'D' * 64
        page = f'<li>app-5.1.0-win-arm64.zip {"e" * 64}</li><li>app-5.1.0-win-x64.zip</li><code>{digest}</code>'

        def fetch(root, url, max_bytes=0):
            self.assertEqual(url, 'https://github.com/example/app/releases/tag/v5.1.0')
            return page

        with patch.object(versions, 'fetch_text', side_effect=fetch):
            manifest, pin = versions.resolve(self.root, self.catalog, 'extras/app', '5.1.0', found={}, log=lambda _: None)
        x64 = manifest['architecture']['64bit']
        self.assertEqual(manifest['version'], '5.1.0')
        self.assertEqual(x64['url'], 'https://releases.example.org/desktop/5.1.0/app-5.1.0-win-x64.zip')
        self.assertEqual(x64['hash'], digest.lower())
        self.assertEqual(manifest['architecture']['arm64']['url'], 'https://releases.example.org/desktop/5.1.0/app-5.1.0-win-arm64.zip')
        self.assertNotIn('hash', manifest['architecture']['arm64'])
        self.assertEqual(pin, {'version': '5.1.0', 'source': 'autoupdate'})
        self.assertEqual(MANIFEST['architecture']['64bit']['hash'], 'a' * 64, 'el catálogo no se modifica')

    def test_autoupdate_without_hash_leaves_download_unverified(self):
        logs = []
        with patch.object(versions, 'fetch_text', return_value='no hashes here'):
            manifest = versions.manifest_from_autoupdate(self.root, MANIFEST, '5.1.0', logs.append)
        self.assertNotIn('hash', manifest['architecture']['64bit'])
        self.assertEqual(len(logs), 1)
        self.assertIn('app-5.1.0-win-x64.zip', logs[0])

    def test_autoupdate_variables_and_unsupported_cases(self):
        values = versions.version_variables('1.2.3-beta')
        self.assertEqual((values['majorVersion'], values['minorVersion'], values['patchVersion']), ('1', '2', '3'))
        self.assertEqual(values['preReleaseVersion'], 'beta')
        self.assertEqual(versions.substitute('v$Version/$underscoreVersion/${cleanVersion}', versions.version_variables('1.2.3')),
                         'v1.2.3/1_2_3/123')
        with self.assertRaises(YitoError):
            versions.manifest_from_autoupdate(self.root, {'version': '1', 'url': 'https://x/a.zip'}, '2')
        checkver = dict(MANIFEST, autoupdate={'url': 'https://x/$matchBuild/a-$version.zip'})
        checkver = {key: value for key, value in checkver.items() if key != 'architecture'}
        with self.assertRaises(YitoError):
            versions.manifest_from_autoupdate(self.root, checkver, '2', lambda _: None)
        with self.assertRaises(YitoError):
            versions.validate_version('../1')


if __name__ == '__main__':
    unittest.main()
