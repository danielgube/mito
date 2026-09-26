import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from textual.widgets import Button, TextArea, Select
from mito.engine import Engine
from mito.ui import YitoApp, UnverifiedDownloadsDialog
from mito.mcp_ui import MCPDialog, MCPJobsDialog


class MCPUITests(unittest.IsolatedAsyncioTestCase):
    async def test_jobs_monitor_is_optional_and_separate(self):
        from mito.configuration import Configuration
        from mito.configuration_ui import ConfigurationDialog
        from mito.storage import YitoError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize(); engine.create_profile('one')
            self.assertEqual(ConfigurationDialog(engine).settings.values['mcp.jobs.monitor'], 'false')
            with self.assertRaises(YitoError):
                Configuration(engine).save([('mcp.jobs.monitor', 'invalid')])
            app = YitoApp(engine)
            async with app.run_test(size=(130, 48)) as pilot:
                await app.workers.wait_for_complete()
                button = app.query_one('#mcp-jobs-button', Button)
                self.assertFalse(button.display)
                app.push_screen(MCPDialog()); await pilot.pause()
                self.assertFalse(app.screen.query('#mcp-job'))
                await pilot.press('escape')
                Configuration(engine).save([('mcp.jobs.monitor', 'true')])
                app.refresh_mcp_monitor(); self.assertTrue(button.display)
                button.scroll_visible(animate=False); await pilot.pause()
                await pilot.click('#mcp-jobs-button'); await pilot.pause()
                self.assertIsInstance(app.screen, MCPJobsDialog)
                app.mcp.state = 'enabled'
                app.mcp.jobs = [{'job_id': 'job1', 'name': 'command_start', 'profile': 'one', 'state': 'running'}]
                with patch.object(app.mcp, '_send') as send:
                    app.screen.refresh_state(); await pilot.pause()
                    send.assert_not_called()
                    app.screen.query_one('#mcp-job', Select).value = 'job1'
                    await pilot.pause(); await pilot.click('#mcp-read')
                    send.assert_called_once_with({'type': 'output', 'job_id': 'job1', 'stream': 'stdout'})
                    app.mcp.events.put({'type': 'output', 'job': {'job_id': 'job1'}, 'output': {'text': 'result', 'stream': 'stdout'}})
                    await app.poll_mcp(); await pilot.pause()
                    self.assertIn('result', app.screen.query_one('#mcp-output', TextArea).text)
                Configuration(engine).save([('mcp.jobs.monitor', 'false')])
                app.refresh_mcp_monitor(); await pilot.pause()
                self.assertFalse(button.display)
                self.assertNotIsInstance(app.screen, MCPJobsDialog)
                app.mcp.state = 'disabled'

    async def test_dialog_is_opt_in_and_snippets_have_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize(); engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(130, 48)) as pilot:
                await app.workers.wait_for_complete()
                self.assertIsNone(app.mcp.thread)
                await pilot.click('#mcp-button'); await pilot.pause()
                self.assertIsInstance(app.screen, MCPDialog)
                text = app.screen.query_one('#mcp-config', TextArea).text
                self.assertIn('[mcp_servers.', text); self.assertNotIn('token', text)
                for client in ('Claude Code', 'VS Code', 'HTTP'):
                    app.screen.query_one('#mcp-client', Select).value = client
                    app.screen.refresh_state()
                    self.assertIn('127.0.0.1', app.screen.query_one('#mcp-config', TextArea).text)
                await pilot.press('escape'); self.assertIsNone(app.mcp.thread)
                with patch.object(app.mcp, 'start') as start:
                    app.start_mcp(12345); start.assert_called_once_with(12345)
                app.mcp.events.put({'type': 'stopped'})
                await app.poll_mcp()
                self.assertIsNone(app.mcp_timer)

    async def test_confirmation_and_external_changes_preserve_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)); engine.initialize(); engine.create_profile('one')
            app = YitoApp(engine)
            async with app.run_test(size=(130, 48)) as pilot:
                await app.workers.wait_for_complete()
                app.mcp.state = 'enabled'
                app.mcp.events.put({'type': 'confirm', 'job_id': 'job', 'downloads': ['unsigned']})
                with patch.object(app.mcp, 'approve') as approve:
                    await app.poll_mcp(); await pilot.pause()
                    self.assertIsInstance(app.screen, UnverifiedDownloadsDialog)
                    await pilot.click('#unverified-cancel'); await pilot.pause()
                    approve.assert_called_once_with('job', False)
                engine.create_profile('two')
                app.mcp.events.put({'type': 'changed'})
                await app.poll_mcp(); await pilot.pause()
                self.assertEqual(app.current_profile(), 'one')
                app.mcp.state = 'disabled'


if __name__ == '__main__': unittest.main()
