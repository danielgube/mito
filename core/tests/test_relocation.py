"""Instancia movida o copiada: persistencia materializada y accesos del escritorio."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mito import relocation
from mito.engine import Engine
from mito.storage import read_json, write_json


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / 'new'
        self.old = base / 'old'
        self.root.mkdir()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.logs = []

    def remember_old(self):
        write_json(self.root / relocation.MARKER, {'root': str(self.old)})

    def test_first_start_and_same_path_need_no_repair(self):
        self.assertIsNone(relocation.check(self.engine, self.logs.append))
        self.assertEqual(read_json(self.root / relocation.MARKER)['root'], str(self.root))
        self.assertIsNone(relocation.check(self.engine, self.logs.append))
        self.assertEqual(self.logs, [])

    def test_move_sets_materialized_persist_aside_without_deleting_data(self):
        payload = self.root / 'core/apps/main/tool'
        (payload / 'data').mkdir(parents=True)
        (payload / 'data/settings.txt').write_text('copied through a junction')
        (payload / 'config.ini').write_text('hard link copied as a file')
        write_json(payload / '.mito-persist.json', {'data': 'data', 'config.ini': 'config.ini'})
        self.remember_old()
        summary = relocation.check(self.engine, self.logs.append)
        self.assertFalse(summary['copied'])
        self.assertFalse((payload / 'data').exists())
        # Los archivos persistentes los rehace persistence.prepare; no se tocan aquí.
        self.assertTrue((payload / 'config.ini').exists())
        [backup] = summary['persist']
        self.assertEqual((Path(backup) / 'settings.txt').read_text(), 'copied through a junction')
        self.assertTrue(Path(backup).is_relative_to(self.root / 'core/staging'))
        self.assertEqual(read_json(self.root / relocation.MARKER)['root'], str(self.root))
        self.assertIsNone(relocation.check(self.engine, self.logs.append))

    def write_shortcut(self, kind, name):
        link = self.root / f'desktop/{name}.lnk'
        link.parent.mkdir(exist_ok=True)
        link.write_bytes(b'lnk')
        registry = self.root / 'core/state' / kind / f'{name}.json'
        write_json(registry, {'path': str(link), 'profile': 'default', 'app': 'main/tool'})
        return link, registry

    def test_move_rewrites_only_shortcuts_of_the_previous_location(self):
        own, own_registry = self.write_shortcut('application-shortcuts', 'own')
        other, _ = self.write_shortcut('application-shortcuts', 'other')
        manager, _ = self.write_shortcut('mito-shortcuts', 'default')
        targets = {str(own): str(self.old / 'core/tools/python/pythonw.exe'),
                   str(other): r'C:\Elsewhere\core\tools\python\pythonw.exe',
                   str(manager): str(self.old / 'core/tools/windows-terminal/YitoTerminal.exe')}
        self.remember_old()
        with patch.object(relocation, 'link_targets', return_value=targets), \
             patch.object(relocation, 'relink_application') as application, \
             patch.object(relocation, 'relink_manager') as manager_link:
            summary = relocation.check(self.engine, self.logs.append)
        self.assertEqual(summary['shortcuts'], {'updated': 2, 'released': 0})
        application.assert_called_once()
        self.assertEqual(application.call_args.args[1], own)
        manager_link.assert_called_once()
        self.assertTrue(own_registry.exists())

    def test_copy_keeps_original_shortcuts_and_stops_claiming_them(self):
        (self.old / 'core/app/mito').mkdir(parents=True)
        (self.old / 'mito.cmd').write_text('@echo off')
        own, own_registry = self.write_shortcut('application-shortcuts', 'own')
        self.remember_old()
        with patch.object(relocation, 'link_targets', return_value={str(own): str(self.old / 'core/tools/python/pythonw.exe')}), \
             patch.object(relocation, 'relink_application') as application:
            summary = relocation.check(self.engine, self.logs.append)
        self.assertTrue(summary['copied'])
        self.assertEqual(summary['shortcuts'], {'updated': 0, 'released': 1})
        application.assert_not_called()
        self.assertFalse(own_registry.exists())
        self.assertTrue(own.exists(), 'el acceso del original no se borra')

    def test_notice_is_shown_once(self):
        relocation.save_notice(self.root, ['uno'])
        relocation.save_notice(self.root, ['dos'])
        self.assertEqual(relocation.pop_notice(self.root), ['uno', 'dos'])
        self.assertEqual(relocation.pop_notice(self.root), [])


if __name__ == '__main__':
    unittest.main()
