import http.server
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from mito.network import Network, intercept, opener, proxy_environment
from mito.storage import YitoError


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def config(self, text):
        (self.root/'config.properties').write_text(text, encoding='utf-8')

    def test_proxy_routing_and_explicit_empty(self):
        self.config('http_proxy=http://proxy.example:8080\nhttps_proxy=\nno_proxy=localhost')
        env = proxy_environment(self.root, {'http_proxy':'old', 'HTTPS_PROXY':'old'})
        self.assertEqual(env['HTTP_PROXY'], 'http://proxy.example:8080')
        self.assertEqual(env['HTTPS_PROXY'], '')
        self.assertNotIn('http_proxy', env)
        client = opener(self.root, env)
        proxy = next(h for h in client.handlers if hasattr(h, 'proxies'))
        self.assertEqual(proxy.proxies['http'], 'http://proxy.example:8080')
        self.assertEqual(proxy.proxies['no'], 'localhost')
        self.config('http_proxy=http://user:secret@proxy.example')
        with self.assertRaises(YitoError):
            proxy_environment(self.root, {})

    def test_portal_post_hidden_fields_and_independent_connectivity(self):
        state = {'authenticated':False, 'posts':0}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == '/login':
                    self.send_response(200); self.end_headers()
                    self.wfile.write(b'<form action="/login"><input name="token" value="abc"><input name="password" type="password"></form>')
                elif self.path == '/redirect':
                    self.send_response(302); self.send_header('Location','/login'); self.end_headers()
                else:
                    self.send_response(204 if state['authenticated'] else 200); self.end_headers()
            def do_POST(self):
                fields = urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                state['posts'] += 1
                state['authenticated'] = fields == {'token':['abc'], 'username':['test-user'], 'password':['test-password'], 'extra':['value']}
                self.send_response(302); self.send_header('Location','/check'); self.end_headers()
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        self.config(f'http_proxy=\nhttps_proxy=\nno_proxy=127.0.0.1\nnetwork.check_url={url}/check\nnetwork.timeout_seconds=1\nenable_proxy_authentication=true\nproxy_authentication_url={url}/login\nproxy_authentication_form_field.extra=value')
        network = Network(self.root)
        self.assertFalse(network.check())
        with self.assertRaises(YitoError):
            network.authenticate('test-user','test-password')
        self.assertEqual(state['posts'], 0)
        with (self.root/'config.properties').open('a') as f:
            f.write('\nproxy_authentication_require_https=false')
        network = Network(self.root)
        self.assertTrue(network.authenticate('test-user','test-password'))
        self.assertEqual(state['posts'], 1)
        network.url = url + '/redirect'
        self.assertFalse(network.check())
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn('test-password',path.read_text())

    def test_interceptor_cancellation_offline_and_success(self):
        with patch('mito.network.Network') as cls, patch('mito.network.prompt_credentials', return_value=None) as prompt:
            network = cls.return_value
            network.check.return_value = False
            network.local_network.return_value = True
            network.enabled = True
            self.assertIsNone(intercept(self.root))
            prompt.assert_called_once()
            network.authenticate.assert_not_called()
            prompt.reset_mock()
            network.check.return_value = True
            intercept(self.root)
            prompt.assert_not_called()
            network.check.return_value = False
            network.local_network.return_value = False
            intercept(self.root)
            prompt.assert_not_called()
            network.local_network.return_value = True
            credentials = {'username':'test','password':'secret'}
            prompt.return_value = credentials
            intercept(self.root)
            network.authenticate.assert_called_once_with('test','secret')
            self.assertEqual(credentials,{})
