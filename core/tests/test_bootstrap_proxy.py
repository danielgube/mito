"""Detección de proxy y portal del bootstrap PowerShell, sin red real."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.parse


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap.ps1'


def bootstrap_functions():
    # Sólo definiciones: nunca ejecuta el bootstrap real ni descarga nada.
    return BOOTSTRAP.read_text(encoding='utf-8-sig').split("$ErrorActionPreference = 'Stop'", 1)[0]


class FakeProxy(BaseHTTPRequestHandler):
    """Proxy HTTP local: el host de la URL absoluta elige el comportamiento."""
    authenticated = False
    posts = []

    def log_message(self, *args):
        pass

    def reply(self, status, headers=(), body=b''):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/generate_204':
            if url.hostname == 'ok.invalid' or FakeProxy.authenticated:
                return self.reply(204)
            if url.hostname == 'ntlm.invalid':
                return self.reply(407, [('Proxy-Authenticate', 'NTLM'), ('Proxy-Authenticate', 'Negotiate')])
            return self.reply(302, [('Location', 'http://captive.invalid/login?from=check')])
        if url.path == '/login':
            action = 'http://other.invalid/submit' if url.hostname == 'foreign.invalid' else '/submit'
            page = ('<html><form method="post" action="' + action + '">'
                    '<input type="hidden" name="csrf" value="tok&amp;en">'
                    '<input type="text" name="user"><input type="password" name="pass">'
                    '</form></html>').encode()
            return self.reply(200, [('Content-Type', 'text/html')], page)
        self.reply(404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
        fields = urllib.parse.parse_qs(body)
        FakeProxy.posts.append(fields)
        if fields.get('pass') == ['secret-pass'] and fields.get('csrf') == ['tok&en']:
            FakeProxy.authenticated = True
        self.reply(302, [('Location', '/done')])


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 bootstrap')
class BootstrapProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_script(self, body):
        script = self.root / 'proxy-check.ps1'
        root = str(self.root).replace("'", "''")
        script.write_text('[Console]::OutputEncoding = New-Object Text.UTF8Encoding\n' + bootstrap_functions()
                          + f"\nInitialize-YitoDiagnostics '{root}'\n" + body, encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_system_proxy_no_proxy_form_and_properties_helpers(self):
        config = self.root / 'config.properties'
        config.write_bytes(b'# comment\nhttp_proxy=old\nbucket.main=x\n')
        path = str(config).replace("'", "''")
        result = self.run_script(f'''
$target = [Uri]'https://www.python.org/'
$fixture = New-Object Net.WebProxy('http://10.1.2.3:8080')
$pac = Get-YitoSystemProxy $target $fixture @{{AutoConfigURL='http://pac.example/proxy.pac'; ProxyOverride='<local>;*.corp.example'}}
$manual = Get-YitoSystemProxy $target $fixture @{{ProxyEnable=1; ProxyServer='10.1.2.3:8080'; AutoConfigURL='http://pac.example/proxy.pac'}}
$port80 = Get-YitoSystemProxy $target (New-Object Net.WebProxy('http://10.1.2.3')) @{{}}
$direct = Get-YitoSystemProxy $target (New-Object Net.WebProxy) @{{}}
$form = Get-YitoPortalForm '<form action="/a?x=1&amp;y=2"><input type=hidden name=csrf value="t&amp;k"><input name="login"><input type="password" name="pwd"></form>'
Set-YitoProperties '{path}' ([ordered]@{{http_proxy='http://p:80'; https_proxy='http://p:80'}})
[ordered]@{{
    noProxy = ConvertTo-YitoNoProxy '<local>;*.corp.example;intranet;10.*;CORP.example'
    pacUrl = $pac.Url; pacSource = $pac.Source; pacNoProxy = $pac.NoProxy
    manualSource = $manual.Source; port80 = $port80.Url; direct = ($null -eq $direct)
    action = $form.Action; user = $form.UserField; password = $form.PasswordField; csrf = $form.Fields['csrf']
    kinds = @(
        (Get-YitoProxyKind @{{Status=204}} 204), (Get-YitoProxyKind @{{Status=407}} 204),
        (Get-YitoProxyKind @{{Status=302; Location='http://portal/'}} 204), (Get-YitoProxyKind @{{Status=0}} 204),
        (Get-YitoProxyKind @{{Status=403}} 204))
}} | ConvertTo-Json -Compress
''')
        self.assertEqual(result['noProxy'], 'localhost,127.0.0.1,::1,.corp.example,intranet,corp.example')
        self.assertEqual(result['pacUrl'], 'http://10.1.2.3:8080')
        self.assertEqual(result['pacSource'], 'script PAC http://pac.example/proxy.pac')
        self.assertEqual(result['pacNoProxy'], 'localhost,127.0.0.1,::1,.corp.example')
        self.assertEqual(result['manualSource'], 'configuración manual de Windows')
        self.assertEqual(result['port80'], 'http://10.1.2.3:80')
        self.assertTrue(result['direct'])
        self.assertEqual((result['action'], result['user'], result['password'], result['csrf']),
                         ('/a?x=1&y=2', 'login', 'pwd', 't&k'))
        self.assertEqual(result['kinds'], ['ok', 'auth', 'portal', 'unreachable', 'blocked'])
        # Conserva comentarios y orden, sustituye claves existentes y escribe UTF-8 sin BOM.
        self.assertEqual(config.read_bytes(),
                         b'# comment\nhttp_proxy=http://p:80\nbucket.main=x\nhttps_proxy=http://p:80\n')

    def test_probe_and_portal_login_through_local_proxy(self):
        FakeProxy.authenticated = False
        FakeProxy.posts = []
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeProxy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        proxy = f'http://127.0.0.1:{server.server_address[1]}'
        result = self.run_script(f'''
$script:YitoSettings['proxy_authentication_username_field'] = 'user'
$script:YitoSettings['proxy_authentication_password_field'] = 'pass'
$proxy = '{proxy}'
$check = 'http://captive.invalid/generate_204'
$ntlm = Invoke-YitoHttp 'http://ntlm.invalid/generate_204' $proxy
$portal = Invoke-YitoHttp $check $proxy
$result = [ordered]@{{
    ok = Get-YitoProxyKind (Invoke-YitoHttp 'http://ok.invalid/generate_204' $proxy) 204
    auth = Get-YitoProxyKind $ntlm 204; schemes = $ntlm.Authenticate
    portal = Get-YitoProxyKind $portal 204; location = $portal.Location
}}
try {{ [void](Invoke-YitoPortalLogin 'http://captive.invalid/login' $proxy $false 'ana' (ConvertTo-SecureString 'x' -AsPlainText -Force) $check 204); $result.https = 'accepted' }}
catch {{ $result.https = $_.Exception.Message }}
$script:YitoSettings['proxy_authentication_require_https'] = 'false'
try {{ [void](Invoke-YitoPortalLogin 'http://foreign.invalid/login' $proxy $false 'ana' (ConvertTo-SecureString 'x' -AsPlainText -Force) $check 204); $result.origin = 'accepted' }}
catch {{ $result.origin = $_.Exception.Message }}
$result.wrong = Invoke-YitoPortalLogin 'http://captive.invalid/login' $proxy $false 'ana' (ConvertTo-SecureString 'wrong' -AsPlainText -Force) $check 204
$result.login = Invoke-YitoPortalLogin 'http://captive.invalid/login' $proxy $false 'ana' (ConvertTo-SecureString 'secret-pass' -AsPlainText -Force) $check 204
$result | ConvertTo-Json -Compress
''')
        self.assertEqual((result['ok'], result['auth'], result['portal']), ('ok', 'auth', 'portal'))
        self.assertEqual(result['schemes'], 'NTLM, Negotiate')
        self.assertEqual(result['location'], 'http://captive.invalid/login?from=check')
        self.assertIn('HTTPS', result['https'])
        self.assertIn('otro origen', result['origin'])
        self.assertFalse(result['wrong'])
        self.assertTrue(result['login'])
        # Sólo los dos intentos válidos llegan a POST, con campos ocultos y usuario.
        self.assertEqual(len(FakeProxy.posts), 2)
        self.assertEqual(FakeProxy.posts[-1], {'csrf': ['tok&en'], 'user': ['ana'], 'pass': ['secret-pass']})
        logs = '\n'.join(p.read_text(encoding='utf-8') for p in (self.root / 'logs').glob('mito.log*')) if (self.root / 'logs').exists() else ''
        self.assertNotIn('secret-pass', logs)


if __name__ == '__main__':
    unittest.main()
