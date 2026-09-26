"""Configuration editor storage; only explicit saves write user settings."""
from .i18n import tr
import math
import os
import tempfile

from .storage import YitoError, identifier, mutation
from .i18n import DEFAULT_SETTING, LANGUAGE_CHOICES


# None means a free-text value; tuples are the complete set of allowed values.
PROPERTIES = {
    'terminal.defaultShell': (('cmd', 'powershell', 'mito'), 'cmd'),
    'posthog.enabled': (('true', 'false'), 'false'),
    'posthog.host': (None, ''),
    'posthog.api_key': (None, ''),
    'mcp.jobs.monitor': (('true', 'false'), 'false'),
    'mito.language': (LANGUAGE_CHOICES, DEFAULT_SETTING),
    'mito.update_url': (None, 'https://api.github.com/repos/danielgube/mito/releases/latest'),
    'enable_proxy_authentication': (('true', 'false'), 'false'),
    'proxy_authentication_url': (None, ''),
    'proxy_authentication_username_field': (None, 'username'),
    'proxy_authentication_password_field': (None, 'password'),
    'proxy_authentication_require_https': (('true', 'false'), 'true'),
    'http_proxy': (None, ''),
    'https_proxy': (None, ''),
    'no_proxy': (None, ''),
    'network.check_url': (None, 'https://www.gstatic.com/generate_204'),
    'network.check_status': (None, '204'),
    'network.timeout_seconds': (None, '4'),
    'shortcut.badgeText': (None, 'MITO'),
    'shortcut.badgeColor': (('negro', 'rojo', 'azul', 'naranja', 'rosa', 'verde', 'marron', 'morado'), 'naranja'),
}
PREFIXES = ('bucket.', 'proxy_authentication_form_field.', 'pocketool.')


def parse(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '!')) and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    return result


def validated(engine, pairs, profile=False):
    result = {}
    for key, value in pairs:
        key = key.strip()
        if not profile:
            value = value.strip()
        if not key or any(c in key for c in '=\r\n\0') or key.startswith(('#', '!')):
            raise YitoError(tr('Introduce una clave válida'))
        if any(c in value for c in '\r\n\0'):
            raise YitoError(tr('{p0}: el valor debe ocupar una línea', p0=(key)))
        if key in result:
            raise YitoError(tr('Clave repetida: {p1}', p1=(key)))
        result[key] = value
    env = result if profile else {key[4:]: value for key, value in result.items() if key.startswith('env.')}
    engine.validate_env(env)
    if profile:
        return result
    from .network import validate_url
    pocketool_names = set()
    for key, value in result.items():
        choices = PROPERTIES.get(key, (None, ''))[0]
        if choices and value not in choices:
            raise YitoError(tr('{p0}: elige uno de los valores permitidos', p0=(key)))
        if key in ('posthog.host', 'http_proxy', 'https_proxy', 'proxy_authentication_url', 'network.check_url', 'mito.update_url'):
            if value or key in ('network.check_url', 'mito.update_url'):
                validate_url(value)
        if key == 'network.timeout_seconds':
            try:
                number = float(value)
                if not math.isfinite(number) or not 0 < number <= 60:
                    raise ValueError()
            except ValueError:
                raise YitoError(tr('{p0}: introduce un número mayor que 0 y hasta 60', p0=(key))) from None
        if key == 'network.check_status':
            if not value.isdecimal() or not 100 <= int(value) <= 599:
                raise YitoError(tr('{p0}: introduce un código HTTP entre 100 y 599', p0=(key)))
        if key == 'shortcut.badgeText':
            from .shortcuts import badge_text
            badge_text(engine, value)
        if key.startswith('bucket.'):
            identifier(key[7:])
            if not value:
                raise YitoError(tr('{p0}: introduce una fuente de catálogo', p0=(key)))
        if key.startswith('proxy_authentication_form_field.') and not key[len(PREFIXES[1]):]:
            raise YitoError(tr('Falta el nombre del campo del formulario'))
        if key.startswith('pocketool.'):
            from .pocketools import validate_source
            name = identifier(key[len('pocketool.'):])
            if name in pocketool_names:
                raise YitoError(tr('Pocketool repetida: {p1}', p1=(name)))
            pocketool_names.add(name)
            validate_source(value)
            if value.startswith('pocketools/') and value != 'pocketools/' + name:
                raise YitoError(tr('La carpeta local debe coincidir con el nombre del paquete.'))
    return result


class Configuration:
    def __init__(self, engine, profile=None):
        self.engine, self.profile = engine, profile
        self.path = engine.root/'profiles'/f'{identifier(profile)}.json' if profile else engine.root/'config.properties'
        self.original = self.path.read_bytes() if self.path.exists() else None
        self.values = dict(engine.profile(profile)['env']) if profile else parse((self.original or b'').decode('utf-8-sig'))

    def save(self, pairs):
        values = validated(self.engine, pairs, bool(self.profile))
        with mutation(self.engine.root):
            current = self.path.read_bytes() if self.path.exists() else None
            if current != self.original:
                raise YitoError(tr('La configuración cambió fuera del diálogo. Ciérralo y vuelve a abrirlo.'))
            if self.profile:
                data = self.engine.profile(self.profile)
                data['env'] = values
                self.engine.save_profile(self.profile, data)
            else:
                # Keep comments, blank lines and unknown lines; rewrite active keys once.
                pending = dict(values)
                lines = []
                for line in (self.original or b'').decode('utf-8-sig').splitlines():
                    parsed = parse(line)
                    if not parsed:
                        lines.append(line)
                    else:
                        key = next(iter(parsed))
                        if key in pending:
                            lines.append(f'{key}={pending.pop(key)}')
                lines.extend(f'{key}={value}' for key, value in pending.items())
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.path.parent, mode='w', encoding='utf-8', newline='\n', delete=False) as stream:
                        temporary = stream.name
                        stream.write('\n'.join(lines) + '\n')
                    os.replace(temporary, self.path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
        self.original = self.path.read_bytes()
        self.values = values
