from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError
import json

from mito.configuration import Configuration, PROPERTIES
from mito.engine import Engine
from mito.storage import YitoError
from mito import self_update, update_checks
from test_self_update import fixture, release, offline_release


class ComponentDailyUITests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_button_refreshes_components_and_mito_again(self):
        from textual.widgets import Button
        from mito.ui import YitoApp
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            engine = Engine(root)
            engine.initialize()
            # A minimal inventory suffices for the refresh service; render an
            # empty home so this test focuses on dispatch and daily caching.
            app = YitoApp(engine)
            with patch.object(app, 'refresh_home', new_callable=AsyncMock), \
                 patch.object(engine, 'installed', return_value={'main/tool': {'key': 'main/tool'}}), \
                 patch.object(engine.catalog, 'refresh_installed', return_value=1) as components, \
                 patch.object(self_update, 'check_remote', return_value=None) as remote, \
                 patch('mito.network.Network.check', return_value=True):
                async with app.run_test(size=(140, 60)) as pilot:
                    await app.workers.wait_for_complete()
                    self.assertEqual(components.call_count, 1)
                    self.assertEqual(remote.call_count, 1)
                    await pilot.click('#check-updates')
                    await app.workers.wait_for_complete()
                    self.assertEqual(components.call_count, 2)
                    self.assertEqual(remote.call_count, 2)
                    self.assertFalse(app.busy)
                    self.assertFalse(app.query_one('#check-updates', Button).disabled)

    async def test_catalog_new_version_requires_confirmation_and_cancel_does_not_install(self):
        from mito.storage import write_json
        from mito.ui import YitoApp, InstallVersionDialog
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.initialize()
            old = {'version': '1.0', 'url': 'https://example.org/tool.zip', 'hash': '0'*64}
            newer = dict(old, version='2.0')
            source = engine.root/'source'
            write_json(source/'tool.json', newer)
            write_json(engine.root/'core/buckets/main.json', {'source': str(source), 'apps': {'tool': old}})
            app = YitoApp(engine)
            published = []
            def install(target, **kwargs):
                record = {'manifest': engine.catalog.resolve(target)[1]}
                if kwargs['confirm_plan']({target: record}):
                    published.append(record)
            with patch.object(engine, 'install', side_effect=install), patch('mito.network.Network.check', return_value=True):
                async with app.run_test(size=(140, 60)) as pilot:
                    await app.workers.wait_for_complete()
                    app.action_search()
                    app.begin('install', None, 'main/tool')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, InstallVersionDialog)
                    self.assertEqual((app.screen.previous, app.screen.version), ('1.0', '2.0'))
                    self.assertFalse(published)
                    await pilot.press('escape')
                    await app.workers.wait_for_complete()
                    self.assertFalse(published)
                    # A newer source version must be checked again on the next click.
                    write_json(source/'tool.json', dict(newer, version='3.0'))
                    app.begin('install', None, 'main/tool')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, InstallVersionDialog)
                    await pilot.click('#install-version-confirm')
                    await app.workers.wait_for_complete()
                    self.assertEqual(published[0]['manifest']['version'], '3.0')
                    # Unchanged versions proceed without another version dialog.
                    app.begin('install', None, 'main/tool')
                    await app.workers.wait_for_complete()
                    self.assertEqual(len(published), 2)
                    with patch.object(engine.catalog, 'refresh_keys', return_value=0):
                        app.begin('install', None, 'main/tool')
                        await app.workers.wait_for_complete()
                    self.assertEqual(len(published), 2)

    async def test_reopening_manager_skips_component_refresh_and_progress_logs(self):
        from mito.catalog import fingerprint
        from mito.storage import write_json
        from mito.ui import YitoApp
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root)
            engine.initialize()
            manifest = {'version': '1', 'url': 'https://example.org/tool.zip', 'hash': '0'*64}
            record = {'key': 'main/tool', 'version': '1', 'manifest': manifest,
                      'manifest_sha256': fingerprint(manifest), 'path': 'core/apps/main/tool'}
            write_json(root / record['path'] / '.mito.json', {'key': 'main/tool', 'record': record, 'manifest_sha256': fingerprint(manifest)})
            with patch.object(engine.catalog, 'refresh_installed', return_value=1) as refresh, patch('mito.network.Network.check', return_value=True):
                for attempt in range(2):
                    app = YitoApp(engine)
                    async with app.run_test(size=(140, 45)):
                        await app.workers.wait_for_complete()
                        self.assertEqual(refresh.call_count, 1)
                        if attempt:
                            self.assertFalse(any('Comprobando actualizaciones de componentes' in line for line in app.log_messages))


class DailyUpdateTests(unittest.TestCase):
    def test_manual_check_bypasses_and_replaces_daily_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            operation = Mock(side_effect=[1, 2])
            self.assertEqual(update_checks.daily(root, 'test', 'key', operation), 1)
            self.assertEqual(update_checks.daily(root, 'test', 'key', operation, force=True), 2)
            self.assertEqual(update_checks.daily(root, 'test', 'key', operation), 2)
            self.assertEqual(operation.call_count, 2)

    def test_manual_mito_check_retries_failed_daily_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            with patch.object(self_update, 'check_remote', side_effect=[OSError('offline'), None]) as remote:
                with self.assertRaises(OSError):
                    self_update.check(root)
                self.assertIsNone(self_update.check(root))
                self.assertIsNone(self_update.check(root, force=True))
                self.assertEqual(remote.call_count, 2)

    def test_success_cached_across_calls_and_next_day_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            operation = Mock(return_value={'version': '2.0.0'})
            with patch.object(update_checks, 'date') as clock:
                clock.today.return_value = date(2026, 9, 13)
                self.assertEqual(update_checks.daily(root, 'test', 'source', operation), operation.return_value)
                self.assertEqual(update_checks.daily(root, 'test', 'source', operation), operation.return_value)
                operation.assert_called_once()
                clock.today.return_value = date(2026, 9, 14)
                update_checks.daily(root, 'test', 'source', operation)
                self.assertEqual(operation.call_count, 2)
                update_checks.daily(root, 'test', 'another source', operation)
                self.assertEqual(operation.call_count, 3)

    def test_failure_and_in_progress_attempt_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            operation = Mock(side_effect=OSError('offline'))
            with self.assertRaises(OSError): update_checks.daily(root, 'test', 'key', operation)
            self.assertIsNone(update_checks.daily(root, 'test', 'key', operation))
            operation.assert_called_once()
            def nested():
                self.assertIsNone(update_checks.daily(root, 'nested', 'key', Mock(side_effect=AssertionError('duplicate'))))
                return 42
            self.assertEqual(update_checks.daily(root, 'nested', 'key', nested), 42)

    def test_404_only_once_and_offline_package_still_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'instance'
            fixture(root)
            with patch.object(self_update, 'check_remote', side_effect=HTTPError(self_update.LATEST, 404, 'Not found', {}, None)) as remote:
                with self.assertRaises(HTTPError): self_update.check(root)
                self.assertIsNone(self_update.check(root))
                offline_release(root, Path(tmp) / 'payload')
                self.assertIn('local_path', self_update.check(root))
                remote.assert_called_once()

    def test_configuration_source_and_version_changes_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            engine = Engine(root)
            with patch.object(self_update, 'check_remote', return_value=None) as remote:
                self_update.check(root)
                source = 'https://gitea.example.org/api/v1/repos/org/mito/releases/latest'
                Configuration(engine).save([('mito.update_url', source)])
                self_update.check(root)
                self_update.check(root)
                self.assertEqual(remote.call_count, 2)
                self.assertEqual(remote.call_args.args[2], source)
                (root / 'core/app/mito/__init__.py').write_text('__version__="1.0.1"')
                self_update.check(root)
                self.assertEqual(remote.call_count, 3)
            self.assertEqual(PROPERTIES['mito.update_url'][1], self_update.LATEST)
            for invalid in ('', 'file:///release.json', 'https://user:password@example.org/release'):
                with self.assertRaises(YitoError): Configuration(engine).save([('mito.update_url', invalid)])

    def test_gitea_metadata_attachment_urls_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            source = 'http://intranet.example/api/v1/repos/org/mito/releases/latest'
            (root / 'config.properties').write_text('mito.update_url=' + source)
            data = release(digest=None)
            artifact = data['assets'][0]
            artifact['browser_download_url'] = 'http://intranet.example/attachments/1234'
            data['assets'].append({'name': artifact['name'] + '.sha256', 'browser_download_url': 'http://intranet.example/attachments/5678'})
            with patch('mito.network.opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.side_effect = [json.dumps(data).encode(), ('a'*64 + '  ' + artifact['name']).encode()]
                info = self_update.check(root)
                self.assertEqual(info['url'], artifact['browser_download_url'])
                self.assertEqual(info['hash'], 'sha256:' + 'a'*64)
                urls = [call.args[0].full_url for call in opener.return_value.open.call_args_list]
                self.assertEqual(urls, [source, 'http://intranet.example/attachments/5678'])
                self.assertEqual(self_update.check(root), info)
                self.assertEqual(opener.return_value.open.call_count, 2)
