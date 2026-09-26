from __future__ import annotations
from .i18n import tr

import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile
import tempfile
from urllib.parse import urlsplit

from .storage import YitoError, identifier, command_name, portable_path, read_json, write_json, mutation
from .transfer import download, download_hashes, download_filename, MAX_EXPANDED


def array(value):
    return [] if value is None else value if isinstance(value, list) else [value]


def effective(manifest):
    result = dict(manifest)
    if 'architecture' in result:
        if '64bit' not in result['architecture']:
            raise YitoError(tr('No hay versión Windows x64'))
        result.update(result['architecture']['64bit'])
    return result


def fingerprint(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def installer_definition(manifest):
    """Validate explicit inline-script or local PowerShell-file installation."""
    installer = manifest.get('installer')
    if installer is None or installer == {}:
        return None
    if not isinstance(installer, dict):
        raise YitoError(tr('Pasos no soportados: ') + 'installer')
    allowed = {'file', 'args'} if 'file' in installer else {'script'}
    unsupported = sorted(set(installer) - allowed)
    if unsupported:
        raise YitoError(tr('Pasos no soportados: ') + ', '.join('installer.' + name for name in unsupported))
    if 'file' in installer:
        file = installer['file']
        if not isinstance(file, str) or Path(file).suffix.lower() != '.ps1':
            raise YitoError(tr('Pasos no soportados: ') + 'installer.file')
        portable_path(file)
        args = installer.get('args')
        if args is not None and not isinstance(args, str) and not (isinstance(args, list) and all(isinstance(arg, str) for arg in args)):
            raise YitoError(tr('{hook} debe ser texto o una lista de líneas', hook='installer.args'))
        return installer
    script = installer.get('script')
    if not isinstance(script, str) and not (isinstance(script, list) and all(isinstance(line, str) for line in script)):
        raise YitoError(tr('{hook} debe ser texto o una lista de líneas', hook='installer.script'))
    return installer


class Catalog:
    def __init__(self, root):
        self.root = root
        self.folder = root / 'core/buckets'

    def sources(self):
        result = {}
        config = self.root / 'config.properties'
        if config.exists():
            for line in config.read_text(encoding='utf-8-sig').splitlines():
                if line.startswith('bucket.') and '=' in line:
                    key, value = line.split('=', 1)
                    result[identifier(key[7:].strip())] = value.strip()
        return result

    def import_bucket(self, name, source):
        name = identifier(name)
        self.folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.folder) as tmp:
            stage = Path(tmp)
            if source.startswith(('http://', 'https://')):
                match = re.fullmatch(r'https?://github.com/([\w-]+)/([\w.-]+)', source.rstrip('/'))
                if not match:
                    raise YitoError(tr('La fuente debe ser un repositorio GitHub HTTPS o una carpeta local'))
                archive = stage / 'bucket.zip'
                download(f'https://api.github.com/repos/{match[1]}/{match[2]}/zipball', archive, root=self.root)
                manifests = self._archive_manifests(archive)
            else:
                source_path = Path(source)
                bucket = source_path / 'bucket' if (source_path / 'bucket').is_dir() else source_path
                manifests = {}
                for path in sorted(bucket.glob('*.json')):
                    manifests[identifier(path.stem)] = read_json(path)
            if not manifests:
                raise YitoError(tr('No hay manifiestos en {p1}', p1=(source)))
            # One atomic index per source, including original manifest JSON.
            write_json(self.folder / f'{name}.json', {'schema': 1, 'source': source, 'apps': manifests})
        return len(manifests)

    @staticmethod
    def _archive_manifests(archive):
        # Validate the whole archive, but never materialize repository files.
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if len(entries) > 150000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise YitoError(tr('Archivo demasiado grande para extraer'))
            seen, files, roots = set(), [], set()
            for entry in entries:
                path = portable_path(entry.filename)
                key = str(path).casefold()
                if key in seen:
                    raise YitoError(tr('Entrada duplicada: {p1}', p1=entry.filename))
                seen.add(key)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise YitoError(tr('Los enlaces del archivo no están permitidos'))
                if not path.parts:
                    raise YitoError(tr('Ruta no portable: {p1}', p1=entry.filename))
                roots.add(path.parts[0])
                files.append((path, entry))
            if len(roots) != 1:
                raise YitoError(tr('Ruta fuera de su raíz: {p1}', p1=str(archive)))
            nested = any(len(p.parts) >= 2 and p.parts[1].casefold() == 'bucket'
                         and (len(p.parts) > 2 or e.is_dir()) for p, e in files)
            manifests = {}
            for path, entry in sorted(files, key=lambda item: str(item[0])):
                if entry.is_dir() or path.suffix.lower() != '.json':
                    continue
                if len(path.parts) != (3 if nested else 2):
                    continue
                if nested and path.parts[1].casefold() != 'bucket':
                    continue
                name = identifier(path.stem)
                manifests[name] = json.loads(source.read(entry).decode('utf-8-sig'))
            return manifests

    def all(self):
        for path in sorted(self.folder.glob('*.json')):
            for name, manifest in read_json(path)['apps'].items():
                yield f'{path.stem}/{name}', manifest

    def refresh_installed(self, inventory, log=print):
        """Refresh only installed keys; payload manifests remain immutable."""
        return self.refresh_keys((record['key'] for record in inventory.values()), log)

    def refresh_keys(self, keys, log=print):
        """Refresh explicit component keys without synchronizing entire buckets."""
        sources = self.sources()
        refreshed = 0
        for key in sorted(set(keys)):
            try:
                bucket, app = map(identifier, key.split('/'))
                index_path = self.folder / f'{bucket}.json'
                index = read_json(index_path) if index_path.exists() else {'schema': 1, 'apps': {}}
                source = sources.get(bucket) or index.get('source')
                if not source:
                    raise YitoError(tr('No hay origen registrado para el catálogo'))
                if source.startswith(('http://', 'https://')):
                    match = re.fullmatch(r'https?://github.com/([\w-]+)/([\w.-]+)', source.rstrip('/'))
                    if not match:
                        raise YitoError(tr('La fuente debe ser un repositorio GitHub o una carpeta local'))
                    from urllib.error import HTTPError
                    with tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / 'manifest.json'
                        base = f'https://raw.githubusercontent.com/{match[1]}/{match[2]}/HEAD'
                        try:
                            download(f'{base}/bucket/{app}.json', target, root=self.root)
                        except HTTPError as exc:
                            if exc.code != 404:
                                raise
                            download(f'{base}/{app}.json', target, root=self.root)
                        manifest = read_json(target)
                else:
                    folder = Path(source)
                    folder = folder / 'bucket' if (folder / 'bucket').is_dir() else folder
                    manifest = read_json(folder / f'{app}.json')
                if not isinstance(manifest, dict) or not isinstance(manifest.get('version'), (str, int, float)):
                    raise YitoError(tr('Manifiesto sin versión válida'))
                identifier(str(manifest['version']))
                # Merge under the same lock as manual refresh/install, without
                # holding it during network IO or replacing unrelated entries.
                with mutation(self.root):
                    index = read_json(index_path) if index_path.exists() else {'schema': 1, 'source': source, 'apps': {}}
                    index['apps'][app] = manifest
                    write_json(index_path, index)
                refreshed += 1
            except Exception as exc:
                from .error_tracking import capture
                capture(self.root, exc, operation='catalog.refresh-installed', component=key)
                log(tr('No se pudo comprobar {p1}: {p3}. Se conserva el catálogo anterior.', p1=(key), p3=(exc)))
        return refreshed

    def resolve(self, name):
        matches = [(key, value) for key, value in self.all() if key == name or ('/' not in name and key.split('/')[1] == name)]
        if len(matches) != 1:
            raise YitoError(tr('Componente no encontrado o ambiguo: {p1}. Usa bucket/app.', p1=(name)))
        return matches[0]

    def compatibility(self, key, manifest):
        try:
            m = effective(manifest)
            from .bucket_resources import required_buckets
            required_buckets(key, m)
            script = m.get('pre_install')
            if script is not None and not isinstance(script, str) and not (isinstance(script, list) and all(isinstance(line, str) for line in script)):
                return tr('pre_install debe ser texto o una lista de líneas')
            identifier(str(m['version']))
            try:
                installer_definition(m)
            except YitoError as exc:
                return str(exc)
            if 'innosetup' in m and not isinstance(m['innosetup'], bool):
                return tr('Pasos no soportados: ') + 'innosetup'
            from .persistence import definitions
            persisted = []
            for source, destination in definitions(m):
                portable_path(source)
                portable_path(destination)
                if source in ('.', '') or destination in ('.', ''):
                    return tr('persist requiere una ruta concreta')
                from pathlib import PureWindowsPath
                current = PureWindowsPath(source)
                if any(current == p or p in current.parents or current in p.parents for p in persisted):
                    return tr('persist contiene rutas solapadas')
                persisted.append(current)
            urls = array(m.get('url'))
            if not urls:
                return tr('Falta URL de descarga')
            download_hashes(urls, m.get('hash'))
            for url in urls:
                if urlsplit(url).scheme not in ('http', 'https'):
                    return tr('Requiere HTTP o HTTPS')
                filename = download_filename(url).lower()
                if Path(filename).suffix and not filename.endswith(('.zip', '.7z', '.rar', '.tar', '.tar.gz', '.tgz', '.tar.xz', '.tar.bz2', '.nupkg', '.msi', '.exe', '.dll', '.jar', '.ico', '.ps1', '.cmd', '.bat', '.com')):
                    return tr('Formato de archivo todavía no soportado')
            for shortcut in m.get('shortcuts', []):
                if isinstance(shortcut, list) and len(shortcut) > 3 and shortcut[3]:
                    icon = shortcut[3]
                    if not isinstance(icon, str) or Path(icon).suffix.lower() not in ('.ico', '.exe', '.dll'):
                        return tr('Formato de icono no soportado para el acceso directo: {p1}', p1=icon)
                    portable_path(icon)
            for field in ('extract_dir', 'extract_to', 'env_add_path'):
                for value in array(m.get(field)):
                    if value is not None:
                        portable_path(value)
            if m.get('psmodule'):
                return tr('Pasos no soportados: psmodule')
            for item in array(m.get('bin')):
                if isinstance(item, list):
                    if len(item) not in (2, 3):
                        return tr('bin debe tener ejecutable, alias y argumentos opcionales')
                    executable, alias = item[:2]
                else:
                    executable, alias = item, Path(item.replace('\\', '/')).stem
                portable_path(executable)
                command_name(alias)
            for env, value in m.get('env_set', {}).items():
                if env.upper() in {'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'PATH', 'COMSPEC', 'SYSTEMROOT', 'WINDIR', 'HOMEDRIVE', 'HOMEPATH', 'MITO_ROOT', 'MITO_PROFILE'} or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env):
                    return tr('Variable reservada: {name}', name=env)
                if '$' in value.replace('${dir}', '').replace('$dir', '').replace('${persist_dir}', '').replace('$persist_dir', '') or '%' in value:
                    return tr('Variable no soportada: {name}', name=env)
            return 'Compatible'
        except (KeyError, TypeError, ValueError, YitoError) as exc:
            return tr('Manifiesto no soportado: {error}', error=exc)
