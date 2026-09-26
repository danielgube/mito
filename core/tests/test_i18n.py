import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import Button, DataTable, Select, Static
from mito.configuration import Configuration, PROPERTIES
from mito.engine import Engine
from mito.i18n import DEFAULT_LANGUAGE, DEFAULT_SETTING, LANGUAGES, LANGUAGE_CHOICES, LOCALES, Translator, bundle, fields, system_language, tr
from mito.storage import YitoError, write_json
from mito.ui import YitoApp


class BundleTests(unittest.TestCase):
    def test_bundles_cover_authored_messages_and_preserve_placeholders(self):
        def load_bundle(path):
            duplicates = []

            def reject_duplicates(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        duplicates.append(key)
                    result[key] = value
                return result

            data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=reject_duplicates)
            self.assertFalse(duplicates, f'{path.name} has duplicate keys: {duplicates}')
            return data

        source = load_bundle(LOCALES / 'español.json')
        english = load_bundle(LOCALES / 'english.json')
        self.assertEqual(len(LANGUAGES), 24)
        self.assertEqual(source.keys(), english.keys())
        for key in source:
            with self.subTest(key=key):
                self.assertEqual(source[key], key)
                self.assertTrue(english[key])
                self.assertEqual(fields(source[key]), fields(english[key]))
        for language in LANGUAGES:
            with self.subTest(language=language):
                localized = load_bundle(LOCALES / f'{language}.json')
                self.assertEqual(localized.keys(), source.keys())
                for key, value in localized.items():
                    self.assertTrue(value)
                    self.assertEqual(fields(key), fields(value))
        # New literal messages and keyboard hints must ship in both bundles.
        for path in LOCALES.parent.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args and isinstance(node.args[0], ast.Constant):
                    self.assertIn(node.args[0].value, source, f'{path.name}:{node.lineno}')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'localize_script':
                    for label in node.args[1:]:
                        if isinstance(label, ast.Constant):
                            self.assertIn(label.value, source, f'{path.name}:{node.lineno}')
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'BINDINGS' for t in node.targets):
                    for _, _, description in ast.literal_eval(node.value):
                        self.assertIn(description, source)

    def test_fallback_and_interpolation_do_not_translate_user_values(self):
        english = Translator('english')
        self.assertEqual(english('Abrir'), 'Open')
        self.assertEqual(english('Profile {p1} creado', p1='Eliminar {x}'), 'Profile Eliminar {x} created')
        self.assertEqual(english('unknown.message'), 'unknown.message')
        self.assertEqual(Translator('../../outside').language, DEFAULT_LANGUAGE)
        self.assertEqual(tr('Abrir'), 'Abrir')
        with patch('mito.i18n.bundle', return_value={}):
            self.assertEqual(Translator('english')('Profile {p1} creado', p1='one'), 'Profile one creado')
        spanish = Translator('español')
        spanish.messages = {}
        self.assertEqual(spanish('Abrir'), 'Open')

    def test_invalid_bundle_entries_fall_back_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'english.json').write_text(json.dumps({'Abrir': 42, 'Profile {p1} creado': 'Profile {missing}', 'Cerrar':'Close'}))
            with patch('mito.i18n.LOCALES', folder):
                bundle.cache_clear()
                try:
                    translator = Translator('english')
                    self.assertEqual(translator('Cerrar'), 'Close')
                    self.assertEqual(translator('Abrir'), 'Abrir')
                    self.assertEqual(translator('Profile {p1} creado', p1='test'), 'Profile test creado')
                finally:
                    bundle.cache_clear()

    def test_language_validation_and_legacy_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch('mito.i18n.system_language', return_value='español'):
            root = Path(tmp)
            engine = Engine(root)
            engine.initialize()
            self.assertEqual(Translator.from_root(root).language, 'español')
            self.assertEqual(PROPERTIES['mito.language'], (LANGUAGE_CHOICES, DEFAULT_SETTING))
            for language in LANGUAGES:
                Configuration(engine).save([('mito.language', language)])
                self.assertEqual(Translator.from_root(root).language, language)
            Configuration(engine).save([('mito.language', 'system')])
            self.assertEqual(Translator.from_root(root).language, 'español')
            before = (root/'config.properties').read_bytes()
            for invalid in ('french', 'german', 'italian', '', '../english', 'ENGLISH'):
                with self.subTest(language=invalid), self.assertRaises(YitoError):
                    Configuration(engine).save([('mito.language', invalid)])
                self.assertEqual((root/'config.properties').read_bytes(), before)
            (root/'config.properties').write_text('\ufeffmito.language=unknown\n', encoding='utf-8')
            self.assertEqual(Translator.from_root(root).language, 'english')

    def test_system_ui_language_and_regional_variants(self):
        with patch('mito.i18n.ctypes.WinDLL') as dll:
            get_language = dll.return_value.GetUserDefaultUILanguage
            for langid, expected in ((0x040a, 'español'), (0x0c0a, 'español'), (0x080a, 'español'),
                                     (0x0409, 'english'), (0x0809, 'english'),
                                     (0x040c, 'français'), (0x0407, 'deutsch'),
                                     (0x0402, 'български'), (0x041a, 'hrvatski'),
                                     (0x0405, 'čeština'), (0x0406, 'dansk'),
                                     (0x0413, 'nederlands'), (0x0425, 'eesti'),
                                     (0x040b, 'suomi'), (0x0408, 'ελληνικά'),
                                     (0x040e, 'magyar'), (0x043c, 'gaeilge'),
                                     (0x0410, 'italiano'), (0x0426, 'latviešu'),
                                     (0x0427, 'lietuvių'), (0x043a, 'malti'),
                                     (0x0415, 'polski'), (0x0416, 'português'),
                                     (0x0418, 'română'), (0x041b, 'slovenčina'),
                                     (0x0424, 'slovenščina'), (0x041d, 'svenska'),
                                     (0, 'english')):
                with self.subTest(langid=langid):
                    get_language.return_value = langid
                    self.assertEqual(system_language(), expected)
                    self.assertEqual(Translator().language, expected)
            self.assertEqual(get_language.argtypes, [])
        with patch('mito.i18n.ctypes.WinDLL', side_effect=OSError):
            self.assertEqual(system_language(), 'english')

    def test_manual_language_overrides_system_and_missing_property_detects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('mito.i18n.system_language', return_value='english') as detect:
                self.assertEqual(Translator.from_root(root).language, 'english')
                self.assertFalse((root/'config.properties').exists())
                (root/'config.properties').write_text('mito.language=español\n', encoding='utf-8')
                detect.reset_mock()
                self.assertEqual(Translator.from_root(root).language, 'español')
                detect.assert_not_called()
            with patch('mito.i18n.system_language', return_value='español') as detect:
                (root/'config.properties').write_text('mito.language=english\n', encoding='utf-8')
                self.assertEqual(Translator.from_root(root).language, 'english')
                detect.assert_not_called()

    def test_native_labels_are_escaped_as_data(self):
        from mito.native_ui import localize_script
        self.assertEqual(localize_script("$title = 'Open'", 'Open', translate=lambda _: "User's $folder"),
                         "$title = 'User''s $folder'")


class LanguageUITests(unittest.IsolatedAsyncioTestCase):
    async def test_english_interface_dialogs_catalog_and_worker_messages(self):
        from mito.configuration_ui import ConfigurationDialog
        from mito.host_apps_ui import HostDataConfirm, HostInfoDialog, RemoveHostDialog
        from mito.network_ui import ProxyDialog
        from mito.profile_management_ui import ManageProfileDialog, DeleteProfileDialog
        from mito.profile_transfer_ui import ImportReviewDialog, TransferPathDialog
        from mito.ui import ShortcutDialog, UnverifiedDownloadsDialog, ComponentInfoDialog, RemoveComponentDialog
        from mito.pocketools_ui import PocketoolsDialog
        with tempfile.TemporaryDirectory() as tmp, patch('mito.network.Network.check', return_value=True):
            root = Path(tmp)
            engine = Engine(root)
            engine.create_profile('one')
            Configuration(engine).save([('mito.language', 'english')])
            manifest = {'version':'1', 'url':'https://example.org/a.zip', 'hash':'0'*64,
                        'description':'Eliminar', 'notes':['No se traduce esta nota.'], 'post_install':'external script'}
            write_json(root/'core/buckets/main.json', {'apps':{'hello':manifest}})
            catalog_before = (root/'core/buckets/main.json').read_bytes()
            app = YitoApp(engine)
            async with app.run_test(size=(150, 45)) as pilot:
                await app.workers.wait_for_complete()
                self.assertEqual(str(app.query_one('#configuration', Button).label), 'Settings')
                self.assertEqual(str(app.query_one('#network-status', Button).label), 'Internet available')
                self.assertIn('no profile', app.translator('ningún profile'))
                self.assertEqual(app._bindings.get_bindings_for_key('ctrl+q')[0].description, 'Quit')
                app.action_search()
                await pilot.pause()
                app.query_one(DataTable).move_cursor(row=0)
                await pilot.pause()
                details = str(app.query_one('#details', Static).content)
                self.assertIn('Eliminar', details)
                self.assertIn('No se traduce esta nota.', details)
                self.assertIn('Skipped step: MITO does not run post_install.', details)
                self.assertEqual(engine.catalog.compatibility('main/hello', manifest), 'Compatible')
                self.assertEqual((root/'core/buckets/main.json').read_bytes(), catalog_before)
                from mito.host_apps import choose_file
                from mito.host_data import choose_folder
                from mito.profile_transfer_ui import choose_export_file
                from mito.network import prompt_credentials
                with patch('subprocess.run') as run, patch('mito.host_data.host_folder', return_value=root):
                    run.return_value.stdout = ''
                    run.return_value.returncode = 0
                    for picker, args, label in (
                        (choose_file, (), 'MITO - Add host application'),
                        (choose_folder, ('data',), 'MITO - Select the host folder to link'),
                        (choose_export_file, (root,), 'MITO - Import profile'),
                        (prompt_credentials, (), 'MITO - Network authentication'),
                    ):
                        picker(*args)
                        self.assertIn(label, run.call_args.args[0][-1])
                lock = {'key':'main/hello', 'version':'1', 'path':'core/apps/main/hello', 'manifest':manifest}
                dialogs = [
                    (ShortcutDialog(), '#shortcut-confirm', 'Create'),
                    (UnverifiedDownloadsDialog(['main/hello: http://example.org/a']), '#unverified-confirm', 'Download and install'),
                    (RemoveComponentDialog(lock, ['one']), '#remove-confirm', 'Remove'),
                    (ComponentInfoDialog(root, lock, 'one'), '#info-close', '×'),
                    (ManageProfileDialog('one', False), '#create', 'Create profile'),
                    (DeleteProfileDialog('one'), '#delete-profile-confirm', 'Remove profile'),
                    (TransferPathDialog(root, 'one'), '#transfer-accept', 'Export'),
                    (ImportReviewDialog('two', {}, {}), '#import-confirm', 'Install and create profile'),
                    (HostInfoDialog(root/'host.exe'), '#host-folder-open', 'Open'),
                    (RemoveHostDialog(root/'host.exe'), '#host-remove-confirm', 'Remove'),
                    (HostDataConfirm('External text'), '#host-data-accept', 'Link'),
                    (ProxyDialog(None), '#proxy-submit', 'Connect'),
                    (PocketoolsDialog(engine), '#pocketools-close', '×'),
                    (ConfigurationDialog(engine), '#configuration-save', 'Save'),
                ]
                for dialog, selector, label in dialogs:
                    app.push_screen(dialog)
                    await pilot.pause()
                    self.assertEqual(str(app.screen.query_one(selector, Button).label), label)
                    self.assertIn(dialog._bindings.get_bindings_for_key('escape')[0].description, ('Cancel','Close'))
                    await pilot.press('escape')
                    await pilot.pause()
                # Validation and messages originate in a thread worker, not the UI event loop.
                app.begin('create', 'one', 'bad name')
                await app.workers.wait_for_complete()
                self.assertTrue(any('Invalid name:' in message for message in app.log_messages), app.log_messages)
                app.begin('create', 'one', 'two')
                await app.workers.wait_for_complete()
                self.assertIn('Profile two created', app.log_messages)
            self.assertEqual(tr('Abrir'), 'Abrir')

    async def test_configuration_language_select_save_cancel_and_reopen(self):
        from mito.configuration_ui import SettingRow
        with tempfile.TemporaryDirectory() as tmp, patch('mito.network.Network.check', return_value=True), patch('mito.i18n.system_language', return_value='español'):
            root = Path(tmp)
            engine = Engine(root)
            engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(150,45)) as pilot:
                await app.workers.wait_for_complete()
                app.open_configuration()
                await pilot.pause()
                row = next(row for row in app.screen.query(SettingRow) if row.key == 'mito.language')
                self.assertEqual(row.query_one(Select).value, 'system')
                row.query_one(Select).value = 'english'
                await pilot.press('escape')
                self.assertFalse((root/'config.properties').exists())
                app.open_configuration()
                await pilot.pause()
                row = next(row for row in app.screen.query(SettingRow) if row.key == 'mito.language')
                row.query_one(Select).value = 'english'
                await pilot.click('#configuration-save')
                await pilot.pause()
                self.assertEqual(Translator.from_root(root).language, 'english')
                self.assertEqual(str(app.query_one('#configuration', Button).label), 'Configuración')
            reopened = YitoApp(engine)
            async with reopened.run_test(size=(150,45)) as pilot:
                await reopened.workers.wait_for_complete()
                self.assertEqual(str(reopened.query_one('#configuration', Button).label), 'Settings')
                reopened.open_configuration('one')
                await pilot.pause()
                self.assertFalse(any(row.key == 'mito.language' for row in reopened.screen.query(SettingRow)))
