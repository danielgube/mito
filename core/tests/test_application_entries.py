from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mito.engine import Engine
from mito.storage import YitoError


class ApplicationEntryTests(unittest.TestCase):
    def test_shortcuts_without_persist_and_explicit_help(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root/'payload'
            payload.mkdir()
            for name in ['help.chm', 'Editor.exe']:
                (payload/name).touch()
            lock = {'path': 'payload', 'key': 'test/editor', 'manifest': {
                'version': '1', 'shortcuts': [['help.chm', 'Help'],
                                            ['Editor.exe', 'Editor', '--edit']]}}
            engine = Engine(root)
            self.assertTrue(engine.can_open(lock))
            self.assertEqual(engine.application_target(lock), ('Editor.exe', ['--edit']))
            self.assertEqual(engine.application_target(lock, 'Help'), ('help.chm', []))
            self.assertEqual(engine.application_name(lock, 'Help'), 'Help')
            self.assertEqual(len(engine.application_entries(lock)), 2)
            with self.assertRaises(YitoError):
                engine.application_target(lock, 'Missing')

    def test_coupled_launch_preserves_selection_before_arguments(self):
        from mito.terminal import launch_component
        with patch('mito.terminal.launch_application') as launch:
            launch_component(None, 'test', 'extras/editor', 'Help', ['argument'], shortcut='Help')
        self.assertEqual(launch.call_args.args[-1],
                         ['run', '--owned', '--profile', 'test', '--app', 'extras/editor',
                          '--shortcut', 'Help', '--', 'argument'])
