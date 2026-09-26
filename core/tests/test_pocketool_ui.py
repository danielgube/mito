from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from textual.app import App
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea
from mito.engine import Engine
from mito.i18n import Translator
from mito.pocketools_ui import PocketoolsDialog
from mito.pocketool_editor_ui import PackageCommandsDialog, CommandEditor, DiscardDialog, GitDialog
from mito.pocketool_authoring import register, save_script
from mito.ui import ShortcutDialog


class PocketoolApp(App):
    def __init__(self, engine, screen=None):
        super().__init__()
        self.engine, self.first_screen = engine, screen
        self.translator, self.busy = Translator('español'), False

    def on_mount(self):
        self.push_screen(self.first_screen or PocketoolsDialog(self.engine, 'one'))

    def write_log(self, *args):
        pass


class PocketoolUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.engine = Engine(self.base / 'mito')
        self.engine.initialize()
        self.engine.save_profile('one', {'workspace': 'one', 'apps': [], 'env': {}})
        self.source = self.engine.root / 'pocketools/tools'

    async def settle(self, app, pilot):
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def test_create_edit_and_shortcut_flow(self):
        app = PocketoolApp(self.engine)
        with patch('mito.error_tracking.capture'):
            async with app.run_test(size=(140, 48)) as pilot:
                await pilot.click('#pocketools-create')
                app.screen.query_one('#package-name', Input).value = 'tools'
                await pilot.pause()
                self.assertEqual(len(app.screen.query('#package-folder, #package-mode, #package-browse')), 0)
                self.assertIn(str(self.source), str(app.screen.query_one('#package-destination', Static).content))
                app.screen.query_one('#package-info', Input).value = 'My commands'
                await pilot.click('#package-save')
                await self.settle(app, pilot)
                self.assertIsInstance(app.screen, PackageCommandsDialog)
                self.assertTrue(app.screen.query_one('#commands-edit', Button).disabled)
                self.assertTrue(app.screen.query_one('#commands-shortcut', Button).disabled)
                self.assertEqual(len(app.screen.query('#commands-apply')), 0)
                await pilot.click('#commands-new')
                self.assertIsInstance(app.screen, CommandEditor)
                app.screen.query_one('#command-name', Input).value = 'hello'
                app.screen.query_one('#command-content', TextArea).load_text('@echo off\necho hello\n')
                await pilot.click('#command-save')
                await self.settle(app, pilot)
                self.assertTrue((self.source / 'hello.cmd').is_file())
                self.assertTrue((self.engine.root / 'pocketools/tools/hello.cmd').is_file())
                await pilot.click('#command-close-bottom')
                await pilot.pause()
                app.screen.query_one('#commands-entry', Select).value = 'hello.cmd'
                await pilot.click('#commands-shortcut')
                self.assertIsInstance(app.screen, ShortcutDialog)
                self.assertEqual(len(app.screen.query('#shortcut-coupled')), 0)
                app.screen.query_one('#shortcut-badge', Input).value = 'TEST'
                app.screen.query_one('#shortcut-arguments', Input).value = '"hello world" -v'
                await pilot.click('#shortcut-color-verde')
                with patch('mito.pocketool_execution.shortcut', return_value=self.base / 'one - tools-hello.lnk') as link:
                    await pilot.click('#shortcut-confirm')
                    await self.settle(app, pilot)
                self.assertEqual(link.call_args.args[1:6], ('one', 'tools', 'hello.cmd', 'TEST', 'verde'))
                self.assertEqual(link.call_args.args[6], ['hello world', '-v'])

    async def test_selection_controls_edit_and_shortcut_even_after_refresh(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        save_script(self.engine, 'tools', 'hello', 'cmd', 'echo hello')
        app = PocketoolApp(self.engine, PackageCommandsDialog(self.engine, 'tools', 'one'))
        async with app.run_test(size=(140, 48)) as pilot:
            screen = app.screen
            for action in ('commands-edit', 'commands-shortcut'):
                button = screen.query_one('#' + action, Button)
                self.assertTrue(button.disabled)
                # Guard a queued event as well as normal clicks on disabled buttons.
                screen.on_button_pressed(Button.Pressed(button))
                self.assertIs(app.screen, screen)
            screen.query_one('#commands-entry', Select).value = 'hello.cmd'
            await pilot.pause()
            self.assertFalse(screen.query_one('#commands-edit', Button).disabled)
            self.assertFalse(screen.query_one('#commands-shortcut', Button).disabled)
            screen.query_one('#commands-profile', Select).clear()
            await pilot.pause()
            self.assertTrue(screen.query_one('#commands-shortcut', Button).disabled)
            self.assertFalse(screen.query_one('#commands-edit', Button).disabled)
            screen.query_one('#commands-entry', Select).clear()
            await pilot.pause()
            self.assertTrue(screen.query_one('#commands-edit', Button).disabled)
            screen.perform(lambda: None, lambda _: None)
            await self.settle(app, pilot)
            self.assertTrue(screen.query_one('#commands-edit', Button).disabled)
            self.assertTrue(screen.query_one('#commands-shortcut', Button).disabled)
            await screen.refresh_commands()
            await pilot.pause()
            self.assertTrue(screen.query_one('#commands-edit', Button).disabled)
            self.assertTrue(screen.query_one('#commands-shortcut', Button).disabled)

    async def test_live_package_has_no_snapshot_update_button(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        app = PocketoolApp(self.engine)
        async with app.run_test(size=(140, 48)):
            self.assertEqual(len(app.screen.query('.pocketool-update')), 0)
            self.assertEqual(len(app.screen.query('.pocketool-manage')), 1)

    async def test_discard_and_external_conflict_keep_unsaved_editor_content(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        app = PocketoolApp(self.engine, CommandEditor(self.engine, 'tools'))
        with patch('mito.error_tracking.capture'):
            async with app.run_test(size=(140, 48)) as pilot:
                app.screen.query_one('#command-name', Input).value = 'hello'
                app.screen.query_one('#command-content', TextArea).load_text('echo first')
                await pilot.click('#command-save')
                await self.settle(app, pilot)
                (self.source / 'hello.cmd').write_text('echo external')
                app.screen.query_one('#command-content', TextArea).load_text('echo editor')
                await pilot.click('#command-save')
                await self.settle(app, pilot)
                self.assertIn('fuera del editor', str(app.screen.query_one('.pocketool-error', Static).content))
                self.assertEqual(app.screen.query_one('#command-content', TextArea).text, 'echo editor')
                await pilot.click('#command-close-bottom')
                self.assertIsInstance(app.screen, DiscardDialog)
                await pilot.click('#discard-cancel')
                self.assertIsInstance(app.screen, CommandEditor)

    async def test_discard_confirm_dismisses_editor_without_fatal(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        app = PocketoolApp(self.engine, CommandEditor(self.engine, 'tools'))
        async with app.run_test(size=(140, 48)) as pilot:
            app.screen.query_one('#command-content', TextArea).load_text('echo changed')
            await pilot.click('#command-close-bottom')
            self.assertIsInstance(app.screen, DiscardDialog)
            await pilot.click('#discard-confirm')
            await self.settle(app, pilot)
            self.assertNotIsInstance(app.screen, (CommandEditor, DiscardDialog))

    async def test_template_documents_selected_profile_without_overwriting_edits(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        self.engine.save_profile('two', {'workspace': 'shared-work', 'apps': [], 'env': {'API_SECRET': 'private-value'}})
        app = PocketoolApp(self.engine, PackageCommandsDialog(self.engine, 'tools', 'two'))
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.click('#commands-new')
            editor = app.screen.query_one('#command-content', TextArea)
            self.assertTrue(editor.text.startswith('@echo off\nchcp 65001 >nul\n'))
            self.assertIn('::MITO_PROFILE=two', editor.text)
            self.assertIn('::MITO_WORKSPACE=' + str(self.engine.root / 'workspaces/shared-work'), editor.text)
            self.assertIn('::HOME=' + str(self.engine.root / 'home'), editor.text)
            self.assertNotIn('private-value', editor.text)
            self.assertNotIn('API_SECRET', editor.text)
            self.assertFalse((self.engine.root / 'workspaces/shared-work').exists())
            app.screen.query_one('#command-kind', Select).value = 'ps1'
            await pilot.pause()
            self.assertIn('# MITO_PROFILE=two', editor.text)
            self.assertNotIn('@echo off', editor.text)
            editor.load_text('Write-Host custom')
            app.screen.query_one('#command-kind', Select).value = 'cmd'
            await pilot.pause()
            self.assertEqual(editor.text, 'Write-Host custom')

    async def test_git_includes_only_checked_paths_and_exposes_retry(self):
        register(self.engine, 'tools', self.source, 'Tools', 'new')
        reviewed = {'branch': 'main', 'head': 'abc', 'url': 'https://example.invalid/tools.git',
                    'changes': [{'path': 'hello.cmd', 'status': '??', 'stamp': '1'},
                                {'path': 'unrelated.txt', 'status': ' M', 'stamp': '2'}]}
        app = PocketoolApp(self.engine, GitDialog(self.engine, 'tools', {'hello.cmd'}))
        with patch('mito.pocketool_git.review', return_value=reviewed), \
             patch('mito.pocketool_git.commit', return_value={'committed': True, 'pushed': False, 'push_error': 'offline'}) as commit:
            async with app.run_test(size=(140, 48)) as pilot:
                await self.settle(app, pilot)
                self.assertTrue(app.screen.query_one('#git-file-0', Checkbox).value)
                self.assertFalse(app.screen.query_one('#git-file-1', Checkbox).value)
                await pilot.click('#git-push')
                await self.settle(app, pilot)
                self.assertEqual(commit.call_args.args[3], ['hello.cmd'])
                self.assertTrue(commit.call_args.args[6])
                self.assertIn('offline', str(app.screen.query_one('.pocketool-error', Static).content))
                self.assertTrue(app.screen.query_one('#git-push', Button).disabled)
                await pilot.click('#git-refresh')
                await self.settle(app, pilot)
                await pilot.click('#git-upload')
                await self.settle(app, pilot)
                self.assertEqual(commit.call_args.args[3], [])
