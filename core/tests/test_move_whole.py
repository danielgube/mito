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

    def test_rename_denied_while_antivirus_scans_is_retried_within_wait(self):
        self.payload.mkdir()
        now = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        calls = []
        real_replace = os.replace  # captured before patching the shared os module
        def replace(source, target):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(13, 'locked')
            return real_replace(source, target)
        outcome = {}
        with patch('mito.engine.os.replace', side_effect=replace):
            self.assertTrue(move_whole(self.source, self.payload, wait=10, sleep=sleep,
                                       clock=lambda: now[0], outcome=outcome))
        self.assertEqual(sleeps, [.25, .5])
        self.assertEqual(outcome['attempts'], 3)
        self.assertEqual((self.payload / 'bin/java.exe').read_bytes(), b'exe')

    def test_rename_still_denied_after_wait_falls_back_to_per_file(self):
        self.payload.mkdir()
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        outcome = {}
        with patch('mito.engine.os.replace', side_effect=PermissionError(13, 'locked')):
            self.assertFalse(move_whole(self.source, self.payload, wait=1, sleep=sleep,
                                        clock=lambda: now[0], outcome=outcome))
        self.assertLessEqual(now[0], 1)
        self.assertGreater(outcome['attempts'], 1)
        self.assertTrue(outcome['reason'].startswith('winerror-'))
        self.assertTrue(self.payload.is_dir())
        self.assertTrue((self.source / 'bin/java.exe').exists())


if __name__ == '__main__':
    unittest.main()
