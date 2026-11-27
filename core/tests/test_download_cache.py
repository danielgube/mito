import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.engine import Engine
from mito.storage import YitoError, mutation, write_json


def fixture_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('bin/tool.exe', (1980, 1, 1, 0, 0, 0)), b'fixture')
    return buffer.getvalue()


class DownloadCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.content = fixture_zip()
        manifest = {'version': '1.0', 'url': 'https://example.org/tool.zip',
                    'hash': hashlib.sha256(self.content).hexdigest(), 'bin': 'bin/tool.exe'}
        write_json(self.root/'core/buckets/main.json', {'apps': {'tool': manifest}})
        self.calls = []

    def fetch(self, url, path, expected, **kwargs):
        self.calls.append(url)
        # Downloading must not hold the mutation lock that publishing uses.
        with mutation(self.root):
            pass
        path.write_bytes(self.content)

    def cache(self):
        return self.root/'core/cache/downloads/main/tool'

    def test_download_happens_outside_the_mutation_lock_and_cache_is_removed_after_publishing(self):
        with patch('mito.engine.download', self.fetch), patch('mito.terminal.prepare'):
            self.engine.install('main/tool', log=lambda _: None)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.root/'core/apps/main/tool/bin/tool.exe').is_file())
        self.assertFalse(self.cache().exists())

    def test_failed_publication_keeps_the_download_for_the_next_attempt(self):
        with patch('mito.engine.download', self.fetch), patch('mito.terminal.prepare'), \
                patch('mito.archives.extract', side_effect=YitoError('application in use')):
            with self.assertRaises(YitoError):
                self.engine.install('main/tool', log=lambda _: None)
        self.assertEqual(len(list(self.cache().rglob('download-0'))), 1)
        self.assertFalse(self.engine.installed())
        logs = []
        with patch('mito.engine.download', side_effect=AssertionError('downloaded again')), \
                patch('mito.terminal.prepare'):
            self.engine.install('main/tool', log=logs.append)
        self.assertTrue(any('ya disponible' in line for line in logs))
        self.assertFalse(self.cache().exists())

    def test_corrupted_cached_file_is_downloaded_again(self):
        from mito.catalog import fingerprint
        from mito.download_cache import entry
        manifest = self.engine.catalog.resolve('main/tool')[1]
        cached = entry(self.root, 'main/tool', manifest)/'download-0'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'tampered')
        with patch('mito.engine.download', self.fetch), patch('mito.terminal.prepare'):
            self.engine.install('main/tool', log=lambda _: None)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(entry(self.root, 'main/tool', manifest).name, fingerprint(manifest)[:16])

    def test_a_second_process_downloading_fails_without_waiting(self):
        from mito.download_cache import exclusive, lock_name
        # Another process holds the file lock of this component only.
        with mutation(self.root, lock_name('main/tool')):
            with self.assertRaisesRegex(YitoError, 'descargando'):
                with exclusive(self.root, 'main/tool'):
                    pass
            with exclusive(self.root, 'main/other'):
                pass

    def test_threads_of_this_process_wait_their_turn(self):
        import threading
        from mito.download_cache import exclusive
        order, inside = [], threading.Event()
        def background():
            with exclusive(self.root, 'main/tool'):
                inside.set()
                order.append('background')
                import time
                time.sleep(0.5)
        thread = threading.Thread(target=background)
        thread.start()
        inside.wait(5)
        logs = []
        with exclusive(self.root, 'main/tool', logs.append):
            order.append('foreground')
        thread.join()
        self.assertEqual(order, ['background', 'foreground'])
        self.assertTrue(any('Esperando' in line for line in logs))

    def test_removing_a_component_discards_its_downloads(self):
        from mito.components import remove
        with patch('mito.engine.download', self.fetch), patch('mito.terminal.prepare'):
            self.engine.install('main/tool', log=lambda _: None)
        stale = self.cache()/'0123456789abcdef'/'download-0.partial'
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b'partial')
        with patch('mito.terminal.prepare'):
            remove(self.engine, 'main/tool', log=lambda _: None)
        self.assertFalse(self.cache().exists())


class ResumeAcrossSessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'download-0.partial'
        self.content = b'0123456789' * 1000
        self.expected = hashlib.sha256(self.content).hexdigest()

    def test_complete_partial_with_hash_needs_no_network(self):
        from mito.transfer import download
        self.path.write_bytes(self.content)
        with patch('mito.transfer._Stream', side_effect=AssertionError('network used')):
            download('https://example.org/tool.zip', self.path, self.expected, resume=True)
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_partial_with_hash_resumes_with_range(self):
        from mito.transfer import download
        self.path.write_bytes(self.content[:4000])
        seen = []

        class Stopped(Exception):
            pass

        class Stream:
            def __init__(self, opener, request, timeout=60):
                seen.append(dict(request.header_items()))
            def next(self, check=None):
                raise Stopped()
            def close(self):
                pass
        with patch('mito.transfer._Stream', Stream), self.assertRaises(Stopped):
            download('https://example.org/tool.zip', self.path, self.expected, resume=True, attempts=1)
        self.assertEqual(seen[0].get('Range'), 'bytes=4000-')

    def test_partial_without_hash_starts_again(self):
        from mito.transfer import download
        self.path.write_bytes(b'unverifiable')
        seen = []

        class Stream:
            def __init__(self, opener, request, timeout=60):
                seen.append(dict(request.header_items()))
            def next(self, check=None):
                raise YitoError('stop')
            def close(self):
                pass
        with patch('mito.transfer._Stream', Stream), self.assertRaises(YitoError):
            download('https://example.org/tool.zip', self.path, None, resume=True, attempts=1)
        self.assertNotIn('Range', seen[0])
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
