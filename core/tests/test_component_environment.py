from pathlib import Path
import tempfile
import unittest

from mito.engine import Engine


class ComponentEnvironmentTests(unittest.TestCase):
    def test_inspection_resolves_scope_architecture_and_bin_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root)
            lock = {'key': 'main/tool', 'path': 'core/apps/main/tool', 'manifest': {
                'env_set': {'ROOT': '$dir', 'DATA': '${persist_dir}', 'LITERAL': 'true'},
                'env_add_path': 'wrong', 'bin': ['bin/tool.exe', 'other/run.exe'],
                'architecture': {'64bit': {'env_add_path': 'bin'}},
                'persist': 'data', 'shortcuts': [['bin/tool.exe', 'Tool']]}}
            variables, paths = engine.component_environment(lock, 'one')
            payload = root / 'core/profile-apps/one/main/tool'
            self.assertEqual(variables, {'ROOT': str(payload),
                'DATA': str(root / 'home/profiles/one/persist/main/tool'), 'LITERAL': 'true'})
            self.assertEqual(paths, [str(payload / 'bin'), str(payload / 'other')])
            self.assertEqual(list(root.iterdir()), [])
            lock['manifest'].pop('shortcuts')
            variables, paths = engine.component_environment(lock, None)
            self.assertEqual(variables['ROOT'], str(root / 'core/apps/main/tool'))
            self.assertEqual(variables['DATA'], str(root / 'home/persist/main/tool'))
            self.assertEqual(list(root.iterdir()), [])

    def test_empty_contributions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            lock = {'key': 'main/tool', 'path': 'core/apps/main/tool', 'manifest': {}}
            self.assertEqual(Engine(root).component_environment(lock, None), ({}, []))
