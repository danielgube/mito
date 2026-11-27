from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import winreg

from async_support import AsyncTestCase, until
from mito import open_with
from mito.storage import YitoError


class StubEngine:
    def __init__(self, root):
        self.root = root

    def profile(self, name):
        return {'apps': ['main/foliate']}

    def selected(self, data):
        return {'main/foliate': {'key': 'main/foliate'}}

    def application_target(self, lock, shortcut=None):
        if shortcut not in (None, 'Foliate'):
            raise YitoError('Shortcut unavailable or ambiguous')
        return 'foliate.exe', []


class OpenWithTests(unittest.TestCase):
    """«Abrir con»: ProgID propio en HKCU (rama de prueba) que lanza mito run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()/'mito'
        self.engine = StubEngine(self.root)
        self.base = rf'Software\MITO-tests-{uuid.uuid4().hex}'
        for target, value in (('mito.open_with.CLASSES', self.base + r'\Classes'),
                              ('mito.open_with.notify', lambda: None),
                              ('mito.open_with.icon_for', lambda *args, **kwargs: self.root/'core/state/icons/x.ico')):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(open_with.delete_tree, self.base)

    def value(self, path, name=''):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.base + r'\Classes' + '\\' + path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    def marked(self, path, name):
        """OpenWithProgids entries are empty REG_NONE values: QueryValueEx returns None data."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.base + r'\Classes' + '\\' + path) as key:
                return winreg.QueryValueEx(key, name)[1] == winreg.REG_NONE
        except FileNotFoundError:
            return False

    def test_extensions_are_normalized_and_validated(self):
        self.assertEqual(open_with.extensions('PDF, .epub;djvu pdf'), ['.pdf', '.epub', '.djvu'])
        for text in ('', '  ', 'a/b', '..pdf', 'x' * 20):
            with self.assertRaises(YitoError):
                open_with.extensions(text)

    def test_register_writes_progid_with_profile_launcher(self):
        record = open_with.register(self.engine, 'default', 'main/foliate', 'pdf epub', shortcut='Foliate')
        progid = record['progid']
        self.assertRegex(progid, r'^MITO\.[0-9a-f]{16}$')
        command = self.value(progid + r'\shell\open\command')
        self.assertIn(str(self.root/'core/tools/python/pythonw.exe'), command)
        self.assertIn('run --profile default --app main/foliate --shortcut Foliate', command)
        self.assertTrue(command.endswith(' -- "%1"'))
        self.assertEqual(self.value(progid + r'\Application', 'ApplicationName'), 'default - main-foliate - Foliate')
        self.assertTrue(self.marked(r'.pdf\OpenWithProgids', progid))
        self.assertTrue(self.marked(r'.epub\OpenWithProgids', progid))
        self.assertEqual(open_with.entries(self.root, 'default', 'main/foliate'), [record])
        # Registering again replaces the previous extensions.
        open_with.register(self.engine, 'default', 'main/foliate', 'djvu', shortcut='Foliate')
        self.assertFalse(self.marked(r'.pdf\OpenWithProgids', progid))
        self.assertTrue(self.marked(r'.djvu\OpenWithProgids', progid))

    def test_inactive_component_or_unknown_entry_is_rejected(self):
        with self.assertRaises(YitoError):
            open_with.register(self.engine, 'default', 'main/other', 'pdf')
        with self.assertRaises(YitoError):
            open_with.register(self.engine, 'default', 'main/foliate', 'pdf', shortcut='Missing')
        self.assertEqual(open_with.registry(self.root), {})

    def test_unregister_removes_only_selected_records(self):
        first = open_with.register(self.engine, 'default', 'main/foliate', 'pdf')
        second = open_with.register(self.engine, 'work', 'main/foliate', 'pdf')
        open_with.unregister(self.root, lambda record: record['profile'] == 'default', log=lambda message: None)
        self.assertIsNone(self.value(first['progid']))
        self.assertFalse(self.marked(r'.pdf\OpenWithProgids', first['progid']))
        self.assertTrue(self.marked(r'.pdf\OpenWithProgids', second['progid']))
        self.assertEqual(list(open_with.registry(self.root)), [second['progid']])

    def test_apply_adds_replaces_and_removes_extensions(self):
        quiet = lambda message: None
        open_with.apply(self.engine, 'default', 'main/foliate', {'Foliate': ['.docx']}, quiet)
        progid = open_with.entries(self.root, 'default', 'main/foliate')[0]['progid']
        # Adding a second extension later keeps the first one.
        open_with.apply(self.engine, 'default', 'main/foliate', {'Foliate': ['.docx', '.pdf']}, quiet)
        self.assertTrue(self.marked(r'.docx\OpenWithProgids', progid))
        self.assertTrue(self.marked(r'.pdf\OpenWithProgids', progid))
        with patch('mito.open_with.register') as register:
            open_with.apply(self.engine, 'default', 'main/foliate', {'Foliate': ['.docx', '.pdf']}, quiet)
            register.assert_not_called()
        open_with.apply(self.engine, 'default', 'main/foliate', {'Foliate': []}, quiet)
        self.assertIsNone(self.value(progid))
        self.assertEqual(open_with.registry(self.root), {})

    def test_foreign_progid_is_never_deleted(self):
        with self.assertRaises(YitoError):
            open_with.remove_progid({'progid': 'Applications\\notepad.exe', 'extensions': []})

    def test_relocation_rewrites_after_move_and_releases_after_copy(self):
        old = Path(self.tmp.name).resolve()/'old'
        record = open_with.register(StubEngine(old), 'default', 'main/foliate', 'pdf')
        open_with.save(self.root, open_with.registry(old))
        self.assertEqual(open_with.relocate(self.engine, old, copied=False, log=lambda message: None), 1)
        self.assertIn(str(self.root), self.value(record['progid'] + r'\shell\open\command'))
        # A copy leaves the entry to the original instance.
        open_with.register(StubEngine(old), 'default', 'main/foliate', 'pdf')
        self.assertEqual(open_with.relocate(self.engine, old, copied=True, log=lambda message: None), 1)
        self.assertIn(str(old), self.value(record['progid'] + r'\shell\open\command'))
        self.assertEqual(open_with.registry(self.root), {})


class OpenWithDialogTests(AsyncTestCase):
    async def test_dialog_lists_adds_from_common_and_text_and_removes(self):
        from textual.app import App
        from textual.widgets import Button, DataTable, Input, Select
        from mito.open_with_ui import OpenWithDialog
        results = []

        class Host(App):
            def on_mount(self):
                self.push_screen(OpenWithDialog('main/foliate', [('Foliate', 'foliate.exe', [])],
                                                {'Foliate': ['.docx']}), results.append)

        app = Host()
        # Default 80×24 clips the dialog; buttons are pressed, not clicked, so the
        # assertions do not depend on the terminal layout.
        async with app.run_test(size=(120, 50)) as pilot:
            self.assertTrue(await until(pilot, lambda: app.screen.query('#open-with-table')))
            screen = app.screen
            table = screen.query_one('#open-with-table', DataTable)
            self.assertTrue(await until(pilot, lambda: table.row_count == 1))
            screen.query_one('#open-with-common', Select).value = '.pdf'
            self.assertTrue(await until(pilot, lambda: table.row_count == 2))
            screen.query_one('#open-with-extensions', Input).value = 'mp3, AVI'
            screen.query_one('#open-with-add', Button).press()
            self.assertTrue(await until(pilot, lambda: table.row_count == 4))
            table.move_cursor(row=0)
            await pilot.pause()
            screen.query_one('#open-with-remove', Button).press()
            self.assertTrue(await until(pilot, lambda: table.row_count == 3))
            screen.query_one('#open-with-confirm', Button).press()
            self.assertTrue(await until(pilot, lambda: results))
        self.assertEqual(results, [{'Foliate': ['.pdf', '.mp3', '.avi']}])

    async def test_preset_adds_its_extensions_without_duplicates(self):
        from textual.app import App
        from textual.widgets import Button, DataTable, Select
        from mito.open_with import PRESETS, extensions
        from mito.open_with_ui import OpenWithDialog
        for name, values in PRESETS:
            self.assertEqual(extensions(' '.join(values)), list(values), name)
        word = dict(PRESETS)['Word']
        results = []

        class Host(App):
            def on_mount(self):
                self.push_screen(OpenWithDialog('main/office', [('Office', 'office.exe', [])],
                                                {'Office': ['.docx']}), results.append)

        app = Host()
        async with app.run_test(size=(120, 50)) as pilot:
            self.assertTrue(await until(pilot, lambda: app.screen.query('#open-with-table')))
            table = app.screen.query_one('#open-with-table', DataTable)
            app.screen.query_one('#open-with-preset', Select).value = ' '.join(word)
            self.assertTrue(await until(pilot, lambda: table.row_count == len(word)))
            await pilot.pause()
            # clear() re-emits Changed with Select.NULL: it is not an extension.
            self.assertEqual(str(app.screen.query_one('#open-with-error').content), '')
            app.screen.query_one('#open-with-confirm', Button).press()
            self.assertTrue(await until(pilot, lambda: results))
        self.assertEqual(results, [{'Office': list(word)}])


if __name__ == '__main__':
    unittest.main()
