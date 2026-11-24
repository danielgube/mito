from pathlib import Path
import tempfile
import unittest

from async_support import AsyncTestCase, click, settle, until

from textual.widgets import DataTable, Input, ContentSwitcher, Checkbox, Select, Button, Static
from unittest.mock import patch
from mito.catalog import fingerprint
from mito.engine import Engine
from mito.storage import write_json, read_json
from mito.ui import YitoApp, OpenApplicationDialog


class UITests(AsyncTestCase):
    def test_checkbox_states_have_distinct_red_and_green_colors(self):
        self.assertIn('Checkbox > .toggle--button { color: #8A4B50; }', YitoApp.CSS)
        self.assertIn('Checkbox.-on > .toggle--button { color: #69FF64; }', YitoApp.CSS)

    async def test_busy_indicator_is_delayed_and_ignores_previous_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(150,45)):
                await app.workers.wait_for_complete()
                indicator = app.query_one('#busy-indicator', Static)
                self.assertEqual(str(indicator.content), '')
                app.start_busy_indicator()
                sequence = app.busy_sequence
                self.assertEqual(str(indicator.content), '')
                app.show_busy_indicator(sequence - 1)
                self.assertEqual(str(indicator.content), '')
                app.show_busy_indicator(sequence)
                self.assertEqual(str(indicator.content), '⏳ …')
                app.stop_busy_indicator()
                self.assertEqual(str(indicator.content), '')

    async def test_import_review_identifies_component_replacement(self):
        from mito.profile_transfer_ui import ImportReviewDialog
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('one')
            app = YitoApp(engine)
            old = {'key':'main/tool', 'version':'1', 'manifest_sha256':'old'}
            new = dict(old, version='2', manifest_sha256='new')
            async with app.run_test(size=(150,45)) as pilot:
                await app.workers.wait_for_complete()
                app.push_screen(ImportReviewDialog('new', {'main/tool':new}, {'main/tool':old}))
                await pilot.pause()
                text = '\n'.join(str(widget.content) for widget in app.screen.query(Static))
                self.assertIn('Sustituir 1 en todos los profiles que lo usan', text)
                self.assertNotIn('Ya instalado', text)

    async def test_real_update_refreshes_rows_and_preserves_activation(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root)
            engine.initialize()
            records = []
            for version in ('1.0', '2.0'):
                m = {'version':version, 'url':'https://example.org/tool.zip', 'hash':'0'*64}
                record = {'key':'main/tool', 'version':version, 'manifest':m, 'manifest_sha256':fingerprint(m),
                    'path':'core/apps/main/tool'}
                if version == '1.0':
                    write_json(root/record['path']/'.mito.json', {'key':'main/tool', 'record':record, 'manifest_sha256':fingerprint(m)})
                records.append(record)
            old_id, new_id = [engine.install_id('main/tool', r) for r in records]
            engine.save_profile('one', {'workspace':'one', 'apps':[old_id], 'env':{}})
            engine.save_profile('two', {'workspace':'two', 'apps':[old_id], 'env':{}})
            engine.save_profile('three', {'workspace':'three', 'apps':[], 'env':{}})
            write_json(root/'core/buckets/main.json', {'apps':{'tool':records[1]['manifest']}})
            app = YitoApp(engine, 'one')
            async with app.run_test(size=(150,45)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                await click(pilot, '#update-0')
                await app.workers.wait_for_complete()
                await pilot.pause()
                await asyncio.gather(app.refresh_home(), app.refresh_home())
                await pilot.pause()
                self.assertEqual(list(app.inventory), [new_id])
                self.assertEqual(len(app.query('#installed-list Checkbox')), 1)
                self.assertTrue(app.query_one('#active-0', Checkbox).value)
                self.assertEqual(app.query_one('#active-0', Checkbox).name, new_id)
                self.assertFalse(app.query('.component-update'))
                self.assertEqual(engine.profile('one')['apps'], [new_id])
                self.assertEqual(engine.profile('two')['apps'], [new_id])
                self.assertEqual(engine.profile('three')['apps'], [])
                # A queued event from the previous profile must not edit the
                # newly selected profile before its rows have been rebuilt.
                previous = app.query_one('#active-0', Checkbox)
                app.query_one('#profile', Select).value = 'three'
                app.on_checkbox_changed(Checkbox.Changed(previous, True))
                await pilot.pause()
                self.assertEqual(engine.profile('three')['apps'], [])
                previous = app.query_one('#active-0', Checkbox)
                app.query_one('#profile', Select).value = 'two'
                app.on_checkbox_changed(Checkbox.Changed(previous, False))
                await pilot.pause()
                await app.workers.wait_for_complete()
                self.assertEqual(engine.profile('two')['apps'], [new_id])
                self.assertTrue(app.query_one('#active-0', Checkbox).value)

    async def test_startup_refresh_detects_update_for_inactive_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root)
            engine.initialize()
            engine.save_profile('one', {'workspace':'one', 'apps':[], 'env':{}})
            old = {'version':'1.0', 'url':'https://example.org/tool.zip', 'hash':'0'*64}
            record = {'key':'main/tool', 'version':'1.0', 'manifest':old,
                'manifest_sha256':fingerprint(old), 'path':'core/apps/main/tool'}
            write_json(root/record['path']/'.mito.json', {'key':'main/tool', 'record':record, 'manifest_sha256':fingerprint(old)})
            source = root/'source'
            write_json(source/'bucket/tool.json', dict(old, version='2.0'))
            write_json(root/'core/buckets/main.json', {'source':str(source), 'apps':{'tool':old}})
            app = YitoApp(engine, 'one')
            async with app.run_test(size=(150,45)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.query_one('#update-0', Button).variant, 'success')
                self.assertFalse(app.query_one('#update-0', Button).disabled)
                self.assertFalse(app.query_one('#active-0', Checkbox).value)
                self.assertEqual(engine.installed()[engine.install_id('main/tool', record)]['version'], '1.0')

    def setUp(self):
        language = patch('mito.i18n.system_language', return_value='español')
        language.start()
        self.addCleanup(language.stop)
        import zipfile
        def download(url, target, *args, **kwargs):
            with zipfile.ZipFile(target, 'w') as output:
                output.writestr('tool.exe', b'new')
        downloader = patch('mito.engine.download', side_effect=download)
        downloader.start()
        self.addCleanup(downloader.stop)
        patcher = patch('mito.network.Network.check', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        picker = patch('mito.profile_transfer_ui.choose_export_file', return_value=None)
        picker.start()
        self.addCleanup(picker.stop)
        search = patch('mito.host_data.candidates', return_value=[])
        search.start()
        self.addCleanup(search.stop)

    async def test_host_application_add_open_shortcut_info_and_remove(self):
        from mito import host_apps
        from mito.host_apps_ui import HostInfoDialog, RemoveHostDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root/'mito')
            engine.initialize()
            engine.save_profile('one', {'workspace':'one','apps':[],'env':{}})
            engine.save_profile('two', {'workspace':'two','apps':[],'env':{}})
            application = root/'Host Application.exe'
            application.write_bytes(b'fixture')
            app = YitoApp(engine, 'one')
            with patch('mito.host_apps.discover',return_value=[]), patch('mito.host_apps.choose_file',return_value=None) as picker, patch('mito.host_apps.launch') as launch, patch('mito.host_apps.shortcut',return_value=root/'example.lnk') as shortcut, patch('mito.persistence.open_location') as open_location:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(app.query_one('#browse').region.y, app.query_one('#host-add').region.y)
                    self.assertLess(app.query_one('#browse').region.x, app.query_one('#host-add').region.x)
                    await click(pilot, '#host-add')
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    self.assertEqual(host_apps.inventory(engine.root),{})
                    picker.return_value = str(application)
                    await click(pilot, '#host-add')
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    identity = next(iter(host_apps.inventory(engine.root)))
                    await click(pilot, f'#host-open-{identity}')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, OpenApplicationDialog)
                    app.screen.query_one('#application-arguments',Input).value = 'run "two words"'
                    app.screen.query_one('#application-process', Select).value = True
                    await click(pilot, '#application-open-independent')
                    await app.workers.wait_for_complete()
                    launch.assert_called_once_with(engine,'one',identity,['run','two words'],coupled=True)
                    app.query_one('#profile',Select).value = 'two'
                    await pilot.pause()
                    await click(pilot, f'#host-link-{identity}')
                    app.screen.query_one('#shortcut-badge',Input).value = 'TEST'
                    app.screen.query_one('#shortcut-arguments',Input).value = '--host "two words"'
                    app.screen.query_one('#shortcut-coupled',Select).value = True
                    await click(pilot, '#shortcut-confirm')
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    shortcut.assert_called_once_with(engine,'two',identity,'TEST',coupled=True,
                        arguments=['--host','two words'],color='naranja')
                    await click(pilot, f'#host-info-{identity}')
                    self.assertIsInstance(app.screen,HostInfoDialog)
                    await click(pilot, '#host-folder-open')
                    open_location.assert_called_once_with(application.parent)
                    await click(pilot, '#host-info-close')
                    await click(pilot, f'#host-remove-{identity}')
                    self.assertIsInstance(app.screen,RemoveHostDialog)
                    await pilot.press('escape')
                    self.assertIn(identity,host_apps.inventory(engine.root))
                    await click(pilot, f'#host-remove-{identity}')
                    await click(pilot, '#host-remove-confirm')
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(host_apps.inventory(engine.root),{})
                    self.assertEqual(application.read_bytes(),b'fixture')

    async def test_global_configuration_editor_controls_save_and_cancel(self):
        from mito.configuration_ui import ConfigurationDialog, SettingRow
        from mito.configuration import parse
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.initialize()
            path = engine.root/'config.properties'
            path.write_text('# Keep this comment\nenv.CUSTOM=old\n')
            app = YitoApp(engine)
            async with app.run_test(size=(150,45)) as pilot:
                await pilot.pause()
                await click(pilot, '#configuration')
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfigurationDialog)
                shell_row = next(r for r in app.screen.query(SettingRow) if r.key == 'terminal.defaultShell')
                self.assertEqual(shell_row.query_one('.setting-value', Select).value, 'cmd')
                row = app.screen.query_one(SettingRow)
                self.assertEqual(row.query_one('.setting-key', Input).value, 'CUSTOM')
                row.query_one('.setting-value', Input).value = 'cancelled'
                await pilot.press('escape')
                self.assertEqual(parse(path.read_text()), {'env.CUSTOM':'old'})
                await click(pilot, '#configuration')
                await pilot.pause()
                shell_row = next(r for r in app.screen.query(SettingRow) if r.key == 'terminal.defaultShell')
                shell_row.query_one('.setting-value', Select).value = 'powershell'
                kind = app.screen.query_one('#configuration-kind', Select)
                kind.value = 'enable_proxy_authentication'
                await pilot.pause()
                self.assertTrue(app.screen.query_one('#configuration-name',Input).disabled)
                await click(pilot, '#configuration-add')
                await settle(app, pilot)
                row = next(r for r in app.screen.query(SettingRow) if r.key == kind.value)
                self.assertIsInstance(row.query_one('.setting-key'), Static)
                row.query_one('.setting-value', Select).value = 'true'
                kind.value = 'proxy_authentication_username_field'
                await pilot.pause()
                await click(pilot, '#configuration-add')
                await settle(app, pilot)
                row = next(r for r in app.screen.query(SettingRow) if r.key == kind.value)
                self.assertIsInstance(row.query_one('.setting-key'), Static)
                row.query_one('.setting-value', Input).value = 'login'
                kind.value = 'env.'
                await pilot.pause()
                app.screen.query_one('#configuration-name', Input).value = 'NEW'
                await click(pilot, '#configuration-add')
                await settle(app, pilot)
                row = next(r for r in app.screen.query(SettingRow) if r.key == 'env.NEW')
                row.query_one('.setting-value', Input).value = 'a=b'
                old = next(r for r in app.screen.query(SettingRow) if r.key == 'env.CUSTOM')
                old.scroll_visible(immediate=True)
                await pilot.pause()
                self.assertTrue(await click(pilot, old.query_one('.setting-remove', Button)))
                await pilot.pause()
                self.assertFalse(any(r.key == 'env.CUSTOM' for r in app.screen.query(SettingRow)))
                await click(pilot, '#configuration-save')
                await until(pilot, lambda: not isinstance(app.screen, ConfigurationDialog))
                self.assertNotIsInstance(app.screen, ConfigurationDialog)
                self.assertEqual(parse(path.read_text(encoding='utf-8')), {'mito.language':'system', 'mcp.jobs.monitor':'false', 'terminal.defaultShell':'powershell', 'enable_proxy_authentication':'true', 'proxy_authentication_username_field':'login', 'env.NEW':'a=b'})
                self.assertIn('# Keep this comment',path.read_text())

    async def test_pocketool_configuration_and_update_dialog(self):
        from mito.configuration_ui import SettingRow
        from mito.pocketools_ui import PocketoolsDialog
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)/'mito')
            engine.initialize()
            source = Path(tmp)/'source'
            write_json(source/'pocketool.json', {'info':'Example tools'})
            (source/'hello.cmd').write_text('@echo hello')
            app = YitoApp(engine)
            async with app.run_test(size=(150,45)) as pilot:
                await pilot.pause()
                await click(pilot, '#configuration')
                app.screen.query_one('#configuration-kind',Select).value = 'pocketool.'
                await pilot.pause()
                app.screen.query_one('#configuration-name',Input).value = 'example'
                await click(pilot, '#configuration-add')
                row = next(row for row in app.screen.query(SettingRow) if row.key == 'pocketool.example')
                row.query_one('.setting-value',Input).value = str(source)
                await click(pilot, '#configuration-save')
                await pilot.pause()
                await click(pilot, '#pocketools')
                self.assertIsInstance(app.screen,PocketoolsDialog)
                await click(pilot, '#pocketool-update-0')
                await settle(app, pilot)
                self.assertIn('Example tools',str(app.screen.query_one('#pocketool-info-0',Static).content))
                self.assertTrue((engine.root/'pocketools/example/hello.cmd').exists())
                (source/'pocketool.json').unlink()
                await click(pilot, '#pocketool-update-0')
                await settle(app, pilot)
                self.assertIn('Error:',str(app.screen.query_one('#pocketools-status',Static).content))
                self.assertTrue((engine.root/'pocketools/example/hello.cmd').exists())
                await click(pilot, '#pocketools-close')
                self.assertNotIsInstance(app.screen,PocketoolsDialog)

    async def test_profile_configuration_editor_validation_and_save(self):
        from mito.configuration_ui import ConfigurationDialog, SettingRow
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.initialize()
            engine.save_profile('test', {'workspace':'test', 'apps':[], 'env':{}})
            app = YitoApp(engine)
            async with app.run_test(size=(150,45)) as pilot:
                await pilot.pause()
                await click(pilot, '#manage-profile')
                app.screen.query_one('#profile-configuration').scroll_visible(immediate=True)
                await pilot.pause()
                await click(pilot, '#profile-configuration')
                await until(pilot, lambda: isinstance(app.screen, ConfigurationDialog))
                self.assertIsInstance(app.screen, ConfigurationDialog)
                app.screen.query_one('#configuration-name',Input).value = 'PATH'
                await click(pilot, '#configuration-add')
                await until(pilot, lambda: app.screen.query(SettingRow))
                row = app.screen.query_one(SettingRow)
                row.query_one('.setting-value',Input).value = 'value'
                await click(pilot, '#configuration-save')
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfigurationDialog)
                self.assertIn('reservada',str(app.screen.query_one('#configuration-error',Static).content))
                self.assertEqual(engine.profile('test')['env'],{})
                row.query_one('.setting-key',Input).value = 'CUSTOM'
                self.assertTrue(await click(pilot, '#configuration-save'), 'Guardar no alcanzable tras el error')
                await settle(app, pilot)
                await until(pilot, lambda: engine.profile('test')['env'] == {'CUSTOM':'value'})
                shown = str(app.screen.query_one('#configuration-error',Static).content) if isinstance(app.screen, ConfigurationDialog) else ''
                self.assertEqual(engine.profile('test')['env'],{'CUSTOM':'value'}, shown)
                self.assertFalse((engine.root/'config.properties').exists())

    async def test_altered_download_is_explained_in_a_centered_dialog(self):
        from mito.transfer import DownloadIntegrityError
        from mito.ui import DownloadIntegrityDialog
        from textual.widgets import TextArea
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root)
            engine.create_profile('test')
            app = YitoApp(engine)
            error = DownloadIntegrityError('Hash incorrecto', url='https://github.com/o/r/releases/download/v1/jdk.zip',
                                           size=1867, host='release-assets.githubusercontent.com')
            with patch('mito.network.Network.check', return_value=True), \
                 patch.object(app, 'install_from_catalog', side_effect=error):
                async with app.run_test(size=(130,42)) as pilot:
                    await pilot.pause()
                    app.begin('install', 'test', 'java/temurin8-jdk')
                    await until(pilot, lambda: isinstance(app.screen, DownloadIntegrityDialog))
                    details = app.screen.query_one('#integrity-details', TextArea).text
                    self.assertIn('https://github.com/o/r/releases/download/v1/jdk.zip', details)
                    self.assertIn('1867', details)
                    self.assertIn('release-assets.githubusercontent.com', details)
                    self.assertIn('proxy', ' '.join(str(s.render()) for s in app.screen.query('Static')))
                    await pilot.press('escape')
                    await until(pilot, lambda: not isinstance(app.screen, DownloadIntegrityDialog))

    async def test_long_engine_step_shows_centered_notice_that_closes_itself(self):
        import threading
        from mito.engine import LongStep
        from mito.ui import LongStepDialog
        release = threading.Event()
        def install(target, log, confirm):
            log(LongStep('Moviendo 566 archivos de java/graalvm', active=True))
            release.wait(10)
            log(LongStep('Archivos java/graalvm: 566/566', active=False))
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('test')
            app = YitoApp(engine)
            with patch('mito.network.Network.check', return_value=True), \
                 patch.object(app, 'install_from_catalog', side_effect=install):
                async with app.run_test(size=(130,42)) as pilot:
                    await pilot.pause()
                    app.begin('install', 'test', 'java/graalvm')
                    shown = await until(pilot, lambda: isinstance(app.screen, LongStepDialog))
                    release.set()
                    self.assertTrue(shown)
                    text = ' '.join(str(s.render()) for s in app.screen.query('Static'))
                    self.assertIn('566 archivos', text)
                    self.assertNotIn('antivirus', text.lower())
                    await until(pilot, lambda: not isinstance(app.screen, LongStepDialog))
                    self.assertNotIsInstance(app.screen, LongStepDialog)
                    self.assertIn('Archivos java/graalvm: 566/566', app.log_messages)

    async def test_periodic_network_check_turns_button_red_without_prompting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'config.properties').write_text('network.poll_seconds=0')
            engine = Engine(root)
            engine.create_profile('test')
            app = YitoApp(engine)
            with patch('mito.network.Network.check', return_value=True) as check, \
                 patch('mito.network.Network.local_network', return_value=True), \
                 patch('mito.network.diagnose') as diagnose:
                async with app.run_test(size=(130,42)) as pilot:
                    await until(pilot, lambda: str(app.query_one('#network-status', Button).label) == 'Internet accesible')
                    self.assertFalse(hasattr(app, 'network_timer'))
                    button = app.query_one('#network-status', Button)
                    self.assertEqual(button.variant, 'default')
                    check.return_value = False
                    app.poll_network()
                    await until(pilot, lambda: button.variant == 'error')
                    self.assertEqual(str(button.label), 'Internet no accesible')
                    check.assert_called_with(log=False)
                    diagnose.assert_not_called()
                    check.return_value = True
                    app.poll_network()
                    await until(pilot, lambda: button.variant == 'default')

    async def test_network_status_and_cancel_dialog(self):
        from mito.network import Diagnosis
        from mito.network_ui import ProxyDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'config.properties').write_text('enable_proxy_authentication=true\nproxy_authentication_url=https://portal.example/login')
            engine = Engine(root)
            engine.create_profile('test')
            app = YitoApp(engine)
            # The portal is already configured: the diagnosis proposes no changes.
            portal = Diagnosis('portal', portal='https://portal.example/login')
            with patch('mito.network.Network.check', return_value=False), patch('mito.network.Network.local_network', return_value=True), \
                 patch('mito.network.diagnose', return_value=portal), patch('mito.network.Network.authenticate') as auth:
                async with app.run_test(size=(130,42)) as pilot:
                    await pilot.pause()
                    self.assertEqual(str(app.query_one('#network-status', Button).label), 'Internet no accesible')
                    app.query_one('#network-status',Button).scroll_visible(immediate=True)
                    await pilot.pause()
                    await click(pilot, '#network-status')
                    await settle(app, pilot)
                    await until(pilot, lambda: isinstance(app.screen, ProxyDialog))
                    self.assertIsInstance(app.screen, ProxyDialog)
                    app.screen.query_one('#proxy-password', Input).value = 'secret'
                    await pilot.press('escape')
                    await pilot.pause()
                    self.assertNotIsInstance(app.screen, ProxyDialog)
                    auth.assert_not_called()
                    app.query_one('#network-status',Button).scroll_visible(immediate=True)
                    await pilot.pause()
                    await click(pilot, '#network-status')
                    await settle(app, pilot)
                    await until(pilot, lambda: isinstance(app.screen, ProxyDialog))
                    auth.return_value = True
                    app.screen.query_one('#proxy-user', Input).value = 'test-user'
                    app.screen.query_one('#proxy-password', Input).value = 'test-password'
                    await click(pilot, '#proxy-submit')
                    await settle(app, pilot)
                    await until(pilot, lambda: not isinstance(app.screen, ProxyDialog))
                    auth.assert_called_once_with('test-user', 'test-password')
                    self.assertNotIsInstance(app.screen, ProxyDialog)

    async def test_unverified_download_popup_cancel_and_confirm(self):
        import zipfile
        from mito.ui import UnverifiedDownloadsDialog
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('test')
            write_json(engine.root/'core/buckets/main.json', {'apps':{'tool':{'version':'1','url':'http://example.org/tool.zip','bin':'tool.exe'}}})
            source = engine.root / 'source'
            write_json(source/'tool.json', engine.catalog.resolve('main/tool')[1])
            write_json(engine.root/'core/buckets/main.json', {'source': str(source),
                'apps': {'tool': engine.catalog.resolve('main/tool')[1]}})
            def fetch(url,path,expected,**kwargs):
                self.assertIsNone(expected)
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('tool.exe',b'fixture')
            app = YitoApp(engine)
            with patch('mito.engine.download', side_effect=fetch) as download:
                async with app.run_test(size=(130,42)) as pilot:
                    await pilot.pause()
                    app.begin('install','test','main/tool')
                    # The dialog is opened by the install worker, not synchronously.
                    await until(pilot, lambda: isinstance(app.screen, UnverifiedDownloadsDialog))
                    self.assertIsInstance(app.screen, UnverifiedDownloadsDialog)
                    download.assert_not_called()
                    await pilot.press('escape')
                    await app.workers.wait_for_complete()
                    self.assertFalse(engine.installed())
                    app.begin('install','test','main/tool')
                    self.assertTrue(await until(pilot, lambda: isinstance(app.screen, UnverifiedDownloadsDialog)))
                    await click(pilot, '#unverified-confirm')
                    await app.workers.wait_for_complete()
                    download.assert_called_once()
                    self.assertEqual(len(engine.installed()),1)

    async def test_log_errors_are_red_and_copy_keeps_plain_text(self):
        from textual.widgets import RichLog
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.initialize()
            app = YitoApp(engine)
            with patch('mito.ui.subprocess.run') as copy:
                async with app.run_test(size=(130,42)) as pilot:
                    await pilot.pause()
                    log = app.query_one('#log', RichLog)
                    with patch.object(log, 'write', wraps=log.write) as write:
                        app.write_log('Instalado: aplicación [test]')
                        app.write_log('Error: acceso denegado', level='error')
                        self.assertEqual(write.call_args.args[0].style, 'bold red')
                        self.assertEqual(write.call_args.args[0].plain, 'Error: acceso denegado')
                    app.busy = True
                    await click(pilot, '#copy-log')
                    await app.workers.wait_for_complete()
                    self.assertEqual(copy.call_args.kwargs['input'], 'Instalado: aplicación [test]\nError: acceso denegado')
                    self.assertNotIn('acceso denegado', ' '.join(copy.call_args.args[0]))
                    app.busy = False

    async def test_export_import_profile_through_dialogs(self):
        from mito.profile_transfer_ui import ImportReviewDialog
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root)
            engine.create_profile('source')
            data = engine.profile('source')
            data['env'] = {'TOKEN':'secret'}
            engine.save_profile('source',data)
            shared = root/'shared.json'
            app = YitoApp(engine)
            async with app.run_test(size=(130,42)) as pilot:
                await pilot.pause()
                await click(pilot, '#manage-profile')
                app.screen.query_one('#export-profile',Button).scroll_visible(immediate=True)
                await pilot.pause()
                await click(pilot, '#export-profile')
                app.screen.query_one('#transfer-file',Input).value = str(shared)
                await click(pilot, '#transfer-accept')
                await app.workers.wait_for_complete()
                self.assertEqual(read_json(shared), {'apps':[]})
                await click(pilot, '#manage-profile')
                app.screen.query_one('#import-profile',Button).scroll_visible(immediate=True)
                await pilot.pause()
                with patch('mito.profile_transfer_ui.choose_export_file', return_value=str(shared)) as picker:
                    await click(pilot, '#import-profile')
                    await app.workers.wait_for_complete()
                    picker.assert_called_once()
                self.assertEqual(app.screen.query_one('#transfer-file',Input).value, str(shared))
                with patch('mito.profile_transfer_ui.choose_export_file', return_value=None):
                    await click(pilot, '#transfer-browse')
                    await app.workers.wait_for_complete()
                self.assertEqual(app.screen.query_one('#transfer-file',Input).value, str(shared))
                app.screen.query_one('#transfer-name',Input).value = 'destination'
                await click(pilot, '#transfer-accept')
                await pilot.pause()
                self.assertIsInstance(app.screen,ImportReviewDialog)
                self.assertNotIn('destination',engine.profiles())
                await click(pilot, '#import-confirm')
                await app.workers.wait_for_complete()
                self.assertEqual(engine.profile('destination'), {'workspace':'destination','apps':[],'env':{}})
                self.assertEqual(app.current_profile(),'destination')

    async def test_compact_sidebar_version_and_create_profile_dialog(self):
        from mito import __version__
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.initialize()
            app = YitoApp(engine)
            async with app.run_test(size=(130,42)) as pilot:
                await pilot.pause()
                self.assertIn(__version__,app.title)
                self.assertFalse(app.query('#home-info'))
                self.assertEqual([b.id for b in app.query('#sidebar Button')], ['manage-profile','home-button','check-updates','self-update','open-home','configuration','pocketools','mcp-button','mcp-jobs-button','network-status'])
                self.assertFalse(app.query_one('#self-update', Button).display)
                with patch('mito.persistence.open_location') as open_location:
                    await click(pilot, '#open-home')
                    open_location.assert_called_once_with(engine.root/'home')
                await click(pilot, '#manage-profile')
                # Layout assertions require the newly mounted dialog to render.
                await pilot.pause()
                self.assertTrue(app.screen.query_one('#delete-profile',Button).disabled)
                self.assertEqual(app.screen.query_one('#export-profile').region.y, app.screen.query_one('#import-profile').region.y)
                title = app.screen.query_one('.dialog-title')
                self.assertEqual(title.styles.content_align, ('center', 'middle'))
                self.assertGreater(app.screen.query_one('#managed-profile-name').region.y, title.region.bottom)
                await click(pilot, '#manage-profile-close')
                self.assertFalse(app.query('#manage-profile-dialog'))
                await click(pilot, '#manage-profile')
                app.screen.query_one('#new-profile',Input).value = 'new'
                await click(pilot, '#create')
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.current_profile(),'new', app.log_messages)

    async def test_catalog_filter_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root)
            engine.initialize()
            write_json(root/'core/buckets/main.json', {'apps':{'hello':{'version':'1', 'description':'Greeting', 'url':'https://example.org/a.zip', 'hash':'0'*64}, 'other':{'version':'1', 'description':'Other', 'url':'https://example.org/b.zip', 'hash':'0'*64}}})
            app = YitoApp(engine)
            async with app.run_test(size=(130, 42)) as pilot:
                await pilot.pause()
                self.assertEqual(app.query_one(ContentSwitcher).current, 'home-page')
                self.assertEqual(app.query_one(DataTable).row_count, 0)
                await click(pilot, '#browse')
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count, 2)
                app.query_one('#search', Input).value = 'hello'
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count, 1)
                self.assertEqual(app.selected, 'main/hello')
                self.assertFalse(app.query_one('#install', Button).disabled)
                # Installation is global and keyed by bucket/app, even when its
                # version differs from the catalog and no profile activates it.
                manifest = {'version': '0.9', 'url': 'https://example.org/old.zip', 'hash': '0'*64}
                record = {'key': 'main/hello', 'version': '0.9', 'path': 'core/apps/main/hello',
                          'manifest': manifest, 'manifest_sha256': fingerprint(manifest)}
                marker = root / record['path'] / '.mito.json'
                write_json(marker, {'key': record['key'], 'record': record, 'manifest_sha256': fingerprint(manifest)})
                await app.operation_done(app.current_profile())
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).get_row('main/hello')[2], 'Instalado')
                self.assertTrue(app.query_one('#install', Button).disabled)
                self.assertIn('Instalado', str(app.query_one('#details', Static).content))
                with patch.object(app, 'begin') as begin:
                    await click(pilot, '#install')
                    begin.assert_not_called()
                app.query_one('#search', Input).value = 'other'
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).get_row('main/other')[2], 'Disponible')
                self.assertFalse(app.query_one('#install', Button).disabled)
                app.query_one('#search', Input).value = 'no-results'
                await pilot.pause()
                self.assertIsNone(app.selected)
                self.assertTrue(app.query_one('#install', Button).disabled)
                marker.unlink()
                app.query_one('#search', Input).value = 'hello'
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).get_row('main/hello')[2], 'Disponible')
                self.assertFalse(app.query_one('#install', Button).disabled)

    async def test_home_checkboxes_toggle_distinct_runtimes_without_uninstalling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            engine=Engine(root)
            engine.initialize()
            for name in ('first','second'):
                engine.save_profile(name,{'workspace':name,'apps':[], 'env':{}})
            for version in ('11','21','25'):
                m={'version':version,'url':'https://example.org/java.zip','hash':'0'*64,'env_set':{'JAVA_HOME':'$dir'}}
                record={'key':f'java/jdk{version}','version':version,'path':f'core/apps/java/jdk{version}','manifest':m,'manifest_sha256':fingerprint(m)}
                write_json(root/record['path']/'.mito.json',{'key':record['key'],'manifest_sha256':fingerprint(m),'record':record})
            app=YitoApp(engine)
            with patch('mito.terminal.prepare'):
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(len(app.query(Checkbox)),3)
                    self.assertEqual(len([b for b in app.query('.component Button') if b.id.startswith(('open-', 'link-'))]),0)
                    rows = list(app.query('.component'))
                    self.assertEqual([row.region.height for row in rows], [1, 1, 1])
                    self.assertTrue(all(row.styles.margin.bottom == 1 for row in rows))
                    self.assertEqual([row.has_class('component-row-light') for row in rows], [False, True, False])
                    self.assertTrue(all(checkbox.region.height == 1 for checkbox in app.query('.component Checkbox')))
                    for index in range(3):
                        await click(pilot, f'#active-{index}')
                        await app.workers.wait_for_complete()
                        await pilot.pause()
                    self.assertEqual(len(engine.profile('first')['apps']),3)
                    await click(pilot, '#active-0')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(len(engine.profile('first')['apps']),2)
                    self.assertEqual(len(engine.installed()),3)
                    app.query_one('#profile',Select).value='second'
                    await pilot.pause()
                    self.assertTrue(all(not c.value for c in app.query(Checkbox)))

    async def test_application_buttons_only_visible_for_active_launchable_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            engine=Engine(root)
            engine.initialize()
            m={'version':'1','shortcuts':[['editor.exe','Editor']]}
            record={'key':'extras/editor','version':'1','path':'core/apps/extras/editor','manifest':m,'manifest_sha256':fingerprint(m)}
            write_json(root/record['path']/'.mito.json',{'key':'extras/editor','manifest_sha256':fingerprint(m),'record':record})
            (root/record['path']/'editor.exe').touch()
            installation=engine.install_id('extras/editor',record)
            engine.save_profile('test',{'workspace':'test','apps':[installation],'env':{}})
            app=YitoApp(engine)
            with patch('mito.shortcuts.application_link',return_value=root/'Desktop/editor.lnk') as link, patch.object(engine, 'launch') as launch:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(len([b for b in app.query('.component Button') if b.id.startswith(('open-', 'link-'))]),2)
                    self.assertTrue(all(button.region.height == 1 for button in app.query('.component Button')))
                    self.assertFalse(app.query('#badge'))
                    await click(pilot, '#open-0')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, OpenApplicationDialog)
                    app.screen.query_one('#application-arguments',Input).value = '--file "two words"'
                    await click(pilot, '#application-open-independent')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    launch.assert_called_once_with('test',installation,
                        arguments=['--file','two words'],coupled=False,shortcut='Editor')
                    await click(pilot, '#link-0')
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one('#shortcut-badge', Input).value,'MITO')
                    self.assertEqual(app.screen.query_one('#shortcut-badge', Input).max_length,4)
                    link.assert_not_called()
                    await click(pilot, '#shortcut-cancel')
                    await pilot.pause()
                    link.assert_not_called()
                    await click(pilot, '#link-0')
                    await pilot.pause()
                    app.screen.query_one('#shortcut-badge', Input).value='A B'
                    await click(pilot, '#shortcut-confirm')
                    await pilot.pause()
                    link.assert_not_called()
                    app.screen.query_one('#shortcut-badge', Input).value='DEV1'
                    await click(pilot, '#shortcut-color-azul')
                    app.screen.query_one('#shortcut-arguments',Input).value='--new-window "a b"'
                    await click(pilot, '#shortcut-confirm')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    link.assert_called_once_with(engine,'test',installation,'DEV1',coupled=False,
                        arguments=['--new-window','a b'],shortcut='Editor',color='azul')
                    await click(pilot, '#active-0')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(len([b for b in app.query('.component Button') if b.id.startswith(('open-', 'link-'))]),0)

    async def test_mito_shortcut_and_delete_selected_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            engine=Engine(root)
            engine.initialize()
            engine.create_profile('first')
            engine.create_profile('second')
            app=YitoApp(engine,'second')
            with patch('mito.shortcuts.mito_link',return_value=root/'desktop/MITO.lnk') as link:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertEqual(app.current_profile(),'second')
                    self.assertFalse(app.query('#terminal'))
                    await click(pilot, '#manage-profile')
                    await click(pilot, '#mito-shortcut')
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one(Input).value,'MITO')
                    await click(pilot, '#shortcut-confirm')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    link.assert_called_once_with(engine,'second','MITO',color='naranja')
                    await click(pilot, '#manage-profile')
                    await click(pilot, '#delete-profile')
                    self.assertIn('second',engine.profiles())
                    await pilot.press('escape')
                    self.assertIn('second',engine.profiles())
                    await click(pilot, '#manage-profile')
                    await click(pilot, '#delete-profile')
                    await click(pilot, '#delete-profile-confirm')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(app.current_profile(),'first')
                    await click(pilot, '#manage-profile')
                    self.assertTrue(app.screen.query_one('#delete-profile',Button).disabled)

    async def test_bucket_filter_and_scrollable_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp))
            engine.initialize()
            for bucket in ('main','extras'):
                write_json(engine.root/f'core/buckets/{bucket}.json',{'apps':{'python':{'version':'3.14','description':'Python language','notes':['WARNING: older version','Not recommended for production.']}}})
            app=YitoApp(engine)
            async with app.run_test(size=(150,45)) as pilot:
                await pilot.pause()
                await click(pilot, '#browse')
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count,2)
                self.assertIn('WARNING: older version',str(app.query_one('#details',Static).content))
                await click(pilot, '#bucket-extras')
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count,1)
                self.assertEqual(app.selected,'main/python')
                self.assertEqual(read_json(app.filter_path),['extras'])

    async def test_component_removal_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp))
            engine.initialize()
            m={'version':'1'}
            record={'key':'main/tool','version':'1','path':'core/apps/main/tool','manifest':m,'manifest_sha256':fingerprint(m)}
            write_json(engine.root/record['path']/'.mito.json',{'key':'main/tool','record':record})
            app=YitoApp(engine)
            with patch('mito.components.remove') as remove:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    await click(pilot, '#remove-0')
                    await pilot.pause()
                    remove.assert_not_called()
                    await click(pilot, '#remove-cancel')
                    await pilot.pause()
                    remove.assert_not_called()
                    await click(pilot, '#remove-0')
                    await pilot.pause()
                    await click(pilot, '#remove-confirm')
                    await app.workers.wait_for_complete()
                    remove.assert_called_once()

    async def test_update_button_only_for_newer_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp))
            engine.initialize()
            m={'version':'1.9','url':'http://example.org/a.zip','hash':'0'*64}
            record={'key':'main/tool','version':'1.9','path':'core/apps/main/tool','manifest':m,'manifest_sha256':fingerprint(m)}
            write_json(engine.root/record['path']/'.mito.json',{'key':'main/tool','record':record})
            write_json(engine.root/'core/buckets/main.json',{'apps':{'tool':dict(m,version='1.10')}})
            app=YitoApp(engine)
            with patch('mito.components.update') as update:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertFalse(app.query_one('#update-0',Button).disabled)
                    self.assertEqual(app.query_one('#update-0',Button).variant, 'success')
                    await click(pilot, '#update-0')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    update.assert_called_once()
                    write_json(engine.root/'core/buckets/main.json',{'apps':{'tool':dict(m,version='1.10',installer={'file':'setup.exe'})}})
                    await app.refresh_home()
                    await pilot.pause()
                    self.assertTrue(app.query_one('#update-0',Button).disabled)
                    write_json(engine.root/'core/buckets/main.json',{'apps':{'tool':m}})
                    await app.refresh_home()
                    await pilot.pause()
                    self.assertFalse(app.query('#update-0'))

    async def test_info_dialog_shows_binaries_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp))
            engine.initialize()
            manifest={'version':'1','description':'Example component','persist':[['config.ini','settings.ini'],'data'],
                      'architecture':{'64bit':{'installer':{'script':'Write-Output "fixture"'}}}}
            record={'key':'main/tool','version':'1','path':'core/apps/main/tool','manifest':manifest,'manifest_sha256':fingerprint(manifest)}
            write_json(engine.root/record['path']/'.mito.json',{'key':'main/tool','record':record})
            target=engine.root/'home/persist/main/tool/settings.ini'
            target.parent.mkdir(parents=True)
            target.write_text('settings')
            app=YitoApp(engine)
            with patch('mito.persistence.open_location') as open_location:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    self.assertFalse(app.query('#persist-0'))
                    await click(pilot, '#info-0')
                    await pilot.pause()
                    self.assertEqual([p for label,p in app.screen.paths],[engine.root/record['path'],target,target.parent/'data'])
                    self.assertIn('Example component',str(app.screen.query_one('#info-description',Static).content))
                    self.assertTrue(any('installer.script: se ejecuta con PowerShell' in str(widget.content)
                                        for widget in app.screen.query(Static)))
                    self.assertTrue(app.screen.query_one('#info-open-2',Button).disabled)
                    await click(pilot, '#info-open-0')
                    open_location.assert_called_once_with(engine.root/record['path'])
                    open_location.reset_mock()
                    await click(pilot, '#info-open-1')
                    open_location.assert_called_once_with(target)
                    self.assertFalse((target.parent/'data').exists())
                    await click(pilot, '#info-close')
                    await pilot.pause()
                    self.assertFalse(app.query('#component-info'))
