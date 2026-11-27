from pathlib import Path
from contextlib import nullcontext
import unittest
import tempfile
from unittest.mock import ANY, patch

from mito.__main__ import main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_release_forwards_kind_and_output(self):
        for arguments, kind in [([], 'hotfix'), (['hotfix'], 'hotfix'),
                                (['minor'], 'minor'), (['mayor'], 'mayor')]:
            with self.subTest(kind=arguments), patch('mito.__main__.Engine') as engine_type, \
                    patch('mito.publish.publish') as publish, patch('builtins.print'):
                engine = self.engine(engine_type)
                self.assertEqual(main(['release', *arguments, '--output', 'out']), 0)
                publish.assert_called_once_with(engine, 'out', kind=kind, confirm=ANY)
                # The CLI asks before discarding a stale pending release; EOF declines.
                with patch('builtins.input', side_effect=EOFError):
                    self.assertFalse(publish.call_args.kwargs['confirm'](['pendiente']))

    def test_local_release_only_builds_current_version(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.publish.publish') as publish, \
                patch('mito.release.build_release') as build, patch('builtins.print'):
            engine = self.engine(engine_type)
            self.assertEqual(main(['release', '--local', '--output', 'out']), 0)
            build.assert_called_once_with(engine, 'out')
            publish.assert_not_called()

    def test_invalid_release_arguments_fail_before_engine_initialization(self):
        for arguments in [['invalid'], ['mayor', '1.0.1'],
                          ['--local', 'mayor'], ['minor', '--local'], ['hotfix', '--local']]:
            with self.subTest(arguments=arguments), patch('mito.__main__.Engine') as engine, \
                    patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:
                    main(['release', *arguments])
                self.assertEqual(error.exception.code, 2)
                engine.assert_not_called()

    def engine(self, engine_type):
        engine = engine_type.return_value
        engine.root = self.root
        engine.environment.return_value = {}
        return engine

    def test_owned_component_forwards_arguments_and_pauses_on_failure(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.network.intercept'), patch('builtins.input') as prompt, \
                patch('builtins.print'):
            engine = self.engine(engine_type)
            engine.launch.return_value = 7
            result = main(['run', '--owned', '--profile', 'one', '--app', 'extras/tool',
                '--', '--name', 'two words'])
        self.assertEqual(result, 7)
        engine.launch.assert_called_once_with('one', 'extras/tool', None,
            ['--name', 'two words'], coupled=False, owned=True, shortcut=None)
        prompt.assert_called_once_with('Pulsa Intro para cerrar esta pestaña.')

    def test_shims_do_not_retry_pending_diagnostic_events(self):
        # Every git call goes through `mito run`; the exit flush would wait for retries.
        with patch('mito.__main__.Engine') as engine_type, patch('mito.__main__.resume') as resume, \
                patch('mito.network.intercept'), patch('builtins.print'):
            self.engine(engine_type).launch.return_value = 0
            self.assertEqual(main(['run', '--profile', 'one', '--app', 'extras/tool']), 0)
        resume.assert_not_called()

    def test_owned_host_forwards_arguments_and_pauses_on_success(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.network.intercept'), patch('mito.host_apps.launch', return_value=0) as launch, \
                patch('builtins.input') as prompt:
            engine = self.engine(engine_type)
            result = main(['host-run', 'host-id', '--profile', 'one', '--owned',
                '--', '--port', '8080'])
        self.assertEqual(result, 0)
        launch.assert_called_once_with(engine, 'one', 'host-id', ['--port', '8080'],
            coupled=False, owned=True)
        prompt.assert_called_once_with('Pulsa Intro para cerrar esta pestaña.')

    def test_owned_component_without_arguments_pauses_even_with_zero_exit(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.network.intercept'), patch('builtins.input') as prompt, \
                patch('builtins.print') as output:
            engine = self.engine(engine_type)
            engine.launch.return_value = 0
            result = main(['run', '--owned', '--profile', 'one', '--app', 'extras/tool'])
        self.assertEqual(result, 0)
        engine.launch.assert_called_once_with('one', 'extras/tool', None, [],
            coupled=False, owned=True, shortcut=None)
        output.assert_called_once_with('La aplicación terminó con el código 0.')
        prompt.assert_called_once_with('Pulsa Intro para cerrar esta pestaña.')

    def test_independent_component_does_not_pause(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.network.intercept'), patch('builtins.input') as prompt:
            self.engine(engine_type).launch.return_value = 0
            self.assertEqual(main(['run', '--profile', 'one', '--app', 'extras/tool']), 0)
        prompt.assert_not_called()

    def test_owned_start_failure_pauses(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.network.intercept'), patch('builtins.input') as prompt, \
                patch('builtins.print'):
            self.engine(engine_type).launch.side_effect = OSError('Cannot start')
            self.assertEqual(main(['run', '--owned', '--profile', 'one', '--app', 'extras/tool']), 1)
        prompt.assert_called_once_with('Pulsa Intro para cerrar esta pestaña.')

    def test_component_run_forwards_selected_shortcut(self):
        for mode in ([], ['--coupled'], ['--owned']):
            with self.subTest(mode=mode), patch('mito.__main__.Engine') as engine_type, \
                    patch('mito.network.intercept'), patch('builtins.input'), \
                    patch('builtins.print'):
                engine = self.engine(engine_type)
                engine.launch.return_value = 0
                result = main(['run', *mode, '--profile', 'one', '--app', 'extras/tool',
                    '--shortcut', 'Tool Help', '--', 'two words'])
                self.assertEqual(result, 0)
                engine.launch.assert_called_once_with('one', 'extras/tool', None,
                    ['two words'], coupled='--coupled' in mode, owned='--owned' in mode,
                    shortcut='Tool Help')

    def test_application_shortcut_forwards_selected_label(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.__main__.mutation', return_value=nullcontext()), \
                patch('mito.shortcuts.application_link', return_value=Path('tool.lnk')) as link, \
                patch('builtins.print'):
            engine = self.engine(engine_type)
            result = main(['app-shortcut', 'extras/tool', '--profile', 'one',
                '--shortcut', 'Tool Help'])
        self.assertEqual(result, 0)
        link.assert_called_once_with(engine, 'one', 'extras/tool', None, coupled=False,
            arguments=[], shortcut='Tool Help', color=None)

    def test_application_shortcut_forwards_mode_and_arguments(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.__main__.mutation', return_value=nullcontext()), \
                patch('mito.shortcuts.application_link', return_value=Path('tool.lnk')) as link, \
                patch('builtins.print'):
            engine = self.engine(engine_type)
            result = main(['app-shortcut', 'installation', '--profile', 'one',
                '--badge', 'DEV', '--coupled', '--', '--name', 'two words'])
        self.assertEqual(result, 0)
        link.assert_called_once_with(engine, 'one', 'installation', 'DEV', coupled=True,
            arguments=['--name', 'two words'], shortcut=None, color=None)


if __name__ == '__main__':
    unittest.main()
