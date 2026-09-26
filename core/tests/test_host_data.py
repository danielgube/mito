from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito import host_apps, host_data
from mito.storage import YitoError


class HostDataTests(unittest.TestCase):
    def test_manual_junction_detected_without_writing_and_unlinked_safely(self):
        folder = self.data()
        host_data.link(self.root, self.identity, 'data', folder)
        host_data.save(self.root, {})  # Same filesystem state as a manual mklink /J.
        with patch('mito.host_data.save') as save:
            self.assertTrue(host_data.linked(self.root, self.identity, 'data'))
            self.assertEqual(host_data.entries(self.root, self.identity, 'data')[0]['target'], str(folder))
            save.assert_not_called()
        host_data.unlink(self.root, self.identity, 'data')
        self.assertFalse(host_data.linked(self.root, self.identity, 'data'))
        self.assertEqual((folder/'session').read_text(), 'host data')

    def test_manual_junction_reused_idempotently_and_removed_with_application(self):
        folder = self.data()
        host_data.link(self.root, self.identity, 'data', folder)
        host_data.save(self.root, {})
        with patch('mito.host_data.subprocess.run') as create:
            host_data.link(self.root, self.identity, 'data', folder)
            host_data.link(self.root, self.identity, 'data', folder)
            create.assert_not_called()
        host_data.save(self.root, {})
        host_apps.remove(self.root, self.identity)
        self.assertEqual((folder/'session').read_text(), 'host data')

    def test_unrelated_manual_junction_not_recognized_or_replaced(self):
        folder = self.data()
        host_data.link(self.root, self.identity, 'data', folder)
        host_data.save(self.root, {})
        # Same portable name, but the expected host folder is now elsewhere.
        elsewhere = self.folder/'elsewhere'
        elsewhere.mkdir()
        with patch('mito.host_data.host_folder', return_value=elsewhere):
            self.assertFalse(host_data.linked(self.root, self.identity, 'data'))
            candidate = elsewhere/folder.name
            candidate.mkdir()
            with self.assertRaises(YitoError):
                host_data.link(self.root, self.identity, 'data', candidate, replace=True)
        host_data.unlink(self.root, self.identity, 'data')
        self.assertEqual((folder/'session').read_text(), 'host data')

    def test_picker_starts_in_category_appdata(self):
        for kind in ('cache', 'data'):
            with patch('mito.host_data.subprocess.run') as run:
                run.return_value.stdout = ''
                self.assertIsNone(host_data.choose_folder(kind))
                self.assertEqual(run.call_args.kwargs['input'], str(self.host))
                self.assertIn('$dialog.SelectedPath = [Console]::In.ReadToEnd()', run.call_args.args[0][-1])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.root = self.folder/'mito'
        self.host = self.folder/'host'
        self.host.mkdir()
        self.exe = self.host/'Programs/Opera/opera.exe'
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'fixture')
        self.identity = host_apps.add(self.root, self.exe)
        picker = patch('mito.host_data.host_folder', return_value=self.host)
        picker.start()
        self.addCleanup(picker.stop)

    def data(self, name='Opera Software'):
        folder = self.host/name
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'session').write_text('host data')
        return folder

    def test_search_keeps_outer_matches_and_excludes_executable_tree(self):
        folder = self.data()
        (folder/'Opera Stable/Opera').mkdir(parents=True)
        nested = self.host/'Vendor/OPERA Data'
        nested.mkdir(parents=True)
        self.assertEqual(host_data.candidates(self.root, self.identity, 'data'), [folder, nested])

    def test_real_junction_unlink_and_application_removal_preserve_host(self):
        folder = self.data()
        for kind in ('data', 'cache'):
            host_data.link(self.root, self.identity, kind, folder)
            self.assertTrue(host_data.linked(self.root, self.identity, kind))
            path, _, _ = host_data.plan(self.root, kind, folder)
            self.assertTrue(path.is_junction())
            self.assertEqual((path/'session').read_text(), 'host data')
        host_data.unlink(self.root, self.identity, 'data')
        self.assertFalse(host_data.linked(self.root, self.identity, 'data'))
        host_apps.remove(self.root, self.identity)
        self.assertFalse(host_data.linked(self.root, self.identity, 'cache'))
        self.assertEqual((folder/'session').read_text(), 'host data')
        self.assertTrue(self.exe.is_file())

    def test_replace_requires_confirmation_and_failure_restores_local_data(self):
        folder = self.data()
        path, _, _ = host_data.plan(self.root, 'data', folder)
        path.mkdir(parents=True)
        (path/'local').write_text('portable')
        with self.assertRaises(YitoError):
            host_data.link(self.root, self.identity, 'data', folder)
        with patch('mito.host_data.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'junction')):
            with self.assertRaises(subprocess.CalledProcessError):
                host_data.link(self.root, self.identity, 'data', folder, replace=True)
        self.assertEqual((path/'local').read_text(), 'portable')
        host_data.link(self.root, self.identity, 'data', folder, replace=True)
        self.assertFalse((path/'local').exists())
        host_apps.remove(self.root, self.identity)
        self.assertEqual((folder/'session').read_text(), 'host data')

    def test_manual_outside_appdata_and_conflicting_application(self):
        folder = self.folder/'Manual data'
        folder.mkdir()
        path, _, relative = host_data.plan(self.root, 'data', folder)
        self.assertEqual(relative, folder.name)
        host_data.link(self.root, self.identity, 'data', folder)
        exe = self.host/'other.exe'
        exe.write_bytes(b'fixture')
        other = host_apps.add(self.root, exe)
        with self.assertRaises(YitoError):
            host_data.link(self.root, other, 'data', folder, replace=True)
        host_apps.remove(self.root, other)
        self.assertTrue(path.is_junction())
        host_apps.remove(self.root, self.identity)
        self.assertTrue(folder.is_dir())

    def test_reject_roots_internal_paths_and_traversal(self):
        for folder in (self.host, self.root):
            with self.assertRaises(YitoError):
                host_data.plan(self.root, 'data', folder)
        with self.assertRaises(YitoError):
            host_data.destination(self.root, 'data', '../outside')

    def test_changed_junction_is_not_removed(self):
        folder = self.data()
        host_data.link(self.root, self.identity, 'data', folder)
        path, _, _ = host_data.plan(self.root, 'data', folder)
        path.rmdir()
        path.mkdir()
        (path/'foreign').write_text('keep')
        with self.assertRaises(YitoError):
            host_apps.remove(self.root, self.identity)
        self.assertEqual((path/'foreign').read_text(), 'keep')
        self.assertIn(self.identity, host_apps.inventory(self.root))
