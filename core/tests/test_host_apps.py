from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito import host_apps
from mito.engine import Engine
from mito.storage import YitoError


class HostAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.engine = Engine(self.folder/'mito')
        self.engine.initialize()
        for profile in ('one', 'two'):
            self.engine.save_profile(profile, {'workspace':profile, 'apps':[], 'env':{'EXTERNAL_APP_TEST':profile}})

    def test_registration_deduplicates_and_removal_only_removes_reference(self):
        application = self.folder/'Host App.exe'
        application.write_bytes(b'fixture')
        identity = host_apps.add(self.engine.root, application)
        self.assertEqual(identity, host_apps.add(self.engine.root, application))
        self.assertEqual(len(host_apps.inventory(self.engine.root)), 1)
        host_apps.remove(self.engine.root, identity)
        self.assertEqual(host_apps.inventory(self.engine.root), {})
        self.assertEqual(application.read_bytes(), b'fixture')
        with self.assertRaises(YitoError):
            host_apps.launch(self.engine, 'one', identity)
        application.with_suffix('.txt').write_text('not executable')
        with self.assertRaises(YitoError):
            host_apps.add(self.engine.root, application.with_suffix('.txt'))

    def test_external_application_uses_selected_profile_environment(self):
        application = self.folder/'Application.exe'
        application.write_bytes(b'fixture')
        identity = host_apps.add(self.engine.root, application)
        for profile in ('one','two'):
            with patch('mito.host_apps.subprocess.Popen') as process:
                host_apps.launch(self.engine, profile, identity)
            actual = process.call_args.kwargs['env']
            self.assertEqual(actual['MITO_PROFILE'], profile)
            self.assertEqual(actual['EXTERNAL_APP_TEST'], profile)
            self.assertEqual(actual['APPDATA'], str(self.engine.root/'home/AppData/Roaming'))
            self.assertEqual(actual['LOCALAPPDATA'], str(self.engine.root/'home/AppData/Local'))
            self.assertIn(str(self.engine.root/'core/state/shims'/profile),actual['PATH'])

    def test_exe_launch_uses_profile_workspace_and_environment(self):
        application = self.folder/'Application.exe'
        application.write_bytes(b'fixture')
        identity = host_apps.add(self.engine.root, application)
        expected = self.engine.environment('one')
        with patch('mito.host_apps.subprocess.Popen') as process:
            host_apps.launch(self.engine, 'one', identity)
        self.assertEqual(process.call_args.args[0], [str(application)])
        self.assertEqual(process.call_args.kwargs['cwd'], self.engine.root/'workspaces/one')
        self.assertEqual(process.call_args.kwargs['env'], expected)

    def test_batch_paths_with_metacharacters_and_powershell_execute(self):
        expected = self.engine.environment('one')
        for extension in ('.cmd','.bat'):
            application = self.folder/f"tool %TEMP% ! & ^{extension}"
            application.write_text('@echo off\n> "%~dp0result.txt" echo %APPDATA%\n',encoding='ascii')
            identity = host_apps.add(self.engine.root, application)
            with patch('mito.host_apps.subprocess.CREATE_NEW_CONSOLE', subprocess.CREATE_NO_WINDOW):
                process = host_apps.launch(self.engine, 'one', identity)
                self.assertEqual(process.wait(timeout=15),0)
            result = self.folder/'result.txt'
            self.assertEqual(result.read_text().strip(),expected['APPDATA'])
            result.unlink()
        application = self.folder/"tool's & !%.ps1"
        application.write_text("@{AppData=$env:APPDATA;LocalAppData=$env:LOCALAPPDATA;Profile=$env:MITO_PROFILE} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'result.json') -Encoding UTF8",encoding='utf-8-sig')
        identity = host_apps.add(self.engine.root, application)
        with patch('mito.host_apps.subprocess.CREATE_NEW_CONSOLE', subprocess.CREATE_NO_WINDOW):
            process = host_apps.launch(self.engine, 'one', identity)
            self.assertEqual(process.wait(timeout=15),0)
        data = json.loads((self.folder/'result.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(data,{'AppData':expected['APPDATA'],'LocalAppData':expected['LOCALAPPDATA'],'Profile':'one'})

    def test_shortcut_pins_profile_and_badge(self):
        application = self.folder/'Host.exe'
        application.write_bytes(b'fixture')
        identity = host_apps.add(self.engine.root, application)
        desktop = self.folder/'Desktop'
        desktop.mkdir()
        collision = desktop/'MITO - one - Host - Host.lnk'
        collision.write_bytes(b'foreign shortcut')
        with patch('mito.shortcuts.desktop_path',return_value=desktop), patch('mito.shortcuts.application_icon',return_value=self.folder/'icon.ico') as icon, patch('mito.shortcuts.write_link') as link:
            result = host_apps.shortcut(self.engine, 'one', identity, 'TEST')
        self.assertNotEqual(result,collision)
        self.assertEqual(collision.read_bytes(),b'foreign shortcut')
        argv = link.call_args.args[1]
        self.assertEqual(argv[-4:],['host-run',identity,'--profile','one'])
        self.assertEqual(icon.call_args.args[2:],('TEST', application))

    def test_coupled_launch_and_shortcut_use_terminal_mode(self):
        application = self.folder/'Host App.exe'
        application.write_bytes(b'fixture')
        identity = host_apps.add(self.engine.root, application)
        with patch('mito.terminal.launch_host', return_value=0) as launch:
            self.assertEqual(host_apps.launch(self.engine, 'one', identity, ['--name','two words'], coupled=True), 0)
        launch.assert_called_once_with(self.engine, 'one', identity, 'Host App', ['--name','two words'])
        desktop = self.folder/'Desktop'
        with patch('mito.shortcuts.desktop_path', return_value=desktop), patch('mito.shortcuts.application_icon', return_value=self.folder/'icon.ico'), patch('mito.shortcuts.write_link') as write:
            host_apps.shortcut(self.engine, 'one', identity, 'TERM', coupled=True,
                arguments=['--name','two words'])
        argv = write.call_args.args[1]
        self.assertIn('--coupled', argv)
        self.assertEqual(argv[-3:], ['--','--name','two words'])
        registry = next((self.engine.root/'core/state/host-shortcuts').glob('*.json'))
        self.assertEqual(json.loads(registry.read_text())['arguments'], ['--name','two words'])


class HostDiscoveryTests(unittest.TestCase):
    """Descubrimiento ligero: accesos .lnk del menú Inicio y del escritorio del host."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.root = self.folder/'mito'
        (self.root/'core/apps').mkdir(parents=True)
        self.menu = self.folder/'Start Menu'
        (self.menu/'Tools').mkdir(parents=True)
        self.targets = {}

    def shortcut(self, name, target):
        link = self.menu/f'{name}.lnk'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_bytes(b'lnk')
        self.targets[str(link)] = str(target)
        return link

    def program(self, relative):
        path = self.folder/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'exe')
        return path

    def test_discover_filters_and_deduplicates(self):
        editor = self.program('Editor/editor.exe')
        self.shortcut('Editor', editor)
        self.shortcut('Tools/Editor (copia)', editor)
        self.shortcut('Uninstall Editor', self.program('Editor/unins000.exe'))
        self.shortcut('Manual', self.program('Editor/manual.pdf'))
        self.shortcut('Missing', self.folder/'Gone/gone.exe')
        self.shortcut('Inside MITO', self.program('mito/core/apps/x/x.exe'))
        added = self.program('Added/added.exe')
        self.shortcut('Added', added)
        host_apps.add(self.root, added)
        script = self.program('Scripts/run.cmd')
        self.shortcut('Tools/Run', script)
        found = host_apps.discover(self.root, [self.menu, self.folder/'absent'], lambda links: self.targets)
        self.assertEqual(found, [('Editor', str(editor)), ('Run', str(script))])

    def test_discover_without_shortcuts_does_not_resolve(self):
        def fail(links):
            raise AssertionError('no links to resolve')
        self.assertEqual(host_apps.discover(self.root, [self.folder/'empty'], fail), [])


class HostDiscoverDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_add_and_browse(self):
        from textual.app import App
        from textual.widgets import Button, Input
        from mito.host_apps_ui import HostDiscoverDialog
        results = []
        found = [('Editor', r'C:\Apps\editor.exe'), ('Viewer', r'C:\Apps\viewer.exe')]

        class Host(App):
            def on_mount(self):
                self.push_screen(HostDiscoverDialog(found), results.append)

        app = Host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.screen.query_one('#host-discover-filter', Input).value = 'view'
            await pilot.pause()
            self.assertEqual(app.screen.shown, [found[1]])
            app.screen.query_one('#host-discover-add', Button).press()
            await pilot.pause()
            app.push_screen(HostDiscoverDialog(found), results.append)
            await pilot.pause()
            app.screen.query_one('#host-discover-browse', Button).press()
            await pilot.pause()
        self.assertEqual(results, [r'C:\Apps\viewer.exe', 'browse'])
