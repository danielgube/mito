"""Autoconfiguración de red desde el botón INTERNET: diagnóstico, guardado y portal."""
import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from async_support import AsyncTestCase, click, until
from mito.configuration import parse
from mito.engine import Engine
from mito.network import Diagnosis, Network, apply, diagnose, no_proxy_from_override

CHECK = 'http://check.test/generate_204'
PORTAL = 'http://portal.test/login'


class Proxy:
    """Proxy HTTP local: recibe URLs absolutas y responde según el modo."""
    def __init__(self, test, mode):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if mode == 'auth':
                    self.send_response(407)
                    self.send_header('Proxy-Authenticate', 'Negotiate')
                    self.send_header('Proxy-Authenticate', 'NTLM')
                    self.end_headers()
                elif self.path == PORTAL:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'<form action="/login"><input name="token" type="hidden" value="x"><input name="user" type="text"><input name="pass" type="password"></form>')
                elif mode == 'portal':
                    self.send_response(302)
                    self.send_header('Location', PORTAL)
                    self.end_headers()
                else:
                    self.send_response(204)
                    self.end_headers()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (('check', False), ('local_network', True)):
            patcher = patch.object(Network, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def config(self, text=''):
        (self.root/'config.properties').write_text(f'# comentario\nnetwork.check_url={CHECK}\nnetwork.timeout_seconds=2\n{text}', encoding='utf-8')

    def test_no_proxy_from_windows_override(self):
        self.assertEqual(no_proxy_from_override('<local>;*.corp.example;intranet;10.*;a/b'), 'localhost,127.0.0.1,::1,.corp.example,intranet')

    def test_stale_configured_proxy_is_replaced_by_the_windows_route(self):
        proxy = Proxy(self, 'ok')
        self.config('http_proxy=http://127.0.0.1:1\nhttps_proxy=http://127.0.0.1:1\n')
        found = {'url': proxy.url, 'source': 'pac', 'no_proxy': 'localhost'}
        result = diagnose(self.root, resolve=lambda url: found)
        self.assertEqual((result.kind, result.proxy, result.source), ('ok', proxy.url, 'pac'))
        self.assertEqual(result.changes, {'http_proxy': proxy.url, 'https_proxy': proxy.url, 'no_proxy': 'localhost'})

    def test_portal_behind_detected_proxy_proposes_portal_keys_without_credentials(self):
        proxy = Proxy(self, 'portal')
        self.config()
        result = diagnose(self.root, resolve=lambda url: {'url': proxy.url, 'source': 'wpad', 'no_proxy': 'localhost'})
        self.assertEqual((result.kind, result.portal), ('portal', PORTAL))
        self.assertEqual(result.changes, {
            'http_proxy': proxy.url, 'https_proxy': proxy.url, 'no_proxy': 'localhost',
            'proxy_authentication_url': PORTAL,
            'proxy_authentication_username_field': 'user',
            'proxy_authentication_password_field': 'pass',
            'enable_proxy_authentication': 'true'})

    def test_configured_portal_needs_no_changes(self):
        proxy = Proxy(self, 'portal')
        self.config(f'http_proxy={proxy.url}\nhttps_proxy={proxy.url}\nenable_proxy_authentication=true\nproxy_authentication_url={PORTAL}\n')
        result = diagnose(self.root, resolve=lambda url: None)
        self.assertEqual((result.kind, result.changes), ('portal', {}))

    def test_proxy_407_is_reported_and_not_saved(self):
        proxy = Proxy(self, 'auth')
        self.config()
        result = diagnose(self.root, resolve=lambda url: {'url': proxy.url, 'source': 'manual', 'no_proxy': ''})
        self.assertEqual((result.kind, result.status, result.authenticate), ('auth', 407, 'NTLM, Negotiate'))
        self.assertEqual(result.changes, {})

    def test_offline_and_online_short_circuit(self):
        resolve = unittest.mock.Mock()
        with patch.object(Network, 'local_network', return_value=False):
            self.assertEqual(diagnose(self.root, resolve=resolve).kind, 'offline')
        with patch.object(Network, 'check', return_value=True):
            self.assertEqual(diagnose(self.root, resolve=resolve).kind, 'ok')
        resolve.assert_not_called()

    def test_apply_keeps_comments_and_other_keys(self):
        self.config('terminal.defaultShell=powershell\n')
        apply(Engine(self.root), {'https_proxy': 'http://proxy.example:8080', 'no_proxy': 'localhost'})
        text = (self.root/'config.properties').read_text(encoding='utf-8')
        self.assertIn('# comentario', text)
        self.assertEqual(parse(text)['terminal.defaultShell'], 'powershell')
        self.assertEqual(parse(text)['https_proxy'], 'http://proxy.example:8080')


class NetworkSetupUITests(AsyncTestCase):
    async def test_detected_portal_is_saved_then_credentials_are_asked_for_the_session(self):
        from mito.network_ui import NetworkSetupDialog, ProxyDialog
        from mito.ui import YitoApp
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp))
            engine.create_profile('test')
            path = engine.root/'config.properties'
            changes = {'https_proxy': 'http://proxy.example:8080', 'enable_proxy_authentication': 'true', 'proxy_authentication_url': PORTAL}
            found = Diagnosis('portal', 'http://proxy.example:8080', 'pac', portal=PORTAL, changes=changes)
            app = YitoApp(engine)
            with patch('mito.network.Network.check', return_value=False), \
                 patch('mito.network.Network.local_network', return_value=True), \
                 patch('mito.network.diagnose', return_value=found):
                async with app.run_test(size=(130, 42)) as pilot:
                    await app.workers.wait_for_complete()
                    # Cancelar no escribe nada.
                    app.check_network(prompt=True)
                    self.assertTrue(await until(pilot, lambda: isinstance(app.screen, NetworkSetupDialog)))
                    await click(pilot, '#network-setup-cancel')
                    await until(pilot, lambda: not isinstance(app.screen, NetworkSetupDialog))
                    self.assertFalse(path.exists())
                    # Guardar escribe las claves detectadas y pide credenciales sólo para la sesión.
                    app.check_network(prompt=True)
                    self.assertTrue(await until(pilot, lambda: isinstance(app.screen, NetworkSetupDialog)))
                    self.assertIn('proxy_authentication_url=' + PORTAL, str(app.screen.query_one('#network-setup-keys').content))
                    await click(pilot, '#network-setup-save')
                    self.assertTrue(await until(pilot, lambda: isinstance(app.screen, ProxyDialog)))
                    self.assertEqual({key: parse(path.read_text(encoding='utf-8'))[key] for key in changes}, changes)
                    await pilot.press('escape')
                    self.assertTrue(await until(pilot, lambda: not isinstance(app.screen, ProxyDialog)))


if __name__ == '__main__':
    unittest.main()
