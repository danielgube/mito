import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mito.engine import move_whole


class MoveWholeTests(unittest.TestCase):
    """Extracted trees are published with one rename unless content must be merged."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.source = base / 'expanded-0' / 'jdk'
        (self.source / 'bin').mkdir(parents=True)
        (self.source / 'bin/java.exe').write_bytes(b'exe')
        self.payload = base / 'payload'

    def test_empty_or_missing_target_is_replaced_with_one_rename(self):
        self.payload.mkdir()
        with patch('mito.engine.os.replace', wraps=os.replace) as replace:
            self.assertTrue(move_whole(self.source, self.payload))
        replace.assert_called_once()
        self.assertEqual((self.payload / 'bin/java.exe').read_bytes(), b'exe')
        self.assertFalse(self.source.exists())
        nested = self.payload.parent / 'other' / 'sub'
        source = self.payload.parent / 'expanded-1'
        (source / 'lib').mkdir(parents=True)
        self.assertTrue(move_whole(source, nested))
        self.assertTrue((nested / 'lib').is_dir())

    def test_non_empty_target_keeps_per_file_merge(self):
        (self.payload / 'docs').mkdir(parents=True)
        self.assertFalse(move_whole(self.source, self.payload))
        self.assertTrue((self.source / 'bin/java.exe').exists())
        self.assertTrue((self.payload / 'docs').is_dir())

    def test_failed_rename_restores_target_and_source(self):
        self.payload.mkdir()
        with patch('mito.engine.os.replace', side_effect=PermissionError('locked')):
            self.assertFalse(move_whole(self.source, self.payload))
        self.assertTrue(self.payload.is_dir())
        self.assertTrue((self.source / 'bin/java.exe').exists())


if __name__ == '__main__':
    unittest.main()
