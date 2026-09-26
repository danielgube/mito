from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from mito.inno import app_inventory, extract_inno, restore_declared_targets
from mito.hooks import expand_inno
from mito.storage import YitoError


def listing(*entries):
    return '\n'.join(['Size        Date/Time            Filename', '----------',
                      *(f'{size:10}  2026-09-15 12:00     {name}' for name, size in entries),
                      '----------'])


class InnoTests(unittest.TestCase):
    def test_hook_helper_extracts_app_inside_payload_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            payload = root/'payload'
            payload.mkdir()
            setup = payload/'setup.exe'
            setup.write_bytes(b'inert setup')

            def extract(archive, destination, actual_root):
                self.assertEqual((archive, actual_root), (setup, root))
                (destination/'bin').mkdir()
                (destination/'bin/tool.exe').write_bytes(b'tool')

            with patch('mito.inno.extract_inno', side_effect=extract):
                expand_inno(root, payload, setup, payload)
            self.assertEqual((payload/'bin/tool.exe').read_bytes(), b'tool')
            with self.assertRaises(YitoError):
                expand_inno(root, payload, setup, root/'outside')
            with self.assertRaises(YitoError):
                expand_inno(root, payload, setup, payload, '{sys}')

    def test_declared_variants_must_be_identical(self):
        for second in (b'editor', b'different'):
            with self.subTest(second=second), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'Editor,1.exe').write_bytes(b'editor')
                (root/'Editor,2.exe').write_bytes(second)
                (root/'Undeclared,1.exe').write_bytes(b'x')
                (root/'Undeclared,2.exe').write_bytes(b'x')
                if second == b'editor':
                    restore_declared_targets(root, ['Editor.exe'])
                    self.assertEqual((root/'Editor.exe').read_bytes(), second)
                else:
                    with self.assertRaises(YitoError):
                        restore_declared_targets(root, ['Editor.exe'])
                    self.assertFalse((root/'Editor.exe').exists())
                self.assertFalse((root/'Undeclared.exe').exists())

    def test_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, data in [('Editor.exe', b'original'), ('Editor,1.exe', b'a'), ('Editor,2.exe', b'b')]:
                (root/name).write_bytes(data)
            restore_declared_targets(root, ['Editor.exe'])
            self.assertEqual((root/'Editor.exe').read_bytes(), b'original')

    def test_normalizes_app_and_omits_other_roots(self):
        self.assertEqual(app_inventory(listing(('{app}\\Sub\\Editor.exe', 3),
                                               ('{tmp}\\helper.exe', 2))),
                         {'sub/editor.exe': ('Sub/Editor.exe', 3)})

    def test_rejects_unsafe_or_ambiguous_paths(self):
        cases = [('{app}\\..\\escape',), ('C:\\escape',),
                 ('{app}\\file', '{APP}\\FILE'),
                 ('{app}\\file', '{app}\\file\\child'),
                 ('{app}\\file\\child', '{app}\\file'),
                 ('{app}\\NUL.txt',)]
        for names in cases:
            with self.subTest(names=names), self.assertRaises(YitoError):
                app_inventory(listing(*((name, 1) for name in names)))

    def test_rejects_limits_missing_payload_and_incomplete_table(self):
        valid = listing(('{app}\\file', 3))
        for text, limits in [(valid, {'max_expanded': 2}),
                             (valid, {'max_entries': 0}),
                             (listing(('{tmp}\\file', 1)), {}),
                             (valid.rsplit('\n', 1)[0], {}),
                             ('unknown output', {})]:
            with self.subTest(text=text, limits=limits), self.assertRaises(YitoError):
                app_inventory(text, **limits)

    def test_only_private_tool_runs_and_inventory_precedes_publication(self):
        for content in (b'app', b'wrong size'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                tool = root/'core/tools/innounp/innounp.exe'
                tool.parent.mkdir(parents=True)
                tool.touch()
                archive = root/'download-0'
                archive.write_bytes(b'never execute')
                destination = root/'payload'
                calls = []

                def run(args, **kwargs):
                    calls.append(args)
                    self.assertEqual(args[0], str(tool))
                    self.assertIn('-b', args)
                    self.assertIn('-n', args)
                    self.assertIn('-a', args)
                    self.assertFalse(destination.exists())
                    if '-v' in args:
                        return SimpleNamespace(returncode=0, stderr='',
                                               stdout=listing(('{app}\\Editor.exe', 3)))
                    self.assertIn('-c{app}', args)
                    self.assertEqual(args[-1], '{app}\\*')
                    output = Path(next(arg[2:] for arg in args if arg.startswith('-d')))
                    (output/'Editor.exe').write_bytes(content)
                    return SimpleNamespace(returncode=0, stderr='', stdout='')

                with patch('mito.inno.subprocess.run', side_effect=run):
                    if content == b'app':
                        extract_inno(archive, destination, root)
                        self.assertEqual((destination/'Editor.exe').read_bytes(), content)
                    else:
                        with self.assertRaises(YitoError):
                            extract_inno(archive, destination, root)
                        self.assertFalse(destination.exists())
                self.assertEqual(len(calls), 2)

    def test_missing_tool_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('mito.inno.subprocess.run') as run, self.assertRaises(YitoError):
                extract_inno(root/'setup.exe', root/'payload', root)
            run.assert_not_called()
            self.assertFalse((root/'payload').exists())
