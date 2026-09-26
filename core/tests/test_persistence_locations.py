from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mito.persistence import locations, open_location
from mito.storage import YitoError


class PersistenceLocationTests(unittest.TestCase):
    def test_paths_follow_manifest_scope_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            lock={'key':'main/editor','manifest':{'persist':[['data','saved'],'config.ini'],'shortcuts':[['app.exe','App']]}}
            self.assertEqual(locations(root,lock,'one')[0][1],root/'home/profiles/one/persist/main/editor/saved')
            self.assertNotEqual(locations(root,lock,'one'),locations(root,lock,'two'))
            lock['manifest'].pop('shortcuts')
            self.assertEqual(locations(root,lock)[1][1],root/'home/persist/main/editor/config.ini')
            self.assertFalse((root/'home').exists())

    def test_files_open_parent_folder_and_missing_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            file=root/'config.ini'
            file.touch()
            with patch('mito.persistence.subprocess.Popen') as process:
                open_location(file)
                process.assert_called_once_with(['explorer.exe',str(root)])
            with self.assertRaises(YitoError):open_location(root/'missing')
