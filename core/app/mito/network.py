"""Proxy routing and interactive portal authentication, with no persisted secrets."""
from .i18n import tr
import ctypes
import http.cookiejar
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
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
    """Forms with a password field: [action, fields, has_password, user_field, password_field]."""
    def __init__(self, password_field):
        super().__init__()
        self.password_field = password_field
        self.current = None
        self.forms = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = [attrs.get('action', ''), {}, False, '', '']
        elif tag == 'input' and self.current is not None:
            name = attrs.get('name')
            if name:
                kind = (attrs.get('type') or 'text').lower()
                self.current[1][name] = attrs.get('value', '')
                if name == self.password_field or kind == 'password':
                    self.current[2] = True
                    self.current[4] = self.current[4] or name
                elif not self.current[3] and kind in ('text', 'email'):
                    self.current[3] = name

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


# Python cannot evaluate PAC/WPAD; .NET resolves the same route Windows uses.
SYSTEM_PROXY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = [Uri]$env:MITO_PROXY_TARGET
$registry = @{}
try { $registry = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' } catch { }
$result = @{ url = ''; source = 'wpad'; override = [string]$registry.ProxyOverride }
if ($registry.ProxyEnable -eq 1 -and $registry.ProxyServer) { $result.source = 'manual' }
elseif ($registry.AutoConfigURL) { $result.source = 'pac' }
$resolver = [Net.WebRequest]::GetSystemWebProxy()
if (-not $resolver.IsBypassed($target)) {
    $proxy = $resolver.GetProxy($target)
    if ($proxy -and $proxy.AbsoluteUri -ne $target.AbsoluteUri -and $proxy.Scheme -in @('http', 'https')) {
        $result.url = '{0}://{1}:{2}' -f $proxy.Scheme, $proxy.Host, $proxy.Port
    }
}
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
$result | ConvertTo-Json -Compress
"""
REDIRECTS = (301, 302, 303, 307, 308)


def no_proxy_from_override(override):
    """Windows ProxyOverride to no_proxy: exact names and suffixes only, like the bootstrap."""
    items = ['localhost', '127.0.0.1', '::1']
    for entry in re.split(r'[;,\s]+', override or ''):
        entry = entry.strip().lower()
        if not entry or entry == '<local>':
            continue
        if entry.startswith('*.'):
            entry = entry[1:]
        if '*' in entry or '/' in entry or entry in items:
            continue
        items.append(entry)
    return ','.join(items)


def system_proxy(target, run=subprocess.run):
    """The proxy Windows would use for target, or None for a direct route."""
    if os.name != 'nt':
        return None
    try:
        result = run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '-'],
            input=SYSTEM_PROXY_SCRIPT, capture_output=True, encoding='utf-8', timeout=30,
            env=dict(os.environ, MITO_PROXY_TARGET=target), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    url = data.get('url') or ''
    try:
        validate_url(url)
    except YitoError:
        return None
    return {'url': url, 'source': data.get('source', 'wpad'), 'no_proxy': no_proxy_from_override(data.get('override', ''))}


@dataclass
class Probe:
    status: int = 0
    location: str = ''
    authenticate: str = ''
    error: str = ''
    url: str = ''

    def kind(self, expected):
        if self.status == expected:
            return 'ok'
        if self.status == 407:
            return 'auth'
        if self.status in REDIRECTS and self.location:
            return 'portal'
        return 'unreachable' if self.status == 0 else 'blocked'


def probe(url, proxy, timeout):
    """One request through proxy ('' = direct) without following redirects."""
    route = urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {})
    client = urllib.request.build_opener(route, NoRedirect())
    try:
        with client.open(url, timeout=timeout) as response:
            return Probe(response.status, url=url)
    except urllib.error.HTTPError as exc:
        result = Probe(exc.code, exc.headers.get('Location', ''), ', '.join(sorted({value.split()[0] for value in exc.headers.get_all('Proxy-Authenticate') or [] if value.split()})), url=url)
        exc.close()
        return result
    except (OSError, ValueError) as exc:
        # HTTPS through a proxy fails in CONNECT: http.client only keeps the status text.
        reason = str(getattr(exc, 'reason', exc))
        match = re.search(r'Tunnel connection failed: (\d{3})', reason)
        return Probe(int(match.group(1)) if match else 0, error=type(exc).__name__, url=url)


@dataclass
class Diagnosis:
    kind: str
    proxy: str = ''
    source: str = ''
    status: int = 0
    authenticate: str = ''
    portal: str = ''
    changes: dict = field(default_factory=dict)


def classify(network, proxy):
    """Probe the check URL; an HTTPS check is retried over HTTP to reveal portals and 407."""
    result = probe(network.url, proxy, network.timeout)
    kind = result.kind(network.expected)
    parsed = urllib.parse.urlsplit(network.url)
    if kind != 'ok' and parsed.scheme == 'https':
        plain = probe(urllib.parse.urlunsplit(('http', parsed.netloc, parsed.path, parsed.query, '')), proxy, network.timeout)
        if plain.kind(network.expected) in ('auth', 'portal'):
            result, kind = plain, plain.kind(network.expected)
    return result, kind


def portal_fields(url, proxy, timeout):
    """HTML field names of the portal login form; never credentials."""
    route = urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {})
    try:
        with urllib.request.build_opener(route, NoRedirect()).open(url, timeout=timeout) as page:
            parser = Form('password')
            parser.feed(page.read(1024 * 1024).decode('utf-8', errors='replace'))
    except (OSError, ValueError):
        return {}
    if not parser.forms:
        return {}
    values = {}
    user, password = parser.forms[0][3], parser.forms[0][4]
    if user and user != 'username':
        values['proxy_authentication_username_field'] = user
    if password and password != 'password':
        values['proxy_authentication_password_field'] = password
    return values


def diagnose(root, network=None, resolve=system_proxy):
    """Find a working route when the check fails and propose config.properties changes.

    Order: the configured route, the route Windows uses (manual, PAC or WPAD) and,
    when a configured proxy no longer exists, a direct connection. Nothing is saved here."""
    network = network or Network(root)
    if network.check():
        return Diagnosis('ok')
    if not network.local_network():
        return Diagnosis('offline')
    config = network.config
    configured = 'https_proxy' in config or 'http_proxy' in config
    candidates = []
    if configured:
        candidates.append((config.get('https_proxy', config.get('http_proxy', '')), 'config.properties', None))
    found = resolve(network.url)
    if found and not any(found['url'] == proxy for proxy, _, _ in candidates):
        candidates.append((found['url'], found['source'], found['no_proxy']))
    if not any(proxy == '' for proxy, _, _ in candidates) and (configured or not found):
        # Without proxy keys the direct route is already the default: nothing to save.
        candidates.append(('', 'direct', '' if configured else None))
    results = []
    for proxy, source, no_proxy in candidates:
        result, kind = classify(network, proxy)
        results.append((kind, proxy, source, no_proxy, result))
        if kind == 'ok':
            break
    rank = {'ok': 0, 'portal': 1, 'auth': 2, 'blocked': 3, 'unreachable': 4}
    kind, proxy, source, no_proxy, result = min(results, key=lambda item: rank[item[0]])
    diagnosis = Diagnosis(kind, proxy, source, result.status, result.authenticate)
    if no_proxy is not None and kind in ('ok', 'portal'):
        diagnosis.changes.update({'http_proxy': proxy, 'https_proxy': proxy, 'no_proxy': no_proxy})
    if kind == 'portal':
        try:
            diagnosis.portal = validate_url(urllib.parse.urljoin(result.url, result.location)).geturl()
        except YitoError:
            diagnosis.kind = 'blocked'
            diagnosis.changes.clear()
            return diagnosis
        if not config.get('proxy_authentication_url'):
            diagnosis.changes['proxy_authentication_url'] = diagnosis.portal
            diagnosis.changes.update(portal_fields(diagnosis.portal, proxy, network.timeout))
        if config.get('enable_proxy_authentication', 'false').lower() != 'true':
            diagnosis.changes['enable_proxy_authentication'] = 'true'
    return diagnosis


def apply(engine, changes):
    """Save detected network keys through the configuration editor contract."""
    from .configuration import Configuration
    configuration = Configuration(engine)
    values = dict(configuration.values)
    values.update(changes)
    configuration.save(values.items())


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
