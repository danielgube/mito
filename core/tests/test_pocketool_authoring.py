"""Pocketool regressions; run explicitly by the user, never during authoring."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito.engine import Engine
from mito import pocketool_authoring as author
from mito import pocketool_execution as execution
from mito import pocketool_git as vcs
from mito.pocketools import sources, update
from mito.storage import YitoError, read_json, write_json


class AuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.engine = Engine(self.base / 'mito')
        self.engine.initialize()
        self.folder = self.base / 'source'

    def create(self):
        author.register(self.engine, 'tools', self.folder, 'My tools', 'new')

    def test_create_live_folder_preserves_git_and_support_files_without_copying(self):
        with patch('mito.pocketools.local_snapshot', side_effect=AssertionError('live folder must not be copied')):
            author.create_package(self.engine, 'ceniza', 'My commands')
            folder = self.engine.root / 'pocketools/ceniza'
            (folder / '.git').mkdir()
            (folder / '.git/keep').write_text('repository data')
            (folder / 'support').mkdir()
            (folder / 'support/helper.py').write_text('print("test")')
            saved = author.save_script(self.engine, 'ceniza', 'hello', 'cmd', 'echo hello')
            self.assertIsNone(saved['publish_error'])
            update(self.engine, 'ceniza', log=lambda _: None)
        self.assertEqual(sources(self.engine.root)['ceniza'], 'pocketools/ceniza')
        self.assertEqual(author.source_folder(self.engine, 'ceniza'), folder)
        self.assertEqual((folder / '.git/keep').read_text(), 'repository data')
        self.assertTrue((folder / 'support/helper.py').is_file())
        with self.assertRaises(YitoError):
            author.create_package(self.engine, 'ceniza', 'Duplicate')

    def test_live_source_remains_portable_after_moving_mito(self):
        author.create_package(self.engine, 'ceniza', 'Portable')
        moved = self.base / 'moved-mito'
        shutil.copytree(self.engine.root, moved)
        self.assertEqual(author.source_folder(Engine(moved), 'ceniza'), moved / 'pocketools/ceniza')
        for value in ('pocketools/other', 'pocketools/../core', 'relative-folder'):
            (moved / 'config.properties').write_text('pocketool.ceniza=' + value)
            with self.subTest(value=value), self.assertRaises(YitoError):
                sources(moved)

    def test_create_reuses_only_empty_unregistered_folders(self):
        folder = self.engine.root / 'pocketools/ceniza'
        folder.mkdir(parents=True)
        author.create_package(self.engine, 'ceniza', 'Empty folder')
        other = self.engine.root / 'pocketools/other'
        other.mkdir()
        (other / 'keep.txt').write_text('existing data')
        with self.assertRaises(YitoError):
            author.create_package(self.engine, 'other', 'Not empty')
        self.assertEqual((other / 'keep.txt').read_text(), 'existing data')

    def test_live_powershell_edit_rename_and_change_type_clean_only_generated_wrappers(self):
        author.create_package(self.engine, 'tools', 'Live scripts')
        folder = self.engine.root / 'pocketools/tools'
        saved = author.save_script(self.engine, 'tools', 'hello', 'ps1', 'Write-Host first')
        self.assertEqual(author.commands(folder), ['hello.ps1'])
        saved = author.save_script(self.engine, 'tools', 'hello', 'ps1', 'Write-Host second', 'hello.ps1', saved['revision'])
        saved = author.save_script(self.engine, 'tools', 'renamed', 'ps1', 'Write-Host renamed', 'hello.ps1', saved['revision'])
        self.assertFalse((folder / 'hello.cmd').exists())
        self.assertTrue(execution.generated_launcher(folder / 'renamed.cmd'))
        author.save_script(self.engine, 'tools', 'renamed', 'cmd', 'echo cmd', 'renamed.ps1', saved['revision'])
        self.assertFalse((folder / 'renamed.ps1').exists())
        self.assertEqual((folder / 'renamed.cmd').read_text(), 'echo cmd')
        saved = author.save_script(self.engine, 'tools', 'custom', 'ps1', 'Write-Host custom')
        with (folder / 'custom.cmd').open('a') as stream:
            stream.write('rem User modification\n')
        original_wrapper = (folder / 'custom.cmd').read_bytes()
        author.save_script(self.engine, 'tools', 'moved', 'ps1', 'Write-Host moved', 'custom.ps1', saved['revision'])
        self.assertEqual((folder / 'custom.cmd').read_bytes(), original_wrapper)

    def test_missing_selection_is_a_user_error_not_a_type_error(self):
        from textual.widgets import Select
        self.create()
        for value in (Select.NULL, type(Select.NULL)(), None, ''):
            with self.subTest(value=value), self.assertRaises(YitoError):
                execution.target(self.engine, 'tools', value)

    def test_edit_remote_copy_keeps_local_files_and_attaches_history(self):
        url = 'https://example.invalid/tools.git'
        (self.engine.root / 'config.properties').write_text('pocketool.tools=' + url)
        folder = self.engine.root / 'pocketools/tools'
        folder.mkdir(parents=True)
        write_json(folder / 'pocketool.json', {'info': 'My installed tools'})
        (folder / 'hello.cmd').write_text('echo local changes')
        (folder / 'support.txt').write_text('keep')
        def clone(engine, source, destination):
            self.assertEqual(source, url)
            (destination / '.git').mkdir(parents=True)
            (destination / '.git/HEAD').write_text('remote history')
            (destination / 'hello.cmd').write_text('echo remote version')
        with patch.object(vcs, 'clone', clone), patch.object(vcs, 'repository') as repository:
            repository.return_value.text.side_effect = lambda argv: 'origin' if argv == ['remote'] else url
            author.edit_local_copy(self.engine, 'tools')
        self.assertEqual((folder / 'hello.cmd').read_text(), 'echo local changes')
        self.assertEqual((folder / 'support.txt').read_text(), 'keep')
        self.assertEqual((folder / '.git/HEAD').read_text(), 'remote history')
        self.assertEqual(sources(self.engine.root)['tools'], 'pocketools/tools')
        # An old or externally restored remote configuration cannot overwrite this repo.
        (self.engine.root / 'config.properties').write_text('pocketool.tools=' + url)
        with self.assertRaisesRegex(YitoError, 'repositorio local'):
            update(self.engine, 'tools', log=lambda _: None)
        self.assertEqual((folder / 'hello.cmd').read_text(), 'echo local changes')

    def test_create_edit_and_publish_preserve_support_files_and_never_execute(self):
        self.create()
        (self.folder / 'support').mkdir()
        (self.folder / 'support/task.py').write_text('raise RuntimeError("do not execute")')
        with patch('subprocess.run', side_effect=AssertionError('no script execution')):
            first = author.save_script(self.engine, 'tools', 'hello', 'cmd', '@echo off\necho first\n')
        content, revision = author.read_script(self.engine, 'tools', 'hello.cmd')
        self.assertEqual(content, '@echo off\necho first\n')
        self.assertEqual(revision, first['revision'])
        author.save_script(self.engine, 'tools', 'hello', 'cmd', '@echo off\necho second\n', 'hello.cmd', revision)
        installed = self.engine.root / 'pocketools/tools'
        self.assertIn('second', (installed / 'hello.cmd').read_text())
        self.assertEqual((installed / 'support/task.py').read_bytes(), (self.folder / 'support/task.py').read_bytes())
        self.assertEqual(sources(self.engine.root)['tools'], str(self.folder))

    def test_external_edit_conflict_and_no_silent_overwrite(self):
        self.create()
        saved = author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo original')
        (self.folder / 'hello.cmd').write_text('echo external')
        with self.assertRaisesRegex(YitoError, 'fuera del editor'):
            author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo lost', 'hello.cmd', saved['revision'])
        self.assertEqual((self.folder / 'hello.cmd').read_text(), 'echo external')
        with self.assertRaises(YitoError):
            author.save_script(self.engine, 'tools', 'HELLO', 'cmd', 'echo collision')
        with self.assertRaises(YitoError):
            author.save_script(self.engine, 'tools', 'hello', 'ps1', 'Write-Host collision')

    def test_rename_type_and_powershell_launcher_are_derived_only(self):
        self.create()
        saved = author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo test')
        author.save_script(self.engine, 'tools', 'renamed', 'ps1', 'Write-Host "áéí"\n', 'hello.cmd', saved['revision'])
        self.assertFalse((self.folder / 'hello.cmd').exists())
        self.assertFalse((self.folder / 'renamed.cmd').exists())
        self.assertTrue((self.folder / 'renamed.ps1').read_bytes().startswith(b'\xef\xbb\xbf'))
        installed = self.engine.root / 'pocketools/tools'
        self.assertFalse((installed / 'hello.cmd').exists())
        self.assertIn('%~dp0renamed.ps1', (installed / 'renamed.cmd').read_text())
        self.assertIn('%*', (installed / 'renamed.cmd').read_text())
        (self.folder / 'renamed.cmd').write_text('echo custom wrapper')
        update(self.engine, 'tools', log=lambda _: None)
        self.assertEqual((installed / 'renamed.cmd').read_text(), 'echo custom wrapper')

    def test_failed_publication_keeps_saved_source_and_previous_install(self):
        self.create()
        author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo old')
        _, revision = author.read_script(self.engine, 'tools', 'hello.cmd')
        with patch.object(author, 'update', side_effect=YitoError('locked')):
            result = author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo new', 'hello.cmd', revision)
        self.assertEqual(result['publish_error'], 'locked')
        self.assertEqual((self.folder / 'hello.cmd').read_text(), 'echo new')
        self.assertEqual((self.engine.root / 'pocketools/tools/hello.cmd').read_text(), 'echo old')

    def test_reconfigured_source_does_not_receive_a_stale_editor_save(self):
        self.create()
        alternative = self.base / 'alternative'
        alternative.mkdir()
        write_json(alternative / 'pocketool.json', {'info': 'Alternative'})
        (self.engine.root / 'config.properties').write_text(f'pocketool.tools={alternative}\n')
        with self.assertRaisesRegex(YitoError, 'configuración cambió'):
            author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo wrong source', expected_folder=self.folder)
        self.assertFalse((alternative / 'hello.cmd').exists())
        with self.assertRaisesRegex(YitoError, 'configuración cambió'):
            update(self.engine, 'tools', expected_source=str(self.folder), log=lambda _: None)

    def test_names_reserved_paths_and_existing_metadata(self):
        for name in ('CON', 'nul', 'COM1', '../bad', 'two words', 'a.cmd', 'a&b', '-option', ''):
            with self.subTest(name=name), self.assertRaises(YitoError):
                author.command_filename(name, 'cmd')
        for folder in (self.engine.root, self.engine.root / 'pocketools/unsafe',
                       self.engine.root / 'core/source', self.base / '.git/source'):
            with self.assertRaises(YitoError):
                author.register(self.engine, 'invalid', folder, 'Bad', 'new')
        self.folder.mkdir()
        write_json(self.folder / 'pocketool.json', {'info': 'Preserved', 'extra': ['keep']})
        author.register(self.engine, 'tools', self.folder, 'Replacement', 'existing')
        self.assertEqual(read_json(self.folder / 'pocketool.json'), {'info': 'Preserved', 'extra': ['keep']})
        with self.assertRaises(YitoError):
            author.register(self.engine, 'other', self.folder, 'Duplicate', 'existing')

    def test_linked_sources_and_scripts_are_rejected(self):
        self.create()
        external = self.base / 'external'
        external.mkdir()
        link = self.base / 'linked'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(self.folder)], check=True, capture_output=True)
        try:
            with self.assertRaisesRegex(YitoError, 'enlaces'):
                author.register(self.engine, 'linked', link, 'Bad', 'existing')
        finally:
            os.rmdir(link)

    def test_shortcut_resolves_updated_copy_and_carries_profile_and_data(self):
        self.create()
        author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo test')
        self.engine.save_profile('one', {'workspace': 'one', 'apps': [], 'env': {'TEST_PROFILE': 'one'}})
        (self.engine.root / 'core/tools/windows-terminal').mkdir(parents=True)
        (self.engine.root / 'core/tools/windows-terminal/WindowsTerminal.exe').touch()
        desktop = self.base / 'desktop'
        args = ['with spaces', '&echo injected', 'semi;colon', '%PATH%', '']
        with patch('mito.terminal.prepare'), patch('mito.shortcuts.desktop_path', return_value=desktop), \
             patch('mito.shortcuts.application_icon', return_value=self.base / 'icon.ico') as icon, \
             patch('mito.shortcuts.write_link') as write:
            path = execution.shortcut(self.engine, 'one', 'tools', 'hello.cmd', 'TEST', 'verde', args)
        self.assertEqual(path.name, 'one - tools-hello.lnk')
        argv = write.call_args.args[1]
        self.assertEqual(argv[0], str(self.engine.root / 'core/tools/python/pythonw.exe'))
        self.assertNotIn('--owned', argv)
        self.assertIn('pocketool-run', argv)
        self.assertNotIn('&echo injected', argv)
        self.assertEqual(execution.decoded_arguments(argv[argv.index('--arguments64') + 1]), args)
        self.assertEqual(icon.call_args.args[3], self.engine.root / 'core/assets/branding/pocketools.ico')
        self.assertEqual(icon.call_args.kwargs['color'], 'verde')
        env, workspace = self.engine.execution_context('one')
        self.assertEqual(env['TEST_PROFILE'], 'one')
        self.assertEqual(workspace, self.engine.root / 'workspaces/one')
        self.assertEqual(execution.target(self.engine, 'tools', 'hello.cmd'), self.engine.root / 'pocketools/tools/hello.cmd')
        (self.engine.root / 'config.properties').write_text('')
        with self.assertRaises(YitoError):
            execution.target(self.engine, 'tools', 'hello.cmd')

    def test_shortcut_launcher_opens_owned_terminal_with_mito_icon(self):
        self.create()
        author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo hello')
        self.engine.save_profile('one', {'workspace': 'one', 'apps': [], 'env': {}})
        terminal = self.engine.root / 'core/tools/windows-terminal'
        terminal.mkdir(parents=True)
        (terminal / 'WindowsTerminal.exe').touch()
        (terminal / 'YitoTerminal.exe').touch()
        with patch('mito.terminal.prepare'), patch.object(execution.subprocess, 'Popen') as process:
            execution.launch(self.engine, 'one', 'tools', 'hello.cmd', ['a;b', 'two words'])
        argv = process.call_args.args[0]
        self.assertEqual(argv[0], str(terminal / 'YitoTerminal.exe'))
        self.assertIn('--owned', argv)
        self.assertEqual(execution.decoded_arguments(argv[argv.index('--arguments64') + 1]), ['a;b', 'two words'])

    def test_registered_terminal_shortcuts_migrate_once_and_keep_metadata(self):
        self.create()
        author.save_script(self.engine, 'tools', 'hello', 'cmd', 'echo hello')
        self.engine.save_profile('one', {'workspace': 'one', 'apps': [], 'env': {}})
        terminal = self.engine.root / 'core/tools/windows-terminal'
        terminal.mkdir(parents=True)
        (terminal / 'WindowsTerminal.exe').touch()
        link = self.base / 'custom name.lnk'
        link.touch()
        registry = self.engine.root / 'core/state/pocketool-shortcuts/example.json'
        data = {'path': str(link), 'profile': 'one', 'package': 'tools', 'command': 'hello.cmd',
                'badge': 'TEST', 'color': 'verde', 'arguments': ['a;b', 'two words']}
        write_json(registry, data)
        with patch('mito.shortcuts.retarget_link', return_value=True) as retarget:
            execution.migrate_shortcuts(self.engine)
            execution.migrate_shortcuts(self.engine)
        retarget.assert_called_once()
        path, old_targets, old_args, new_argv = retarget.call_args.args
        self.assertEqual(path, link)
        self.assertEqual(old_targets, [terminal / 'WindowsTerminal.exe', terminal / 'YitoTerminal.exe'])
        self.assertIn('--owned', old_args)
        self.assertNotIn('--owned', new_argv)
        self.assertEqual(new_argv[0], str(self.engine.root / 'core/tools/python/pythonw.exe'))
        self.assertEqual(read_json(registry), dict(data, launcher='pythonw'))
        # Leave edited links and their records alone when their target/args differ.
        write_json(registry, data)
        with patch('mito.shortcuts.retarget_link', return_value=False):
            execution.migrate_shortcuts(self.engine)
        self.assertEqual(read_json(registry), data)

    def test_argument_payload_validation(self):
        for bad in ('!!!', base64.b64encode(b'{}').decode(), base64.b64encode(b'[1]').decode()):
            with self.assertRaises(YitoError):
                execution.decoded_arguments(bad)

    def test_execution_uses_profile_context_and_job_without_mutating_host_environment(self):
        self.create()
        author.save_script(self.engine, 'tools', 'hello', 'ps1', 'Write-Output test')
        self.engine.save_profile('one', {'workspace': 'one', 'apps': [], 'env': {'POCKETOOL_TEST': 'one'}})
        before = dict(os.environ)
        with patch('mito.processes.ProcessJob') as job:
            job.return_value.spawn.return_value.wait.return_value = 7
            code = execution.execute(self.engine, 'one', 'tools', 'hello.ps1', ['two words', '&literal'])
        call = job.return_value.spawn.call_args
        self.assertEqual(code, 7)
        self.assertEqual(call.kwargs['cwd'], self.engine.root / 'workspaces/one')
        self.assertEqual(call.kwargs['env']['POCKETOOL_TEST'], 'one')
        self.assertEqual(call.args[0][-2:], ['two words', '&literal'])
        self.assertIn('-File', call.args[0])
        self.assertEqual(dict(os.environ), before)
        job.return_value.close.assert_called_once()


@unittest.skipUnless(shutil.which('git.exe'), 'Git required for temporary-repository regressions')
class GitAuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.engine = Engine(self.base / 'mito')
        self.engine.initialize()
        self.folder = self.base / 'repo'
        self.folder.mkdir()
        vcs.initialize(self.engine, self.folder)
        self.git = vcs.repository(self.engine, self.folder)
        self.git.run(['config', 'user.name', 'Pocketool test'])
        self.git.run(['config', 'user.email', 'pocketool@example.invalid'])

    def snapshot(self):
        return vcs.review(self.engine, self.folder)

    def commit(self, selected, push=False):
        return vcs.commit(self.engine, self.folder, self.snapshot(), selected, 'Test change',
                          'https://example.invalid/tools.git' if push else '', push)

    def test_only_selected_files_committed_other_staged_content_preserved(self):
        (self.folder / 'selected.cmd').write_text('echo selected')
        (self.folder / 'unrelated.txt').write_text('staged unrelated')
        self.git.run(['add', '--', 'unrelated.txt'])
        self.commit(['selected.cmd'])
        self.assertEqual(self.git.text(['ls-tree', '--name-only', 'HEAD']), 'selected.cmd')
        self.assertIn('unrelated.txt', self.git.text(['diff', '--cached', '--name-only']))
        (self.folder / 'selected.cmd').write_text('echo edited')
        self.commit(['selected.cmd'])
        self.assertEqual(self.git.text(['show', 'HEAD:selected.cmd']), 'echo edited')
        self.assertIn('unrelated.txt', self.git.text(['diff', '--cached', '--name-only']))

    def test_external_changes_invalidate_review(self):
        (self.folder / 'hello.cmd').write_text('echo first')
        reviewed = self.snapshot()
        (self.folder / 'hello.cmd').write_text('echo changed')
        with self.assertRaisesRegex(YitoError, 'cambió'):
            vcs.commit(self.engine, self.folder, reviewed, ['hello.cmd'], 'Stale')
        self.assertFalse(self.git.run(['rev-parse', '--verify', '--quiet', 'HEAD'], optional=True))

    def test_push_failure_preserves_commit_and_retry_does_not_commit_again(self):
        (self.folder / 'hello.cmd').write_text('echo first')
        original = vcs.Git.run
        def fail_push(git, arguments, optional=False):
            if arguments[0] == 'push':
                raise YitoError('offline')
            return original(git, arguments, optional)
        with patch.object(vcs.Git, 'run', fail_push):
            result = self.commit(['hello.cmd'], push=True)
            self.assertEqual(result['push_error'], 'offline')
            head = self.git.text(['rev-parse', 'HEAD'])
            vcs.commit(self.engine, self.folder, self.snapshot(), [], 'Retry', 'https://example.invalid/tools.git', True)
            self.assertEqual(self.git.text(['rev-parse', 'HEAD']), head)

    def test_hooks_and_filters_are_not_executed(self):
        self.git.run(['config', 'filter.bad.clean', 'command-that-must-not-run'])
        self.git.run(['config', 'filter.bad.required', 'true'])
        (self.folder / '.gitattributes').write_text('*.cmd filter=bad\n')
        (self.folder / '.git/hooks').mkdir(exist_ok=True)
        (self.folder / '.git/hooks/pre-commit').write_text('#!/bin/sh\nexit 91\n')
        (self.folder / 'hello.cmd').write_text('echo hello')
        self.commit(['hello.cmd', '.gitattributes'])
        self.assertEqual(self.git.text(['show', 'HEAD:hello.cmd']), 'echo hello')

    def test_deletion_and_support_files_are_explicit(self):
        (self.folder / 'old.cmd').write_text('echo old')
        self.commit(['old.cmd'])
        (self.folder / 'old.cmd').unlink()
        (self.folder / 'support').mkdir()
        (self.folder / 'support/data.txt').write_text('support')
        self.commit(['old.cmd', 'support/data.txt'])
        self.assertEqual(self.git.text(['ls-tree', '-r', '--name-only', 'HEAD']), 'support/data.txt')


class CloneTests(unittest.TestCase):
    def test_clone_materializes_blobs_without_checkout_and_retains_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root / 'mito')
            engine.initialize()
            calls = []
            def run(git, arguments, optional=False):
                calls.append(arguments)
                if arguments[0] == 'clone':
                    (Path(arguments[-1]) / '.git').mkdir(parents=True)
                elif arguments[0] == 'rev-parse':
                    return b'a' * 40
                elif arguments[0] == 'ls-tree':
                    return b'100644 blob ' + b'a' * 40 + b' 11\thello.cmd\0'
                elif arguments[0] == 'cat-file':
                    return b'echo hello\n'
                return b''
            with patch.object(vcs.Git, 'run', run), patch('mito.pocketool_git.git_executable', return_value='git.exe'):
                vcs.clone(engine, 'https://example.invalid/repo.git', root / 'work')
            self.assertTrue((root / 'work/.git').is_dir())
            self.assertEqual((root / 'work/hello.cmd').read_bytes(), b'echo hello\n')
            self.assertTrue(any('--no-checkout' in args for args in calls))
            self.assertFalse(any(args[0] in ('checkout', 'submodule') for args in calls))

    def test_clone_rejects_symlinks_without_publishing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root / 'mito')
            engine.initialize()
            def run(git, arguments, optional=False):
                if arguments[0] == 'clone':
                    (Path(arguments[-1]) / '.git').mkdir(parents=True)
                elif arguments[0] == 'rev-parse':
                    return b'a' * 40
                elif arguments[0] == 'ls-tree':
                    return b'120000 blob ' + b'a' * 40 + b' 3\tlink\0'
                return b''
            with patch.object(vcs.Git, 'run', run), patch('mito.pocketool_git.git_executable', return_value='git.exe'):
                with self.assertRaises(YitoError):
                    vcs.clone(engine, 'https://example.invalid/repo.git', root / 'work')
            self.assertFalse((root / 'work').exists())
