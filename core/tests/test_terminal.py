import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from mito.engine import Engine
from mito.storage import read_json
from mito.terminal import command, prepare, guid, launch_component


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        folder = self.root / 'core/tools/windows-terminal'
        folder.mkdir(parents=True)
        (folder / 'WindowsTerminal.exe').touch()
        for name in ('java11', 'java21'):
            self.engine.save_profile(name, {'workspace': name, 'apps': [], 'env': {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_profiles_menu_colors_fullscreen_and_update(self):
        prepare(self.engine)
        path = self.root / 'core/tools/windows-terminal/settings/settings.json'
        settings = read_json(path)
        visible = [p for p in settings['profiles']['list'] if not p.get('hidden')]
        self.assertEqual([p['name'] for p in visible], ['CMD (java11)', 'PowerShell (java11)', 'PowerShell (java21)', 'CMD (java21)', 'MITO'])
        self.assertEqual(settings['defaultProfile'], guid('java11'))
        self.assertEqual(settings['launchMode'], 'maximized')
        self.assertTrue(settings['showTabsInFullscreen'])
        self.assertEqual(settings['profiles']['defaults']['opacity'], 94)
        self.assertEqual(len({p['colorScheme'] for p in visible}), 3)
        self.assertIn('--type powershell --profile java11', visible[1]['commandline'])
        self.engine.save_profile('new', {'workspace': 'new', 'apps': [], 'env': {}})
        prepare(self.engine)
        self.assertEqual(len(read_json(path)['newTabMenu']), 7)
        previous = path.read_bytes()
        prepare(self.engine)
        self.assertEqual(previous, path.read_bytes())

    def test_default_follows_selection_and_survives_background_prepare(self):
        path = self.root / 'core/tools/windows-terminal/settings/settings.json'
        prepare(self.engine, selected_profile='java21')
        prepare(self.engine)
        settings = read_json(path)
        self.assertEqual(settings['defaultProfile'], guid('java21'))
        self.assertEqual(settings['newTabMenu'][0]['profile'], guid('java21'))
        self.assertEqual(settings['profiles']['list'][0]['guid'], guid('java21'))
        self.engine.delete_profile('java21')
        self.assertEqual(read_json(path)['defaultProfile'], guid('java11'))

    def test_default_shell_choices_and_invalid_value_fallback(self):
        path = self.root / 'core/tools/windows-terminal/settings/settings.json'
        for mode, expected in [('powershell', guid('java21', 'powershell')),
                               ('mito', guid()), ('cmd', guid('java21')),
                               ('invalid', guid('java21'))]:
            with self.subTest(mode=mode):
                (self.root / 'config.properties').write_text('terminal.defaultShell=' + mode)
                prepare(self.engine, selected_profile='java21')
                settings = read_json(path)
                self.assertEqual(settings['defaultProfile'], expected)
                self.assertEqual(settings['newTabMenu'][0]['profile'], expected)
                self.assertEqual(settings['profiles']['list'][0]['guid'], expected)
                if mode != 'mito':
                    self.assertEqual(settings['newTabMenu'][-1]['profile'], guid())

    def test_no_profiles_defaults_to_manager(self):
        for name in self.engine.profiles():
            (self.root / 'profiles' / (name + '.json')).unlink()
        prepare(self.engine)
        settings = read_json(self.root / 'core/tools/windows-terminal/settings/settings.json')
        self.assertEqual(settings['defaultProfile'], guid())

    def test_manager_launch_keeps_explicit_manager_and_selected_default(self):
        from mito.terminal import launch
        with patch('mito.terminal.subprocess.Popen') as process:
            launch(self.engine, selected_profile='java21')
        argv = process.call_args.args[0]
        self.assertEqual(argv[argv.index('--profile') + 1], guid())
        settings = read_json(self.root / 'core/tools/windows-terminal/settings/settings.json')
        self.assertEqual(settings['defaultProfile'], guid('java21'))

    def test_prepare_brands_portable_terminal_window_icons(self):
        source = self.root / 'core/assets/branding/mito.ico'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'mito icon')
        (source.parent / 'pocketools.ico').write_bytes(b'pocketools icon')
        images = self.root / 'core/tools/windows-terminal/Images'
        images.mkdir()
        (images / 'terminal_contrast-black.ico').write_bytes(b'upstream')

        with patch('mito.terminal_branding.ensure_branded_executable'):
            prepare(self.engine)

        for name in ('terminal_contrast-black.ico', 'terminal_contrast-white.ico'):
            self.assertEqual((images / name).read_bytes(), source.read_bytes())
        settings = read_json(self.root / 'core/tools/windows-terminal/settings/settings.json')
        for profile in settings['profiles']['list']:
            if not profile.get('hidden'):
                self.assertEqual(profile['icon'], str(source))

    def test_bootstrap_tools_brand_before_publishing_first_run_marker(self):
        from mito.bootstrap_tools import ensure_tools
        repository_core = Path(__file__).resolve().parents[1]
        source = self.root / 'core/assets/branding/mito.ico'
        source.parent.mkdir(parents=True)
        shutil.copy2(repository_core / 'assets/branding/mito.ico', source)
        folder = self.root / 'core/tools/windows-terminal'
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_core / 'tools/windows-terminal/WindowsTerminal.exe', folder / 'WindowsTerminal.exe')
        (folder / '.mito-tool.json').write_text('{"version":"1"}')
        lock = self.root / 'core/bootstrap.lock.json'
        lock.write_text('{"tools":[{"id":"windows-terminal","version":"1","required":["WindowsTerminal.exe"]}]}')

        ensure_tools(self.root)

        self.assertTrue((self.root / 'core/state/bootstrap-tools.json').is_file())
        self.assertTrue((folder / 'YitoTerminal.exe').is_file())
        self.assertNotEqual((folder / 'YitoTerminal.exe').read_bytes(), (folder / 'WindowsTerminal.exe').read_bytes())
        self.assertEqual(command(self.engine)[0], str(folder / 'YitoTerminal.exe'))
        for name in ('terminal_contrast-black.ico', 'terminal_contrast-white.ico'):
            self.assertEqual((folder / 'Images' / name).read_bytes(), source.read_bytes())

    def test_commands_reuse_instance_window_and_set_shell(self):
        manager = command(self.engine)
        ps = command(self.engine, 'java21', 'powershell')
        self.assertIn('--maximized', manager)
        self.assertEqual(manager[manager.index('-w')+1], ps[ps.index('-w')+1])
        self.assertIn(guid('java21', 'powershell'), ps)
        self.assertEqual(ps[-5:], ['shell', '--type', 'powershell', '--profile', 'java21'])

    def test_coupled_application_gets_named_tab_and_owned_runner(self):
        with patch('mito.terminal.subprocess.Popen') as process:
            launch_component(self.engine, 'java21', 'extras/editor', 'My Editor', ['run', 'two words'])
        argv = process.call_args.args[0]
        self.assertEqual(argv[argv.index('--title') + 1], 'My Editor')
        self.assertEqual(argv[-9:], ['run', '--owned', '--profile', 'java21', '--app',
            'extras/editor', '--', 'run', 'two words'])

    def test_powershell_receives_activated_environment(self):
        with patch('mito.engine.subprocess.call', return_value=0) as call:
            self.engine.shell('java21', shell_type='powershell')
        argv = call.call_args.args[0]
        self.assertTrue(argv[0].endswith('powershell.exe'))
        self.assertIn('-NoProfile', argv)
        self.assertEqual(call.call_args.kwargs['env']['MITO_PROFILE'], 'java21')
        self.assertEqual(call.call_args.kwargs['env']['USERPROFILE'], str(self.root / 'home'))
