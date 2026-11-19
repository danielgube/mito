import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mito import host_exposure
from mito.storage import YitoError


class StubEngine:
    def __init__(self, root):
        self.root = root

    def installed(self):
        return {'main/mattermost': {'key': 'main/mattermost'}}

    def application_entries(self, lock):
        return [('Mattermost', 'Mattermost.exe', [])]


class HostExposureTests(unittest.TestCase):
    """Datos en host: junctions del AppData del host hacia home/AppData de MITO."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.root = self.folder/'mito'
        self.host = {'data': self.folder/'host/Roaming', 'cache': self.folder/'host/Local'}
        for path in self.host.values():
            path.mkdir(parents=True)
        self.engine = StubEngine(self.root)
        self.source = self.mito_data(self.root)
        known = patch('mito.host_exposure.host_folder', side_effect=lambda kind: self.host[kind])
        known.start()
        self.addCleanup(known.stop)
        self.addCleanup(self.remove_junctions)

    def mito_data(self, root):
        source = root/'home/AppData/Roaming/Mattermost'
        source.mkdir(parents=True, exist_ok=True)
        (source/'config.json').write_text('mito')
        return source

    def remove_junctions(self):
        for base in self.host.values():
            for path in base.iterdir() if base.exists() else ():
                if path.is_junction():
                    os.rmdir(path)

    def test_candidates_use_declared_executable_names_and_skip_junctions(self):
        local = self.root/'home/AppData/Local/mattermost-updater/nested'
        local.mkdir(parents=True)
        (self.root/'home/AppData/Roaming/Other/Mattermost').mkdir(parents=True)
        outside = self.folder/'outside'
        outside.mkdir()
        host_exposure.create_junction(self.root/'home/AppData/Roaming/Mattermost-linked', outside)
        found = host_exposure.candidates(self.engine, {'key': 'main/mattermost'})
        self.assertIn(('data', 'Mattermost'), found)
        self.assertIn(('data', str(Path('Other/Mattermost'))), found)
        self.assertIn(('cache', 'mattermost-updater'), found)
        self.assertNotIn(('data', 'Mattermost-linked'), found)
        os.rmdir(self.root/'home/AppData/Roaming/Mattermost-linked')

    def test_link_shares_mito_data_and_unlink_keeps_it(self):
        host = self.host['data']/'Mattermost'
        self.assertIsNone(host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost'))
        self.assertTrue(host.is_junction())
        self.assertEqual((host/'config.json').read_text(), 'mito')
        self.assertTrue(host_exposure.linked(self.root, 'main/mattermost'))
        # Repeating the operation is idempotent and creates nothing.
        with patch('mito.host_exposure.subprocess.run') as create:
            host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost')
            create.assert_not_called()
        host_exposure.unlink(self.root, 'main/mattermost')
        self.assertFalse(host.exists())
        self.assertFalse(host_exposure.linked(self.root, 'main/mattermost'))
        self.assertEqual((self.source/'config.json').read_text(), 'mito')
        self.assertEqual(host_exposure.registry(self.root), {})

    def test_existing_host_folder_is_set_aside_and_restored(self):
        host = self.host['data']/'Mattermost'
        host.mkdir()
        (host/'config.json').write_text('host')
        with self.assertRaises(YitoError):
            host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost')
        self.assertFalse(host.is_junction())
        moved = host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost', aside=True)
        self.assertEqual((moved/'config.json').read_text(), 'host')
        self.assertEqual((host/'config.json').read_text(), 'mito')
        host_exposure.unlink(self.root, 'main/mattermost')
        self.assertFalse(host.is_junction())
        self.assertEqual((host/'config.json').read_text(), 'host')
        self.assertFalse(moved.exists())

    def test_foreign_or_modified_junctions_are_never_replaced(self):
        other = self.folder/'other'
        other.mkdir()
        host = self.host['data']/'Mattermost'
        host_exposure.create_junction(host, other)
        with self.assertRaises(YitoError):
            host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost', aside=True)
        os.rmdir(host)
        host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost')
        os.rmdir(host)
        host_exposure.create_junction(host, other)
        with self.assertRaises(YitoError):
            host_exposure.unlink(self.root, 'main/mattermost')
        self.assertTrue(host.is_junction())

    def test_failed_junction_restores_host_folder(self):
        host = self.host['data']/'Mattermost'
        host.mkdir()
        (host/'config.json').write_text('host')
        with patch('mito.host_exposure.create_junction', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                host_exposure.link(self.engine, 'main/mattermost', 'data', 'Mattermost', aside=True)
        self.assertEqual((host/'config.json').read_text(), 'host')
        self.assertEqual(host_exposure.registry(self.root), {})

    def test_relocation_retargets_after_move_and_releases_after_copy(self):
        old = self.folder/'old'
        old_source = self.mito_data(old)
        host = self.host['data']/'Mattermost'
        host_exposure.create_junction(host, old_source)
        host_exposure.save(self.root, {'main/mattermost': {'data': [{'relative': 'Mattermost', 'backup': None}]}})
        self.assertEqual(host_exposure.relocate(self.root, old, copied=False, log=lambda message: None), 1)
        self.assertTrue(host_exposure.points_to(host, self.source))
        self.assertTrue(host_exposure.linked(self.root, 'main/mattermost'))
        os.rmdir(host)
        host_exposure.create_junction(host, old_source)
        self.assertEqual(host_exposure.relocate(self.root, old, copied=True, log=lambda message: None), 1)
        self.assertTrue(host_exposure.points_to(host, old_source))
        self.assertEqual(host_exposure.registry(self.root), {})


if __name__ == '__main__':
    unittest.main()
