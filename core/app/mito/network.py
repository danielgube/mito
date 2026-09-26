"""Proxy routing and interactive portal authentication, with no persisted secrets."""
from .i18n import tr
import ctypes
import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .storage import YitoError


def properties(root):
    path = root / 'config.properties'
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith(('#', '!')) and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
    return values


def proxy_environment(root, environment):
    values = properties(root)
    result = dict(environment)
    for name in ('http_proxy', 'https_proxy', 'no_proxy'):
        if name in values:
            value = values[name]
            if value and name != 'no_proxy':
                validate_url(value)
            for key in list(result):
                if key.lower() == name:
                    del result[key]
            # Empty values deliberately suppress inherited Windows proxy settings.
            result[name.upper()] = value
    return result


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise YitoError(tr('Configura una URL HTTP/HTTPS sin credenciales'))
    return parsed


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential-bearing POST or accept a portal as Internet.
        return None


class ExplicitProxyHandler(urllib.request.ProxyHandler):
    def proxy_open(self, req, proxy, type):
        if not proxy or urllib.request.proxy_bypass_environment(req.host, self.proxies):
            return None
        return super().proxy_open(req, proxy, type)


def opener(root, environment=None, *handlers):
    env = proxy_environment(root, os.environ) if environment is None else environment
    proxies = urllib.request.getproxies()
    for key, value in env.items():
        if key.lower() in ('http_proxy', 'https_proxy', 'no_proxy'):
            proxies[key.lower().removesuffix('_proxy')] = value
    return urllib.request.build_opener(ExplicitProxyHandler(proxies), *handlers)


class Form(HTMLParser):
    def __init__(self, password_field):
        super().__init__()
        self.password_field = password_field
        self.current = None
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = [attrs.get('action', ''), {}, False]
        elif tag == 'input' and self.current is not None:
            name = attrs.get('name')
            if name:
                self.current[1][name] = attrs.get('value', '')
                self.current[2] |= name == self.password_field or attrs.get('type') == 'password'

    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            if self.current[2]:
                self.forms.append(self.current)
            self.current = None


class Network:
    def __init__(self, root, environment=None):
        self.root = root
        self.environment = environment
        self.config = properties(root)
        self.timeout = float(self.config.get('network.timeout_seconds', '4'))
        if not 0 < self.timeout <= 60:
            raise YitoError(tr('network.timeout_seconds debe estar entre 0 y 60'))
        self.url = self.config.get('network.check_url', 'https://www.gstatic.com/generate_204')
        validate_url(self.url)
        self.expected = int(self.config.get('network.check_status', '204'))
        self.auth_url = self.config.get('proxy_authentication_url', '')
        if self.auth_url:
            validate_url(self.auth_url)
        self.enabled = self.config.get('enable_proxy_authentication', 'false').lower() == 'true' and bool(self.auth_url)

    def check(self):
        try:
            with opener(self.root, self.environment, NoRedirect()).open(self.url, timeout=self.timeout) as response:
                return response.status == self.expected
        except urllib.error.HTTPError as exc:
            exc.close()
            return False
        except (OSError, ValueError, YitoError) as exc:
            from .diagnostics import write, message
            write(self.root, 'warning', 'network.check', message(self.root, exc))
            return False

    def local_network(self):
        if os.name != 'nt':
            return True
        flags = ctypes.c_ulong()
        return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))

    def authenticate(self, username, password):
        if not self.enabled or not username or not password:
            return False
        # Cookies and credentials live only for this request sequence.
        client = opener(self.root, self.environment, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
        user_field = self.config.get('proxy_authentication_username_field', 'username')
        pass_field = self.config.get('proxy_authentication_password_field', 'password')
        try:
            with client.open(self.auth_url, timeout=self.timeout) as page:
                parser = Form(pass_field)
                parser.feed(page.read(1024 * 1024).decode('utf-8', errors='replace'))
            action, fields = self.auth_url, {}
            if parser.forms:
                action = urllib.parse.urljoin(self.auth_url, parser.forms[0][0])
                fields = parser.forms[0][1]
            parsed = validate_url(action)
            original = validate_url(self.auth_url)
            if (parsed.scheme, parsed.netloc) != (original.scheme, original.netloc):
                raise YitoError(tr('El formulario apunta a otro origen; configura su URL explícitamente'))
            if parsed.scheme != 'https' and self.config.get('proxy_authentication_require_https', 'true').lower() != 'false':
                raise YitoError(tr('El POST requiere HTTPS; configura proxy_authentication_require_https=false para un portal HTTP'))
            prefix = 'proxy_authentication_form_field.'
            fields.update({key[len(prefix):]: value for key, value in self.config.items() if key.startswith(prefix)})
            fields.update({user_field: username, pass_field: password})
            body = urllib.parse.urlencode(fields).encode()
            request = urllib.request.Request(action, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
            try:
                with client.open(request, timeout=self.timeout):
                    pass
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code not in (301, 302, 303):
                    return False
            # Check using a fresh client: children cannot inherit portal cookies.
            return self.check()
        except (OSError, ValueError):
            return False


def prompt_credentials(failed=False, translate=tr):
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object Windows.Forms.Form
$f.Text = 'MITO - Autenticación de red'
$f.ClientSize = New-Object Drawing.Size(420,190)
$f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false
$l = New-Object Windows.Forms.Label
$l.Text = 'Usuario y contraseña del portal. Cancelar continúa sin conexión.'
$l.SetBounds(15,10,390,35)
$u = New-Object Windows.Forms.TextBox
$u.SetBounds(15,50,390,25)
$p = New-Object Windows.Forms.TextBox
$p.SetBounds(15,85,390,25)
$p.UseSystemPasswordChar = $true
$ok = New-Object Windows.Forms.Button
$ok.Text = 'Conectar'
$ok.SetBounds(195,135,100,30)
$ok.DialogResult = 'OK'
$cancel = New-Object Windows.Forms.Button
$cancel.Text = 'Cancelar'
$cancel.SetBounds(305,135,100,30)
$cancel.DialogResult = 'Cancel'
$f.Controls.AddRange(@($l,$u,$p,$ok,$cancel))
$f.AcceptButton = $ok
$f.CancelButton = $cancel
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
if ($f.ShowDialog() -eq 'OK') {
    @{username=$u.Text;password=$p.Text} | ConvertTo-Json -Compress
}
$p.Clear()
$f.Dispose()
"""
    if failed:
        script = script.replace('Usuario y contraseña del portal. Cancelar continúa sin conexión.', 'No se pudo verificar Internet. Reintenta o cancela para continuar.')
    from .native_ui import localize_script
    script = localize_script(script, 'MITO - Autenticación de red',
        'Usuario y contraseña del portal. Cancelar continúa sin conexión.',
        'No se pudo verificar Internet. Reintenta o cancela para continuar.',
        'Conectar', 'Cancelar', translate=translate)
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', script], capture_output=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None


def intercept(root, environment=None):
    """Cancel, failed authentication or offline network never prevents app launch."""
    try:
        network = Network(root, environment)
        if network.check() or not network.enabled or not network.local_network():
            return
        failed = False
        from .i18n import Translator
        translate = Translator.from_root(root)
        while True:
            credentials = prompt_credentials(failed, translate=translate)
            if not credentials:
                return
            try:
                if network.authenticate(credentials['username'], credentials['password']):
                    return
            finally:
                credentials.clear()
            failed = True
    except (OSError, ValueError, YitoError):
        return
