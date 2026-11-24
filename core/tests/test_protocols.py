from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import winreg

from async_support import AsyncTestCase, until
from mito import open_with, protocols
from mito.storage import YitoError


class StubEngine:
    def __init__(self, root):
        self.root = root

    def profile(self, name):
        return {'apps': ['main/claude']}

    def selected(self, data):
        return {'main/claude': {'key': 'main/claude'}}

    def installed(self):
        return {'main/claude': {'key': 'main/claude'}}

    def application_entries(self, lock):
        return [('Claude', 'claude.exe', [])]

    def application_target(self, lock, shortcut=None):
        if shortcut not in (None, 'Claude'):
            raise YitoError('Shortcut unavailable or ambiguous')
        return 'claude.exe', []


class ProtocolTests(unittest.TestCase):
    """«Reclamar protocolo»: clave URL en HKCU (rama de prueba) que lanza mito run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()/'mito'
        self.engine = StubEngine(self.root)
        self.base = rf'Software\MITO-tests-{uuid.uuid4().hex}'
        self.classes = self.base + r'\Classes'
        for target, value in (('mito.protocols.CLASSES', self.classes),
                              ('mito.protocols.MACHINE_CLASSES', None),
                              ('mito.open_with.notify', lambda: None),
                              ('mito.open_with.icon_for', lambda *args, **kwargs: self.root/'core/state/icons/x.ico')):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(open_with.delete_tree, self.base)
        self.quiet = lambda message: None

    def host_key(self, scheme='claude', program=r'C:\Users\me\AppData\Local\AnthropicClaude\app-1\claude.exe'):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.classes + '\\' + scheme) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'URL:{scheme}')
            winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
            winreg.SetValueEx(key, 'Blob', 0, winreg.REG_BINARY, b'\x00\x01')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.classes + '\\' + scheme + r'\shell\open\command') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, f'"{program}" -- "%1"')
        return f'"{program}" -- "%1"'

    def command(self, scheme='claude'):
        return protocols.read_command(winreg.HKEY_CURRENT_USER, self.classes, scheme)

    def test_scheme_names_are_validated_and_reserved_ones_rejected(self):
        self.assertEqual(protocols.scheme_name(' Claude:// '), 'claude')
        for text in ('', 'x', '1abc', 'a b', 'http', 'HTTPS', 'file', 'ms-settings', 'ms-teams', 'mito'):
            with self.assertRaises(YitoError):
                protocols.scheme_name(text)

    def test_candidates_match_declared_executable_names(self):
        self.host_key('claude')
        self.host_key('other', r'C:\Tools\other.exe')
        self.assertEqual(protocols.candidates(self.engine, {'key': 'main/claude'}), ['claude'])

    def test_claim_backs_up_and_release_restores_exactly(self):
        original = self.host_key()
        before = protocols.snapshot(winreg.HKEY_CURRENT_USER, self.classes + r'\claude')
        protocols.claim(self.engine, 'default', 'main/claude', 'claude', 'Claude', self.quiet)
        line = self.command()
        self.assertIn(str(self.root/'core/tools/python/pythonw.exe'), line)
        self.assertIn('run --profile default --app main/claude --shortcut Claude', line)
        self.assertTrue(line.endswith(' -- "%1"'))
        self.assertEqual(protocols.status(self.root, 'claude', 'default', 'main/claude')[0], 'mine')
        self.assertEqual(protocols.status(self.root, 'claude', 'work', 'main/claude')[0], 'profile')
        protocols.unregister(self.root, lambda record: True, self.quiet)
        self.assertEqual(self.command(), original)
        self.assertEqual(protocols.snapshot(winreg.HKEY_CURRENT_USER, self.classes + r'\claude'), before)
        self.assertEqual(protocols.registry(self.root), {})

    def test_release_leaves_a_key_another_application_took_back(self):
        self.host_key()
        protocols.claim(self.engine, 'default', 'main/claude', 'claude', log=self.quiet)
        taken = self.host_key(program=r'C:\Host\app-2\claude.exe')
        self.assertEqual(protocols.status(self.root, 'claude', 'default', 'main/claude')[0], 'other')
        protocols.unregister(self.root, lambda record: True, self.quiet)
        self.assertEqual(self.command(), taken)

    def test_session_reasserts_overwritten_claim_and_restores_the_first_backup(self):
        original = self.host_key()
        session = protocols.Session(self.engine, 'default', 'main/claude', self.quiet)
        row = session.claim('claude', 'Claude')
        self.assertEqual(row['status'], 'mine')
        self.assertFalse(session.check('claude'))
        # The MITO copy registers itself directly, as Electron apps do on start.
        self.host_key(program=str(self.root/'core/apps/main/claude/claude.exe'))
        self.assertTrue(session.check('claude'))
        self.assertEqual(protocols.status(self.root, 'claude', 'default', 'main/claude')[0], 'mine')
        self.assertIn('--shortcut Claude', self.command())
        session.release()
        self.assertEqual(self.command(), original)
        self.assertEqual(session.schemes, {})
        self.assertEqual(protocols.registry(self.root), {})

    def test_session_release_leaves_a_key_taken_after_the_last_check(self):
        self.host_key()
        session = protocols.Session(self.engine, 'default', 'main/claude', self.quiet)
        session.claim('claude')
        taken = self.host_key(program=r'C:\Host\app-2\claude.exe')
        session.release()
        self.assertEqual(self.command(), taken)

    def test_claim_of_absent_scheme_is_removed_on_release(self):
        protocols.claim(self.engine, 'default', 'main/claude', 'claude', log=self.quiet)
        self.assertIsNotNone(self.command())
        protocols.unregister(self.root, lambda record: True, self.quiet)
        self.assertIsNone(protocols.snapshot(winreg.HKEY_CURRENT_USER, self.classes + r'\claude'))

    def test_direct_mito_executable_is_reported(self):
        self.host_key(program=str(self.root/'core/apps/main/claude/claude.exe'))
        self.assertEqual(protocols.status(self.root, 'claude', 'default', 'main/claude')[0], 'direct')

    def test_another_profile_takes_over_but_keeps_the_host_backup(self):
        original = self.host_key()
        protocols.claim(self.engine, 'default', 'main/claude', 'claude', log=self.quiet)
        protocols.claim(self.engine, 'work', 'main/claude', 'claude', log=self.quiet)
        self.assertEqual(protocols.registry(self.root)['claude']['profile'], 'work')
        protocols.unregister(self.root, lambda record: record['profile'] == 'work', self.quiet)
        self.assertEqual(self.command(), original)

    def test_inactive_component_is_rejected(self):
        with self.assertRaises(YitoError):
            protocols.claim(self.engine, 'default', 'main/other', 'claude', log=self.quiet)
        self.assertEqual(protocols.registry(self.root), {})

    def test_rows_list_candidates_with_their_state(self):
        self.host_key()
        self.assertEqual([(row['scheme'], row['status']) for row in protocols.rows(self.engine, 'default', 'main/claude')],
                         [('claude', 'other')])


class FakeSession:
    """Session double: records calls; check() reports one overwrite."""

    def __init__(self):
        self.schemes = {}
        self.calls = []
        self.overwritten = True

    def describe(self, scheme):
        return {'scheme': protocols.scheme_name(scheme), 'status': 'absent', 'command': None}

    def claim(self, scheme, shortcut=None):
        self.calls.append(('claim', scheme, shortcut))
        self.schemes[scheme] = shortcut
        return {'scheme': scheme, 'status': 'mine', 'command': 'mito run'}

    def check(self, scheme):
        result, self.overwritten = self.overwritten, False
        return result

    def release(self, scheme=None):
        self.calls.append(('release', scheme))
        if scheme is None:
            self.schemes.clear()
        else:
            self.schemes.pop(scheme, None)


class ProtocolDialogTests(AsyncTestCase):
    async def test_claim_watch_and_close_release(self):
        from textual.app import App
        from textual.widgets import Button, Input
        from mito.protocols_ui import ProtocolDialog
        session = FakeSession()
        closed = []
        rows = [{'scheme': 'claude', 'status': 'other', 'command': '"C:\\Host\\claude.exe" -- "%1"'}]

        class Host(App):
            def on_mount(self):
                self.push_screen(ProtocolDialog('main/claude', [('Claude', 'claude.exe', [])], rows, session,
                                                watch=0.1), closed.append)

        app = Host()
        async with app.run_test(size=(120, 50)) as pilot:
            self.assertTrue(await until(pilot, lambda: app.screen.query('.protocol-toggle')))
            screen = app.screen
            screen.query_one('#protocol-scheme', Input).value = 'http'
            screen.query_one('#protocol-add', Button).press()
            await pilot.pause()
            self.assertEqual(len(screen.query('.protocol-toggle')), 1)
            screen.query_one('.protocol-toggle', Button).press()
            self.assertTrue(await until(pilot, lambda: session.schemes == {'claude': 'Claude'}))
            # The watcher re-asserts it and says so.
            self.assertTrue(await until(pilot, lambda: 'Recuperado' in screen.notes.get('claude', '')))
            screen.query_one('#protocol-close', Button).press()
            self.assertTrue(await until(pilot, lambda: closed))
        self.assertEqual(session.calls, [('claim', 'claude', 'Claude'), ('release', None)])
        self.assertEqual(session.schemes, {})


if __name__ == '__main__':
    unittest.main()
