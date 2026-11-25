import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from async_support import AsyncTestCase, click
from unittest.mock import patch
import zipfile

from textual.widgets import Button, Static
from mito import self_update as updater
from mito.engine import Engine
from mito.storage import YitoError, write_json
from mito.ui import YitoApp


def fixture(root, version='1.0.0'):
    for relative in updater.FIXED:
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('fixture')
    folder = root / 'core/app/mito'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '__init__.py').write_text('__version__ = ' + repr(version))
    (folder / '__main__.py').write_text('# fixture')
    write_json(root / 'release.json', {'version': version, 'platform': 'windows-x64', 'kind': 'bootstrap'})
    write_json(root / 'core/bootstrap.lock.json', {'python': {'url': 'https://example.org/python.zip', 'sha256': 'a'*64}, 'wheels': [], 'tools': []})


def release(version='1.0.1', digest='sha256:' + 'a'*64):
    name = f'mito-{version}-windows-x64.zip'
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'size': 123, 'digest': digest,
                        'browser_download_url': f'https://github.com/danielgube/mito/releases/download/v{version}/{name}'}]}


def offline_release(root, payload, version='1.0.1', signed=True):
    fixture(payload, version)
    folder = root / 'update'
    folder.mkdir(exist_ok=True)
    archive = folder / f'mito-{version}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for file in payload.rglob('*'):
            if file.is_file(): zipped.write(file, file.relative_to(payload))
    if signed:
        archive.with_suffix('.zip.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    return archive


class PocketoolAssetUpdateTests(unittest.TestCase):
    def test_new_icon_is_managed_but_absent_legacy_payload_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root, '1.0.0')
            fixture(payload, '1.0.1')
            icon = 'core/assets/branding/pocketools.ico'
            (payload / icon).write_bytes(b'new icon')
            updater.apply(root, payload, {'current': '1.0.0', 'version': '1.0.1'}, processes=lambda _: [])
            self.assertEqual((root / icon).read_bytes(), b'new icon')
            fixture(payload, '1.0.2')
            (payload / icon).unlink()
            updater.apply(root, payload, {'current': '1.0.1', 'version': '1.0.2'}, processes=lambda _: [])
            self.assertEqual((root / icon).read_bytes(), b'new icon')


class OfflineUpdateTests(unittest.TestCase):
    def test_local_newest_release_wins_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            offline_release(root, payload, '1.0.2')
            archive = offline_release(root, payload, '1.0.10')
            with patch('mito.network.opener', side_effect=AssertionError('network')):
                info = updater.check(root)
            self.assertEqual(info['version'], '1.0.10')
            self.assertEqual(Path(info['local_path']), archive.resolve())
            self.assertEqual(info['hash'], 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_local_prepare_apply_preserves_archive_and_data_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, payload = base / 'instance', base / 'payload'
            fixture(root)
            archive = offline_release(root, payload)
            (root / 'config.properties').write_text('user data')
            with patch('mito.network.opener', side_effect=AssertionError('network')), patch.object(updater, 'download', side_effect=AssertionError('download')), patch('mito.host_data.host_folder', return_value=base / 'host'), patch.object(updater, 'snapshot_runner'):
                info = updater.check(root)
                work = updater.prepare(root, info)
                updater.apply(root, work / 'payload', info, processes=lambda _: [])
            self.assertEqual(updater.detected_version(root), '1.0.1')
            self.assertEqual((root / 'config.properties').read_text(), 'user data')
            self.assertTrue(archive.is_file())

    def test_unsigned_requires_consent_and_changed_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            archive = offline_release(root, payload, signed=False)
            info = updater.check_offline(root)
            with self.assertRaises(YitoError): updater.prepare(root, info)
            target = Path(tmp) / 'copied.zip'
            updater.copy_offline(root, info, target)
            self.assertEqual(target.read_bytes(), archive.read_bytes())
            info['hash'] = 'sha256:' + '0'*64
            with self.assertRaises(YitoError): updater.copy_offline(root, info, target)
            self.assertFalse(target.exists())

    def test_changed_bootstrap_refuses_without_network_or_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, payload = base / 'instance', base / 'payload'
            fixture(root)
            offline_release(root, payload)
            lock = json.loads((root / 'core/bootstrap.lock.json').read_text())
            lock['python']['sha256'] = 'b'*64
            write_json(root / 'core/bootstrap.lock.json', lock)
            with patch('mito.network.opener', side_effect=AssertionError('network')), patch('mito.host_data.host_folder', return_value=base / 'host'), patch.object(updater.subprocess, 'run', side_effect=AssertionError('bootstrap')):
                with self.assertRaisesRegex(YitoError, 'bootstrap distinto'):
                    updater.prepare(root, updater.check(root))
            self.assertEqual(updater.detected_version(root), '1.0.0')

    def test_older_releases_ignored_and_wrong_metadata_or_sidecar_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            offline_release(root, payload, '0.9.0')
            self.assertIsNone(updater.check_offline(root))
            archive = offline_release(root, payload)
            archive.with_suffix('.zip.sha256').write_text('0'*64 + '  other.zip')
            with self.assertRaises(YitoError): updater.check_offline(root)
            archive.rename(archive.with_name('mito-1.0.2-windows-x64.zip'))
            with self.assertRaises(YitoError): updater.check_offline(root)


class SelfUpdateTests(unittest.TestCase):
    def test_release_selection_and_hash_contract(self):
        self.assertIsNone(updater.release_info(release(), '1.0.1'))
        self.assertIsNone(updater.release_info(release(), '2.0.0'))
        self.assertIsNone(updater.release_info(dict(release(), prerelease=True), '1.0.0'))
        self.assertEqual(updater.release_info(release('1.0.10'), '1.0.9')['version'], '1.0.10')
        for digest in (None, '', 'skip', 'md5:' + 'b'*32, 'sha1:' + 'c'*40):
            self.assertEqual(bool(updater.release_info(release(digest=digest), '1.0.0')['hash']), bool(digest and digest != 'skip'))
        for change in ({'tag_name': 'v1.0.2-rc1'}, {'assets': []}):
            with self.assertRaises(YitoError):
                updater.release_info(dict(release(), **change), '1.0.0')
        for field, value in (('digest', 'sha256:bad'), ('browser_download_url', 'file:///other.zip'), ('size', 0)):
            data = release()
            data['assets'][0][field] = value
            with self.assertRaises(YitoError):
                updater.release_info(data, '1.0.0')

    def test_check_uses_latest_api_and_configured_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            with patch('mito.network.opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(release()).encode()
                self.assertEqual(updater.check(root)['version'], '1.0.1')
                self.assertEqual(opener.call_args.args[0], root)
                self.assertEqual(opener.return_value.open.call_args.args[0].full_url, updater.LATEST)
                self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 15)

    def test_declared_checksum_sidecar_is_required_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            data = release(digest=None)
            archive = data['assets'][0]
            data['assets'].append({'name': archive['name'] + '.sha256',
                                   'browser_download_url': archive['browser_download_url'] + '.sha256'})
            for checksum, valid in ((('b'*64 + '  ' + archive['name'] + '\n').encode(), True),
                                    (('b'*64 + '  other.zip\n').encode(), False)):
                with self.subTest(valid=valid), patch('mito.network.opener') as opener:
                    opener.return_value.open.return_value.__enter__.return_value.read.side_effect = [json.dumps(data).encode(), checksum]
                    if valid:
                        self.assertEqual(updater.check_remote(root, '1.0.0', updater.LATEST)['hash'], 'sha256:' + 'b'*64)
                    else:
                        with self.assertRaises(YitoError): updater.check_remote(root, '1.0.0', updater.LATEST)

    def test_bootstrap_without_pins_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            lock = json.loads((root / 'core/bootstrap.lock.json').read_text())
            lock['wheels'] = [{'url': 'https://example.org/dependency.whl', 'sha256': None}]
            write_json(root / 'core/bootstrap.lock.json', lock)
            with self.assertRaises(YitoError): updater.validate_payload(root, '1.0.0')

    def test_apply_preserves_all_instance_data_and_removes_obsolete_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            fixture(payload, '1.0.1')
            protected = ('config.properties', 'profiles/default.json', 'profiles/work.json', 'home/private',
                         'workspaces/project/code', 'core/apps/java/data', 'core/profile-apps/work/java/data',
                         'pocketools/scripts/private', 'core/state/host-applications.json', 'core/tools/python/private')
            for relative in protected:
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('private:' + relative)
            (root / 'core/app/mito/obsolete.py').write_text('old code')
            (payload / 'config.properties').write_text('defaults')
            info = updater.release_info(release(), '1.0.0')
            backup = updater.apply(root, payload, info, processes=lambda _: [])
            self.assertEqual(updater.detected_version(root), '1.0.1')
            self.assertFalse((root / 'core/app/mito/obsolete.py').exists())
            self.assertTrue((backup / 'core/app/mito/obsolete.py').is_file())
            for relative in protected:
                self.assertEqual((root / relative).read_text(), 'private:' + relative)

    def test_publication_failure_restores_previous_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            fixture(payload, '1.0.1')
            (payload / 'mito.cmd').write_text('new launcher')
            rename = Path.rename
            def fail_once(path, target):
                if 'incoming' in path.parts and path.name == 'bootstrap.py':
                    raise PermissionError('locked')
                return rename(path, target)
            with patch.object(Path, 'rename', fail_once), self.assertRaises(PermissionError):
                updater.apply(root, payload, updater.release_info(release(), '1.0.0'), processes=lambda _: [])
            self.assertEqual(updater.detected_version(root), '1.0.0')
            self.assertEqual((root / 'mito.cmd').read_text(), 'fixture')
            self.assertEqual((root / 'core/bootstrap.py').read_text(), 'fixture')

    def test_bootstrap_swap_and_rollback_keep_previous_runtime(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
                fixture(root)
                fixture(payload, '1.0.1')
                for folder, value in ((root, 'old'), (payload, 'new')):
                    (folder / 'core/tools/python').mkdir(parents=True)
                    (folder / 'core/tools/python/python.exe').write_text(value)
                    write_json(folder / 'core/state/bootstrap-tools.json', {'value': value})
                rename = Path.rename
                def replace(path, target):
                    if fail and 'incoming' in path.parts and path.name == 'bootstrap-tools.json':
                        raise PermissionError('blocked marker')
                    return rename(path, target)
                with patch.object(Path, 'rename', replace):
                    if fail:
                        with self.assertRaises(PermissionError):
                            updater.apply(root, payload, updater.release_info(release(), '1.0.0'), tools_changed=True, processes=lambda _: [])
                    else:
                        updater.apply(root, payload, updater.release_info(release(), '1.0.0'), tools_changed=True, processes=lambda _: [])
                self.assertEqual((root / 'core/tools/python/python.exe').read_text(), 'old' if fail else 'new')
                self.assertEqual(updater.detected_version(root), '1.0.0' if fail else '1.0.1')

    def test_process_started_during_copy_aborts_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            fixture(payload, '1.0.1')
            with patch.object(updater, 'running_processes', side_effect=[[], ['99: python.exe']]) as processes:
                with self.assertRaises(YitoError):
                    updater.apply(root, payload, updater.release_info(release(), '1.0.0'), processes=processes)
            self.assertEqual(updater.detected_version(root), '1.0.0')

    def test_refuses_git_stale_version_processes_and_foreign_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, payload = Path(tmp) / 'instance', Path(tmp) / 'payload'
            fixture(root)
            fixture(payload, '1.0.1')
            info = updater.release_info(release(), '1.0.0')
            with self.assertRaises(YitoError):
                updater.apply(root, payload, info, processes=lambda _: ['123: python.exe'])
            with self.assertRaises(YitoError):
                updater.apply(root, payload, dict(info, current='0.9.0'), processes=lambda _: [])
            (root / '.git').write_text('gitdir: elsewhere')
            with self.assertRaises(YitoError):
                updater.apply(root, payload, info, processes=lambda _: [])
            (root / '.git').unlink()
            (payload / 'core/state').mkdir()
            (payload / 'core/state/injected.json').write_text('{}')
            with self.assertRaises(YitoError):
                updater.apply(root, payload, info, processes=lambda _: [])
            self.assertEqual(updater.detected_version(root), '1.0.0')

    def test_unverified_requires_confirmation_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            with patch.object(updater, 'download') as download, self.assertRaises(YitoError):
                updater.prepare(root, updater.release_info(release(digest=None), '1.0.0'))
            download.assert_not_called()

    def test_prepare_checks_hash_size_archive_and_bootstrap_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, payload = base / 'instance', base / 'source'
            fixture(root)
            fixture(payload, '1.0.1')
            archive = base / 'release.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for file in payload.rglob('*'):
                    if file.is_file(): z.write(file, file.relative_to(payload))
            info = updater.release_info(release(), '1.0.0')
            info.update(size=archive.stat().st_size, hash=hashlib.sha256(archive.read_bytes()).hexdigest())
            def download(url, target, expected, **kwargs):
                self.assertEqual(expected, info['hash'])
                shutil.copy2(archive, target)
            with patch('mito.host_data.host_folder', return_value=base / 'host'), patch.object(updater, 'download', download), patch.object(updater, 'snapshot_runner'), patch.object(updater, 'launch') as launch:
                work = updater.prepare(root, info)
                self.assertFalse(work.is_relative_to(root))
                self.assertFalse(json.loads((work / 'request.json').read_text())['tools_changed'])
                self.assertEqual(updater.detected_version(root), '1.0.0')
                launch.assert_not_called()
                with self.assertRaises(YitoError):
                    updater.prepare(root, dict(info, size=1))

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime')
    def test_copied_runtime_is_independent_and_launch_uses_host_cmd(self):
        source = Path(updater.__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix='mito space ! % & ') as tmp:
            work = Path(tmp)
            updater.snapshot_runner(source, work)
            result = subprocess.run([str(work / 'runner/python.exe'), '-B', '-I', '-c',
                                     'import mito.self_update; print(mito.self_update.__file__)'],
                                    capture_output=True, text=True, cwd=work, env=updater.host_environment(work))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(work / 'app/mito'), result.stdout)
            with patch.object(updater.subprocess, 'Popen') as popen:
                updater.launch(work)
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs['cwd'], work)
            self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NEW_CONSOLE)
            self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_BREAKAWAY_FROM_JOB)
            self.assertNotIn('WT_SESSION', kwargs['env'])
            self.assertEqual(kwargs['env']['TEMP'], str(work))

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD')
    def test_real_host_cmd_handles_special_paths_and_runs_outside_instance(self):
        source = Path(updater.__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix='mito cmd ! % & ') as tmp:
            work = Path(tmp)
            updater.snapshot_runner(source, work)
            # Only the disposable fixture auto-exits; production waits for the user.
            (work / 'app/mito/self_update.py').write_text(
                "import json, os, sys\nfrom pathlib import Path\n"
                "request=Path(sys.argv[1])\n"
                "(request.parent/'proof.json').write_text(json.dumps({'request':str(request),'cwd':os.getcwd(),'temp':os.environ['TEMP']}))\n", encoding='utf-8')
            process = updater.launch(work)
            self.assertEqual(process.wait(timeout=20), 0)
            proof = json.loads((work / 'proof.json').read_text())
            self.assertEqual(Path(proof['request']), work / 'request.json')
            self.assertEqual(Path(proof['cwd']), work)
            self.assertEqual(Path(proof['temp']), work)

    @unittest.skipUnless(os.name == 'nt', 'Windows process inventory')
    def test_real_process_inventory_detects_current_runtime(self):
        root = Path(updater.__file__).resolve().parents[3]
        self.assertTrue(any(line.startswith(str(os.getpid()) + ':') for line in updater.running_processes(root)))

    def test_waiting_for_processes_closes_them_only_when_the_user_types_c(self):
        root = Path('C:/instance')
        agent = {'pid': 17820, 'name': 'agent.exe', 'path': r'home\AppData\Local\Agent\agent.exe'}
        inventory = unittest.mock.Mock(side_effect=[[agent], [agent], []])
        stop = unittest.mock.Mock(return_value=[])
        said = []
        updater.wait_for_closed_processes(root, inventory=inventory, stop=stop,
                                          ask=unittest.mock.Mock(side_effect=['', ' C ']), say=said.append)
        stop.assert_called_once_with(root, [17820])
        self.assertIn(r'  17820: home\AppData\Local\Agent\agent.exe', said)
        inventory = unittest.mock.Mock(side_effect=[[agent], []])
        stop = unittest.mock.Mock()
        updater.wait_for_closed_processes(root, inventory=inventory, stop=stop,
                                          ask=unittest.mock.Mock(return_value='si'), say=said.append)
        stop.assert_not_called()

    def test_autostart_entries_pointing_inside_the_instance_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'mito'
            entries = [('HKEY_CURRENT_USER', 'Run', 'Agent', '"' + str(root / 'home/AppData/agent.exe').upper() + '"'),
                       ('HKEY_CURRENT_USER', 'Run', 'Sibling', '"' + str(root) + '-other\\app.exe"'),
                       ('HKEY_CURRENT_USER', 'Run', 'Binary', b'\x00')]
            self.assertEqual([entry[2] for entry in updater.autostart_inside(root, entries)], ['Agent'])

    @unittest.skipUnless(os.name == 'nt', 'Windows process control')
    def test_real_stop_processes_closes_an_instance_process_and_ignores_foreign_pids(self):
        with tempfile.TemporaryDirectory(prefix='mito stop ') as tmp:
            root = Path(tmp)
            shutil.copy2(Path(os.environ['SystemRoot']) / 'System32/cmd.exe', root / 'agent.exe')
            # `pause` blocks on the open pipe until the process is force-closed.
            process = subprocess.Popen([str(root / 'agent.exe'), '/d', '/c', 'pause'], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = __import__('time').monotonic() + 15
                while not any(item['pid'] == process.pid for item in updater.process_inventory(root)):
                    self.assertLess(__import__('time').monotonic(), deadline)
                self.assertEqual(updater.stop_processes(root, [process.pid, os.getpid()]), [])
                self.assertIsNotNone(process.wait(timeout=10))
            finally:
                if process.poll() is None:
                    process.kill()

    @unittest.skipUnless(os.name == 'nt', 'Windows updater')
    def test_real_external_cmd_updates_disposable_distribution(self):
        source = Path(updater.__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix='mito actual update ') as tmp:
            work = Path(tmp)
            root = work / 'instance'
            fixture(root)
            fixture(work / 'payload', '1.0.1')
            (root / 'config.properties').write_text('user configuration')
            updater.snapshot_runner(source, work)
            # Automate only the disposable console's Enter prompts.
            with (work / 'app/mito/__init__.py').open('a') as stream:
                stream.write('\nimport builtins\nbuiltins.input = lambda prompt: ""\n')
            write_json(work / 'request.json', {'root': str(root), 'info': updater.release_info(release(), '1.0.0'), 'tools_changed': False})
            process = updater.launch_ready(work)
            self.assertEqual(process.wait(timeout=30), 0)
            self.assertEqual(json.loads((work / 'result.json').read_text())['status'], 'complete')
            self.assertEqual(updater.detected_version(root), '1.0.1')
            self.assertEqual((root / 'config.properties').read_text(), 'user configuration')


class SelfUpdateUITests(AsyncTestCase):
    async def test_home_discovers_new_offline_zip_and_dialog_shows_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'instance'
            engine = Engine(root)
            engine.create_profile('one')
            fixture(root)
            with patch('mito.network.opener', side_effect=OSError('offline')):
                app = YitoApp(engine)
                async with app.run_test(size=(140, 50)) as pilot:
                    await app.workers.wait_for_complete()
                    self.assertFalse(app.query_one('#self-update', Button).display)
                    archive = offline_release(root, Path(tmp) / 'payload')
                    await click(pilot, '#home-button')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertIn('Offline', str(app.query_one('#self-update', Button).label))
                    await click(pilot, '#self-update')
                    await pilot.pause()
                    text = '\n'.join(str(widget.content) for widget in app.screen.query(Static))
                    self.assertIn(str(archive.resolve()), text)
                    self.assertIn('sin conexión', text)
                    await pilot.press('escape')
                    archive.unlink()
                    await click(pilot, '#home-button')
                    await app.workers.wait_for_complete()
                    self.assertFalse(app.query_one('#self-update', Button).display)

    async def test_startup_check_shows_update_and_offline_keeps_home_usable(self):
        for offline in (False, True):
            with self.subTest(offline=offline), tempfile.TemporaryDirectory() as tmp:
                engine = Engine(Path(tmp))
                engine.create_profile('one')
                fixture(engine.root)
                info = updater.release_info(release(), '1.0.0')
                with patch.object(updater, 'check', side_effect=OSError('offline') if offline else None, return_value=info):
                    app = YitoApp(engine)
                    async with app.run_test(size=(140, 50)) as pilot:
                        await app.workers.wait_for_complete()
                        await pilot.pause()
                        self.assertEqual(app.query_one('#self-update', Button).display, not offline)
                        self.assertFalse(app.busy)
                        self.assertFalse(app.query_one('#manage-profile', Button).disabled)
                        if offline: self.assertTrue(any('offline' in line for line in app.log_messages))

    async def test_launch_failure_keeps_manager_open_and_restores_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(140, 50)) as pilot:
                await app.workers.wait_for_complete()
                app.mito_update_checked(updater.release_info(release(), '1.0.0'))
                with patch.object(updater, 'prepare', return_value=Path(tmp)), patch.object(updater, 'launch_ready', side_effect=OSError('CMD unavailable')), patch.object(app, 'exit') as exit_app:
                    app.confirm_mito_update(True)
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    exit_app.assert_not_called()
                    self.assertFalse(app.busy)
                    self.assertFalse(app.query_one('#self-update', Button).disabled)
                    self.assertTrue(any('CMD unavailable' in line for line in app.log_messages))

    async def test_button_visibility_dialog_cancel_and_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(140, 50)) as pilot:
                await app.workers.wait_for_complete()
                button = app.query_one('#self-update', Button)
                self.assertFalse(button.display)
                app.mito_update_checked(updater.release_info(release(digest=None), '1.0.0'))
                self.assertTrue(button.display)
                await pilot.pause()
                await click(pilot, '#self-update')
                await pilot.pause()
                text = '\n'.join(str(s.content) for s in app.screen.query(Static))
                self.assertIn('sin verificación de integridad', text)
                self.assertIn('CMD independiente', text)
                with patch.object(app, 'prepare_mito_update') as prepare:
                    await pilot.press('escape')
                    await pilot.pause()
                    prepare.assert_not_called()
                    await click(pilot, '#self-update')
                    await click(pilot, '#self-update-confirm')
                    await pilot.pause()
                    prepare.assert_called_once()
                    self.assertTrue(app.busy)
