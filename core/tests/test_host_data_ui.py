from pathlib import Path
import tempfile
import unittest

from async_support import AsyncTestCase, click
from unittest.mock import patch

from textual.widgets import Button
from mito import host_apps, host_data
from mito.engine import Engine
from mito.host_apps_ui import HostDataConfirm
from mito.ui import YitoApp


class HostDataUITests(AsyncTestCase):
    async def test_add_offers_roaming_then_local_and_accepts_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            engine = Engine(base/'mito')
            engine.initialize()
            host = base/'host'
            host.mkdir()
            exe = host/'opera.exe'
            exe.write_bytes(b'fixture')
            data, cache = host/'Roaming/Opera Software', host/'Local/Opera Software'
            data.mkdir(parents=True)
            cache.mkdir(parents=True)
            app = YitoApp(engine)
            with patch('mito.network.Network.check', return_value=True), patch('mito.host_apps.choose_file', return_value=str(exe)), patch('mito.host_data.host_folder', side_effect=lambda kind: host/host_data.KINDS[kind]), patch('mito.host_data.candidates', side_effect=lambda root, identity, kind: [data if kind == 'data' else cache]) as search:
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    await click(pilot, '#host-add')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, HostDataConfirm)
                    self.assertIn('Host data:', app.screen.message)
                    await click(pilot, '#host-data-accept')
                    # Junction creation happens in a worker before the next dialog.
                    for _ in range(40):
                        await pilot.pause(0.1)
                        if isinstance(app.screen, HostDataConfirm) and 'Host cache:' in app.screen.message:
                            break
                    self.assertIsInstance(app.screen, HostDataConfirm)
                    self.assertIn('Host cache:', app.screen.message)
                    await pilot.press('escape')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    identity = next(iter(host_apps.inventory(engine.root)))
                    self.assertTrue(host_data.linked(engine.root, identity, 'data'))
                    self.assertFalse(host_data.linked(engine.root, identity, 'cache'))
                    self.assertEqual([call.args[2] for call in search.call_args_list], ['data', 'cache'])
                    with patch('mito.host_data.choose_folder') as picker:
                        await click(pilot, f'#host-cache-{identity}')
                        await pilot.pause()
                        self.assertIsInstance(app.screen, HostDataConfirm)
                        self.assertIn(str(cache), app.screen.message)
                        picker.assert_not_called()
                        await click(pilot, '#host-data-accept')
                        await app.workers.wait_for_complete()
                        await pilot.pause()
                        self.assertTrue(host_data.linked(engine.root, identity, 'cache'))
                    buttons = [app.query_one(f'#host-{action}-{identity}', Button)
                               for action in ('open', 'cache', 'data', 'link', 'info', 'remove')]
                    self.assertEqual(len({button.region.y for button in buttons}), 1)
                    self.assertLessEqual(buttons[-1].region.right, app.size.width)
                    host_apps.remove(engine.root, identity)

    async def test_manual_link_cancel_replace_unlink_and_buttons_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            engine = Engine(base/'mito')
            engine.initialize()
            host = base/'host'
            folder = host/'App Data'
            folder.mkdir(parents=True)
            (folder/'session').write_text('keep')
            exe = host/'app.exe'
            exe.write_bytes(b'fixture')
            identity = host_apps.add(engine.root, exe)
            local = engine.root/'home/AppData/Roaming/App Data'
            local.mkdir(parents=True)
            (local/'portable').write_text('local')
            app = YitoApp(engine)
            with patch('mito.host_data.candidates', return_value=[]), patch('mito.network.Network.check', return_value=True), patch('mito.host_data.host_folder', return_value=host), patch('mito.host_data.choose_folder', return_value=str(folder)):
                async with app.run_test(size=(150,45)) as pilot:
                    await pilot.pause()
                    button = f'#host-data-{identity}'
                    self.assertIn('✗', str(app.query_one(button, Button).label))
                    await click(pilot, button)
                    await pilot.pause()
                    self.assertIsInstance(app.screen, HostDataConfirm)
                    await click(pilot, '#host-data-accept')
                    await pilot.pause()
                    self.assertIsInstance(app.screen, HostDataConfirm)
                    await pilot.press('escape')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual((local/'portable').read_text(), 'local')
                    await click(pilot, button)
                    await pilot.pause()
                    await click(pilot, '#host-data-accept')
                    await pilot.pause()
                    await click(pilot, '#host-data-accept')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertTrue(local.is_junction())
                    self.assertIn('✓', str(app.query_one(button, Button).label))
                    # Forget metadata to simulate a junction made outside MITO.
                    host_data.save(engine.root, {})
                    await app.refresh_home()
                    await pilot.pause()
                    self.assertEqual(str(app.query_one(button, Button).label), 'Data · ✓')
                    await click(pilot, button)
                    await pilot.pause()
                    await click(pilot, '#host-data-accept')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertFalse(local.exists())
                    self.assertIn('✗', str(app.query_one(button, Button).label))
                    self.assertEqual((folder/'session').read_text(), 'keep')
                    self.assertFalse(app.query_one(f'#host-cache-{identity}', Button).disabled)
