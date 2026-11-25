from __future__ import annotations
from .i18n import tr

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

from .catalog import Catalog, array, effective, fingerprint
from .storage import YitoError, identifier, command_name, inside, mutation, read_json, write_json
from .transfer import download, extract_zip, download_hashes
from .diagnostics import step
from . import operation


def move_whole(source, target, *, wait=0.0, sleep=time.sleep, clock=time.monotonic, outcome=None):
    """Move an extracted tree with one rename when nothing has to be merged.

    Per-file renames of freshly extracted binaries wait for the antivirus one by
    one (~200 ms each measured); a single directory rename avoids that. Right
    after extraction the antivirus still holds the new files and Windows denies
    the directory rename, so it is retried for up to `wait` seconds before
    falling back. Any existing content in target, or a rename that keeps
    failing, keeps the per-file merge. `outcome` receives attempts and reason."""
    info = {'attempts': 0, 'reason': ''}
    try:
        if target.exists():
            if not target.is_dir() or target.is_junction() or any(target.iterdir()):
                info['reason'] = 'target-not-empty'
                return False
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        deadline, delay = clock() + wait, .25
        while True:
            info['attempts'] += 1
            try:
                os.replace(source, target)
                return True
            except PermissionError as exc:
                info['reason'] = f'winerror-{getattr(exc, "winerror", None) or exc.errno}'
                if clock() + delay > deadline:
                    raise
                sleep(delay)
                delay = min(delay * 2, 2.0)
    except OSError as exc:
        info['reason'] = info['reason'] or type(exc).__name__
        target.mkdir(parents=True, exist_ok=True)
        return False
    finally:
        if outcome is not None:
            outcome.update(info)


class LongStep(str):
    """Log line that also brackets a step with no progress to report.

    Plain consumers just print it; an interface may show the active line
    prominently and hide it at the matching inactive one."""
    def __new__(cls, text, active):
        line = super().__new__(cls, text)
        line.active = active
        return line


class Engine:
    def __init__(self, root):
        self.root = root.resolve()
        self.catalog = Catalog(self.root)

    # Las carpetas conocidas de Windows suelen declararse como %USERPROFILE%\<nombre>.
    # Con USERPROFILE en home, SHGetKnownFolderPath falla si no existen (p. ej. Electron
    # aborta con "Failed to get 'downloads' path"). Se crean vacías, sin excepciones por app.
    USER_FOLDERS = ('Desktop', 'Documents', 'Downloads', 'Music', 'Pictures', 'Videos',
                    'Favorites', 'Links', 'Contacts', 'Searches', 'Saved Games', 'AppData/LocalLow')

    def initialize(self):
        for relative in ('home', 'home/AppData/Roaming', 'home/AppData/Local', 'home/tmp', 'home/custom-commands', 'profiles', 'workspaces', 'core/cache', 'core/staging', 'core/state', 'core/apps'):
            inside(self.root, relative).mkdir(parents=True, exist_ok=True)
        for relative in self.USER_FOLDERS:
            inside(self.root / 'home', relative).mkdir(parents=True, exist_ok=True)

    def profiles(self):
        return sorted(p.stem for p in (self.root / 'profiles').glob('*.json'))

    def profile(self, name):
        path = self.root / 'profiles' / f'{identifier(name)}.json'
        if not path.exists():
            raise YitoError(tr('No existe el profile {p1}', p1=(name)))
        result = read_json(path)
        if not isinstance(result.get('apps'), list) or any(not isinstance(app, str) for app in result['apps']):
            raise YitoError(tr('apps debe ser una lista de identificadores de instalación'))
        if len(set(result['apps'])) != len(result['apps']):
            raise YitoError(tr('apps contiene instalaciones repetidas'))
        result['env'] = self.validate_env(result.get('env', {}))
        inside(self.root / 'workspaces', result['workspace'])
        return result

    @staticmethod
    def validate_env(overrides):
        if not isinstance(overrides, dict):
            raise YitoError(tr('env debe ser un objeto de variables de entorno'))
        reserved = {'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'HOMEDRIVE', 'HOMEPATH'}
        seen = set()
        for key, value in overrides.items():
            if not key or '=' in key or ';' in key or '\0' in key or not isinstance(value, str) or '\0' in value:
                raise YitoError(tr('Variable de entorno no válida: ') + key)
            upper = key.upper()
            if upper in reserved or upper.startswith('MITO_'):
                raise YitoError(tr('Variable reservada por MITO: ') + key)
            if upper in seen:
                raise YitoError(tr('Variable de entorno repetida: ') + key)
            seen.add(upper)
        return overrides

    def global_env(self):
        config = self.root / 'config.properties'
        values = {}
        if config.exists():
            for line in config.read_text(encoding='utf-8-sig').splitlines():
                line = line.strip()
                if not line.startswith('env.'):
                    continue
                key, separator, value = line[4:].partition('=')
                key = key.strip()
                if not separator:
                    raise YitoError(tr('Variable global sin =: ') + key)
                if key in values:
                    raise YitoError(tr('Variable de entorno repetida: ') + key)
                values[key] = value.strip()
        return self.validate_env(values)

    @staticmethod
    def install_id(key, lock):
        return key

    def installed(self):
        """Read installation metadata independently of profiles and catalogs."""
        result = {}
        if any((self.root / 'core/apps').glob('*/*/*/.mito.json')):
            raise YitoError(tr('Almacenamiento antiguo: cierra las aplicaciones y ejecuta mito.cmd migrate-storage'))
        for marker in sorted((self.root / 'core/apps').glob('*/*/.mito.json')):
            metadata = read_json(marker)
            lock = dict(metadata['record'], path=marker.parent.relative_to(self.root).as_posix())
            if lock['path'] != 'core/apps/' + lock['key'] or lock['key'] in result:
                raise YitoError(tr('Identidad o ruta de componente incoherente'))
            result[self.install_id(lock['key'], lock)] = lock
        return result

    def selected(self, data):
        installed = self.installed()
        missing = [app for app in data['apps'] if app not in installed]
        if missing:
            raise YitoError(tr('Falta la instalación: ') + ', '.join(missing))
        return {app: installed[app] for app in data['apps']}

    def set_active(self, profile, install_id, active):
        with mutation(self.root):
            data = self.profile(profile)
            installed = self.installed()
            if active:
                if install_id not in installed:
                    raise YitoError(tr('El componente no está instalado'))
                if install_id not in data['apps']:
                    data['apps'].append(install_id)
            else:
                data['apps'] = [app for app in data['apps'] if app != install_id]
            self.save_profile(profile, data)
            from .terminal import prepare
            prepare(self)

    def save_profile(self, name, data):
        write_json(self.root / 'profiles' / f'{identifier(name)}.json', data)

    def create_profile(self, name, workspace=None):
        name = identifier(name)
        workspace = identifier(workspace or name)
        with mutation(self.root):
            if name in self.profiles():
                raise YitoError(tr('El profile {p1} ya existe', p1=(name)))
            self.initialize()
            (self.root / 'workspaces' / workspace).mkdir(exist_ok=True)
            self.save_profile(name, {'workspace': workspace, 'apps': [], 'env': {}})
            from .terminal import prepare
            prepare(self)
        return name

    def ensure_default_profile(self):
        """Una instancia sin profiles (checkout del fuente o profiles/ vacío) recibe
        el mismo profile default vacío que incluye el ZIP de release."""
        if self.profiles():
            return None
        try:
            return self.create_profile('default')
        except YitoError:
            # Otro proceso lo creó entre la comprobación y el lock.
            if self.profiles():
                return None
            raise

    def delete_profile(self, name):
        name = identifier(name)
        with mutation(self.root):
            data = self.profile(name)
            if len(self.profiles()) <= 1:
                raise YitoError(tr('No se puede eliminar el último profile'))
            write_json(self.root / 'core/state/deleted-profiles' / f'{name}.json', data)
            inside(self.root / 'profiles', f'{name}.json').unlink()
            from .open_with import unregister
            # «Abrir con» would otherwise launch a profile that no longer exists.
            unregister(self.root, lambda record: record.get('profile') == name)
            from .terminal import prepare
            prepare(self)
        return self.profiles()[0]

    def installation_plan(self, requested, profile=None, log=lambda message: None, manifests=None, pins=None):
        with mutation(self.root):
            return self._installation_plan(requested, profile, log, manifests, pins)

    def _installation_plan(self, requested, profile, log, manifests=None, pins=None):
        """manifests sustituye el manifiesto del catálogo para claves concretas (versión
        elegida); pins marca esas claves como retenidas. Una versión ya retenida conserva
        su manifiesto instalado aunque el catálogo avance, también como dependencia."""
        self.initialize()
        data = self.profile(profile) if profile else {'apps': []}
        records = {}
        plan = []
        visiting, visited = set(), set()
        current = self.installed()
        manifests, pins = manifests or {}, pins or {}
        retained = {}

        def resolve(name):
            key, manifest = self.catalog.resolve(name)
            if key in manifests:
                manifest = manifests[key]
                if key in pins:
                    retained[key] = pins[key]
            elif key in current and current[key].get('pinned'):
                manifest = current[key]['manifest']
                retained[key] = current[key]['pinned']
            if key in visiting:
                raise YitoError(tr('Dependencia circular: {p1}', p1=(key)))
            if key in visited:
                return
            for hook in ('post_install',):
                if effective(manifest).get(hook):
                    log(tr('{p0}: {p2} omitido por la política general de MITO', p0=(key), p2=(hook)))
            status = self.catalog.compatibility(key, manifest)
            if status != 'Compatible':
                raise YitoError(f'{key}: {status}')
            visiting.add(key)
            for dependency in array(effective(manifest).get('depends')):
                resolve(dependency)
            visiting.remove(key)
            visited.add(key)
            plan.append((key, manifest))

        for request in ([requested] if isinstance(requested, str) else requested):
            resolve(request)
        # Resolve and validate the full dependency graph before downloading.
        installs = []
        for key, manifest in plan:
            digest = fingerprint(manifest)
            bucket, app = key.split('/')
            relative = f'core/apps/{identifier(bucket)}/{identifier(app)}'
            destination = inside(self.root, relative)
            record = {'key': key, 'version': manifest['version'], 'manifest_sha256': digest, 'path': relative, 'manifest': manifest}
            if key in retained:
                record['pinned'] = retained[key]
            installation = self.install_id(key, record)
            records[installation] = record
            if installation not in data['apps']:
                data['apps'].append(installation)
            installs.append((key, manifest, destination))
        from .components import validate_update_layout
        for key, manifest, destination in installs:
            if key in current:
                validate_update_layout(current[key], manifest)
        unverified = []
        resource_sources = {}
        for key, manifest, destination in installs:
            if key in current and current[key]['manifest_sha256'] == fingerprint(manifest):
                continue
            m = effective(manifest)
            from .bucket_resources import sources as hook_sources
            resource_sources[key] = hook_sources(self.catalog, key, m)
            unverified.extend(f'{key} · recursos del catálogo {name}'
                              for name, source in resource_sources[key].items()
                              if source.startswith(('http://', 'https://')))
            hashes = download_hashes(array(m['url']), m.get('hash'))
            unverified.extend(f'{key} {manifest["version"]} · descarga {index + 1}'
                              for index, expected in enumerate(hashes) if expected is None)
        return {'records': records, 'current': current, 'data': data, 'unverified': unverified,
                'resource_sources': resource_sources,
                'profiles': {name: self.profile(name) for name in self.profiles()}}

    def install(self, requested, profile=None, log=print, confirm_unverified=None, confirm_plan=None, manifests=None, pins=None):
        prepared = self.installation_plan(requested, profile, log, manifests, pins)
        if confirm_plan is not None and not confirm_plan(prepared['records']):
            raise YitoError(tr('Importación cancelada'))
        if prepared['unverified'] and (confirm_unverified is None or not confirm_unverified(prepared['unverified'])):
            raise YitoError(tr('Instalación cancelada: descargas sin hash no confirmadas'))
        with mutation(self.root):
            current = self._installation_plan(requested, profile, lambda message: None, manifests, pins)
            if current != prepared:
                raise YitoError('STATE_CONFLICT: installation plan changed; retry and review again')
            from .components import publish_plan
            publish_plan(self, current['records'], current['current'], profile, current['data'], log)
            if profile:
                log(tr('Profile {p1} preparado', p1=profile))
            else:
                log(tr('Componente instalado. Puedes activarlo en Inicio.'))
            return current['records']

    def _install_payload(self, key, manifest, destination, log):
        m = effective(manifest)
        with tempfile.TemporaryDirectory(dir=self.root / 'core/staging') as tmp:
            stage = Path(tmp)
            payload = stage / 'payload'
            payload.mkdir()
            urls = array(m['url'])
            hashes = download_hashes(urls, m.get('hash'))
            for index, (url, expected) in enumerate(zip(urls, hashes)):
                if expected is None:
                    log(tr('{p0}: descarga sin hash declarado ({p2}/{p4})', p0=(key), p2=(index + 1), p4=(len(urls))))
                log(tr('Descargando {p1} {p3} ({p5}/{p7})', p1=(key), p3=(manifest['version']), p5=(index + 1), p7=(len(urls))))
                operation.phase(tr('DESCARGANDO'), tr('Descargando {p1} {p3} ({p5}/{p7})', p1=(key), p3=(manifest['version']), p5=(index + 1), p7=(len(urls))))
                archive = stage / f'download-{index}'
                with step(self.root, 'install', 'download-and-hash', key):
                    download(url, archive, expected, root=self.root)
                operation.checkpoint()
                expanded = stage / f'expanded-{index}'
                from .archives import extract
                from .inno import APP, inno_root
                from .transfer import download_filename
                filename = download_filename(url)
                def positional(field):
                    value = m.get(field)
                    if isinstance(value, list):
                        if len(value) != len(urls):
                            raise YitoError(tr('{p0}: cantidad distinta de URL', p0=(field)))
                        return value[index] or '.'
                    return value or '.'
                extract_dir = positional('extract_dir')
                # Scoop: with innosetup, a bare Inno constant in extract_dir selects
                # the installer root to extract (innounp -c) instead of a subfolder.
                selected = inno_root(extract_dir) if m.get('innosetup') else None
                operation.phase(tr('PREPARANDO ARCHIVOS'), tr('Extrayendo {p1}…', p1=key))
                with step(self.root, 'install', 'extract', key):
                    extracted = extract(archive, expanded, self.root, filename, log=log, innosetup=m.get('innosetup', False),
                                        inno_root=selected or APP)
                operation.checkpoint()
                if selected and extracted:
                    extract_dir = '.'
                source = inside(expanded, extract_dir if extracted else '.')
                target = inside(payload, positional('extract_to'))
                if not source.is_dir():
                    raise YitoError(tr('extract_dir no encontrado: {p1}', p1=(source.name)))
                log(tr('Preparando {p1}', p1=key))
                total = 0
                for path in source.rglob('*'):
                    if path.is_file():
                        total += 1
                        if (target / path.relative_to(source)).exists():
                            raise YitoError(tr('Dos artefactos intentan escribir el mismo archivo'))
                copied = 0
                def copy_file(source_file, target_file):
                    nonlocal copied
                    # expanded-N es temporal y está en el mismo staging: mover evita
                    # reescribir cada archivo y que el antivirus vuelva a escanearlo
                    # antes de permitir publicar la carpeta.
                    try:
                        os.replace(source_file, target_file)
                        result = target_file
                    except OSError:
                        result = shutil.copy2(source_file, target_file)
                    copied += 1
                    if copied % 1000 == 0 and copied < total:
                        log(tr('Archivos {p1}: {p2}/{p3}', p1=key, p2=copied, p3=total))
                    if copied % 50 == 0 or copied == total:
                        operation.transfer(copied, total, unit='files')
                        operation.checkpoint()
                    return result
                with step(self.root, 'install', 'copy-payload', key):
                    # Moving freshly extracted files can block for minutes on
                    # hosts that scan new files; announce it so it never looks hung.
                    moving = tr('Moviendo {p1} archivos de {p2} a su carpeta. Con muchos archivos este paso puede tardar varios minutos; MITO sigue trabajando.',
                                p1=total, p2=key)
                    operation.phase(tr('PREPARANDO ARCHIVOS'), moving)
                    log(LongStep(moving, active=True))
                    try:
                        # Waiting for the antivirus pays off while it costs less than
                        # the per-file fallback (~0.2 s per file), capped at 30 s.
                        outcome = {}
                        moved = move_whole(source, target, wait=min(30.0, 0.15 * total), outcome=outcome)
                        from .diagnostics import write
                        write(self.root, 'info', 'install.move-whole', 'moved' if moved else 'per-file',
                              component=key, files=total, **outcome)
                        if moved:
                            copied = total
                        else:
                            shutil.copytree(source, target, dirs_exist_ok=True, copy_function=copy_file)
                    finally:
                        log(LongStep(tr('Archivos {p1}: {p2}/{p3}', p1=key, p2=copied, p3=total), active=False))
                operation.checkpoint()
            if m.get('innosetup'):
                from .inno import restore_declared_targets
                restore_declared_targets(payload, [s[0] for s in m.get('shortcuts', [])] +
                                         [path for path, _, _ in self.bins(m)])
            from .hooks import run_pre_install, run_installer
            from .bucket_resources import sources as hook_sources, prepare as prepare_resources
            operation.phase(tr('PREPARANDO ARCHIVOS'), tr('Preparando {p1}', p1=key))
            prepare_resources(self.root, hook_sources(self.catalog, key, m), stage / 'hook-buckets', log)
            operation.checkpoint()
            with step(self.root, 'install', 'pre-install', key):
                run_pre_install(self.root, key, m, payload, log)
            operation.checkpoint()
            with step(self.root, 'install', 'installer', key):
                run_installer(self.root, key, m, payload, log)
            operation.checkpoint()
            log(tr('Validando {p1}', p1=key))
            # Validate command targets before publishing the payload.
            with step(self.root, 'install', 'validate', key):
                for executable, alias, args in self.bins(m):
                    if not inside(payload, executable).is_file():
                        raise YitoError(tr('Comando ausente: {p1}', p1=(executable)))
            record = {'key': key, 'version': manifest['version'], 'manifest_sha256': fingerprint(manifest), 'path': destination.relative_to(self.root).as_posix(), 'manifest': manifest}
            write_json(payload / '.mito.json', {'key': key, 'manifest_sha256': fingerprint(manifest), 'record': record})
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise YitoError(tr('Instalación incompleta existente: {p1}', p1=(destination)))
            # Windows puede retener el renombrado hasta que el antivirus termine de
            # analizar los archivos recién escritos; se indica para que no parezca colgado.
            log(tr('Publicando {p1}; el antivirus puede retrasar este paso con muchos archivos', p1=key))
            with step(self.root, 'install', 'publish', key):
                payload.rename(destination)
            log(tr('Instalado {p1} {p3}', p1=(key), p3=(manifest['version'])))

    @staticmethod
    def bins(manifest):
        from .arguments import split
        for item in array(manifest.get('bin')):
            if isinstance(item, list):
                executable, alias = item[:2]
                args = split(item[2]) if len(item) > 2 else []
            else:
                executable, alias, args = item, Path(item.replace('\\', '/')).stem, []
            command_name(alias)
            yield executable, alias, args

    def component_path(self, payload, relative):
        # Persist links may resolve under home instead of the immutable payload.
        from pathlib import PureWindowsPath
        inside(Path('C:/__mito_validation__'), relative)
        return inside(self.root, (payload.relative_to(self.root) / Path(*PureWindowsPath(relative).parts)).as_posix())

    def component_environment(self, lock, profile):
        """Inspect one component's contributions without preparing or activating it."""
        from .isolation import payload_path, persist_root
        payload = payload_path(self.root, lock, profile)
        manifest = effective(lock['manifest'])
        paths = [str(self.component_path(payload, value)) for value in array(manifest.get('env_add_path'))]
        paths.extend(str(self.component_path(payload, executable).parent)
                     for executable, _, _ in self.bins(manifest))
        persist_dir = str(persist_root(self.root, lock, profile))
        variables = {key: value.replace('${dir}', str(payload)).replace('$dir', str(payload))
                     .replace('${persist_dir}', persist_dir).replace('$persist_dir', persist_dir)
                     for key, value in manifest.get('env_set', {}).items()}
        return variables, list(dict.fromkeys(paths))

    def application_entries(self, lock):
        from .arguments import split
        m = effective(lock['manifest'])
        candidates = [(s[1], s[0], split(s[2]) if len(s) > 2 else []) for s in m.get('shortcuts', [])]
        candidates += [(alias, path, args) for path, alias, args in self.bins(m)
                       if not any(s[1] == path and s[2] == args for s in candidates)]
        entries = []
        for label, executable, args in candidates:
            target = self.component_path(inside(self.root, lock['path']), executable)
            if target.suffix.lower() in ('.exe', '.com', '.cmd', '.bat', '.ps1', '.jar', '.chm') and target.is_file():
                entries.append((label, executable, args))
        return entries

    def application_target(self, lock, shortcut=None):
        """Use only launch entries declared by the manifest, never scan runtimes."""
        entries = self.application_entries(lock)
        if shortcut is not None:
            entries = [entry for entry in entries if entry[0] == shortcut]
            if len(entries) != 1:
                raise YitoError(f'Shortcut unavailable or ambiguous: {shortcut}')
        else:
            entries.sort(key=lambda entry: Path(entry[1]).suffix.lower() == '.chm')
        if entries:
            return entries[0][1], entries[0][2]
        raise YitoError(tr('La app no declara un ejecutable disponible; úsala desde la terminal'))

    def application_name(self, lock, shortcut=None):
        """Return a safe, generic display name from the selected launch entry."""
        executable, _ = self.application_target(lock, shortcut)
        if shortcut is not None:
            return shortcut
        for shortcut in effective(lock['manifest']).get('shortcuts', []):
            if isinstance(shortcut, list) and shortcut and shortcut[0] == executable and len(shortcut) > 1:
                name = ' '.join(str(shortcut[1]).splitlines()).strip()
                if name:
                    return name
        return lock['key'].split('/')[-1]

    def can_open(self, lock):
        try:
            self.application_target(lock)
            return True
        except (YitoError, OSError, ValueError, TypeError, IndexError):
            return False

    def environment(self, profile):
        with mutation(self.root):
            return self._environment(profile)

    def context_variables(self, profile):
        """Read the managed paths without activating components or creating files."""
        profile = identifier(profile)
        data = self.profile(profile)
        home = self.root / 'home'
        return {
            'MITO_PROFILE': profile, 'MITO_ROOT': str(self.root),
            'MITO_WORKSPACE': str(inside(self.root / 'workspaces', data['workspace'])),
            'HOME': str(home), 'USERPROFILE': str(home),
            'APPDATA': str(home / 'AppData/Roaming'), 'LOCALAPPDATA': str(home / 'AppData/Local'),
            'TEMP': str(home / 'tmp'), 'TMP': str(home / 'tmp'),
            'HOMEDRIVE': home.drive, 'HOMEPATH': str(home)[len(home.drive):],
        }

    def execution_context(self, profile):
        """Build environment and cwd from one consistent profile snapshot."""
        with mutation(self.root):
            env = self._environment(profile)
            workspace = inside(self.root/'workspaces', self.profile(profile)['workspace'])
            workspace.mkdir(parents=True, exist_ok=True)
            return env, workspace

    def _environment(self, profile):
        self.initialize()
        data = self.profile(profile)
        from .network import proxy_environment
        env = dict(os.environ)
        home = self.root / 'home'
        # Discard activation from a parent MITO shell; profiles never stack.
        inherited_path = env.get('MITO_HOST_PATH', env.get('PATH', ''))
        for key in env.get('MITO_ACTIVE_VARS', '').split(';'):
            if key:
                env.pop(key, None)
        env = proxy_environment(self.root, env)
        env.update(self.context_variables(profile), MITO_HOST_PATH=inherited_path)
        paths = [str(home / 'custom-commands'), str(self.root / 'core/commands')]
        from .pocketools import paths as pocketool_paths
        paths.extend(pocketool_paths(self.root))
        variables = []
        for installation, lock in self.selected(data).items():
            key = lock['key']
            payload = inside(self.root, lock['path'])
            if not (payload / '.mito.json').is_file():
                raise YitoError(tr('Falta el componente {p1}; vuelve a instalarlo', p1=(key)))
            marker = read_json(payload / '.mito.json')
            if marker.get('key') != key or marker.get('manifest_sha256') != lock['manifest_sha256'] or fingerprint(lock['manifest']) != lock['manifest_sha256']:
                raise YitoError(tr('Lock o instalación incoherente: {p1}', p1=(key)))
            from .isolation import prepare_payload
            prepare_payload(self.root, lock, profile)
            component_vars, component_paths = self.component_environment(lock, profile)
            paths.extend(component_paths)
            env.update(component_vars)
            variables.extend(component_vars)
        overrides = {key.upper(): value for key, value in self.global_env().items()}
        overrides.update({key.upper(): value for key, value in data['env'].items()})
        for key, value in overrides.items():
            key = key.upper()
            for existing in list(env):
                if existing.upper() == key:
                    del env[existing]
            env[key] = value
            variables.append(key)
        env['MITO_ACTIVE_VARS'] = ';'.join(dict.fromkeys(variables))
        shim_dir = self.root / 'core/state/shims' / identifier(profile)
        self._write_shims(data, profile, shim_dir)
        env['PATH'] = os.pathsep.join([str(shim_dir), *dict.fromkeys(paths), inherited_path])
        return env

    def _write_shims(self, data, profile, folder):
        folder.mkdir(parents=True, exist_ok=True)
        # Remove only derived wrappers recorded for this profile, so deactivation
        # cannot leave a stale command on PATH.
        registry = folder / 'generated.json'
        previous = read_json(registry) if registry.exists() else []
        selected = self.selected(data)
        wanted = {command_name(alias) + '.cmd' for lock in selected.values() for _, alias, _ in self.bins(effective(lock['manifest']))}
        for filename in previous:
            if filename not in wanted:
                inside(folder, filename).unlink(missing_ok=True)
        for key, lock in selected.items():
            for executable, alias, _ in self.bins(effective(lock['manifest'])):
                quoted_alias = command_name(alias).replace('%', '%%')
                content = f'@echo off\nsetlocal DisableDelayedExpansion\n"%~dp0..\\..\\..\\..\\tools\\python\\python.exe" -B -I -X utf8 -m mito run --profile {identifier(profile)} --app {key} --bin="{quoted_alias}" -- %*\n'
                # shims live core/state/shims/profile; core/tools is three levels up.
                content = content.replace('..\\..\\..\\..\\tools', '..\\..\\..\\tools')
                path = folder / f'{command_name(alias)}.cmd'
                if not path.exists() or path.read_text() != content:
                    path.write_text(content, encoding='utf-8')
        write_json(registry, sorted(wanted))

    def launch(self, profile, app=None, binary=None, arguments=(), new_terminal=False,
               coupled=False, owned=False, shortcut=None):
        if shortcut is not None and binary:
            raise YitoError(tr('Modo de lanzamiento no válido'))
        data = self.profile(profile)
        env = self.environment(profile)
        workspace = inside(self.root / 'workspaces', data['workspace'])
        workspace.mkdir(parents=True, exist_ok=True)
        if app:
            selected = self.selected(data)
            matches = [key for key, lock in selected.items() if key == app or lock['key'] == app or lock['key'].split('/')[1] == app]
            if len(matches) != 1:
                raise YitoError(tr('{p0} no está seleccionado en {p2}', p0=(app), p2=(profile)))
            lock = selected[matches[0]]
            key = lock['key']
            m = effective(lock['manifest'])
            fixed_args = []
            if binary:
                bins = [b for b in self.bins(m) if b[1].lower() == binary.lower()]
                if len(bins) != 1:
                    raise YitoError(tr('Comando no encontrado: {p1}', p1=(binary)))
                executable = bins[0][0]
                fixed_args = bins[0][2]
            else:
                executable, fixed_args = self.application_target(lock, shortcut)
            from .isolation import payload_path, persist_root
            payload = payload_path(self.root, lock, profile)
            target = self.component_path(payload, executable)
            if not target.is_file():
                raise YitoError(tr('No existe {p1}', p1=(executable)))
            fixed_args = [a.replace('$dir', str(payload)).replace('$persist_dir', str(persist_root(self.root, lock, profile))) for a in fixed_args]
            command = [str(target), *fixed_args, *arguments]
            if target.suffix.lower() in ('.bat', '.cmd'):
                from .jobs import batch_command
                command = batch_command(target, [*fixed_args, *arguments], env)
            elif target.suffix.lower() == '.ps1':
                command = ['powershell.exe', '-NoLogo', '-NoProfile', '-File', str(target), *fixed_args, *arguments]
            elif target.suffix.lower() == '.chm':
                command = [str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'hh.exe'), str(target), *fixed_args, *arguments]
            elif target.suffix.lower() == '.jar':
                java = shutil.which('java.exe', path=env.get('PATH', ''))
                if java is None:
                    raise YitoError(tr('Comando no encontrado: {p1}', p1='java.exe'))
                command = [java, '-jar', str(target), *fixed_args, *arguments]
        else:
            from .terminal import launch
            return launch(self, profile)
        if coupled:
            if binary or new_terminal or owned:
                raise YitoError(tr('Modo de lanzamiento no válido'))
            from .terminal import launch_component
            return launch_component(self, profile, key, self.application_name(lock, shortcut), arguments, shortcut=shortcut)
        # Un comando de consola (shim de bin) trabaja donde se invoca, como cualquier
        # comando del PATH: `git init` en C:\proyecto debe actuar en C:\proyecto.
        # Aplicaciones y accesos directos se abren en el workspace del profile.
        cwd = workspace
        if binary:
            try:
                cwd = Path(os.getcwd())
            except OSError:
                cwd = workspace
        if owned:
            from .processes import ProcessJob
            group = ProcessJob()
            try:
                process = group.spawn(command, cwd=cwd, env=env, creationflags=0)
                result = process.wait()
                group.wait_empty()
                return result
            finally:
                group.close()
        if new_terminal:
            subprocess.Popen(command, cwd=cwd, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
            return 0
        if app and not binary:
            subprocess.Popen(command, cwd=cwd, env=env)
            return 0
        return subprocess.call(command, cwd=cwd, env=env)

    def shell(self, profile, arguments=(), shell_type='cmd'):
        env = self.environment(profile)
        workspace = inside(self.root / 'workspaces', self.profile(profile)['workspace'])
        workspace.mkdir(parents=True, exist_ok=True)
        if shell_type == 'powershell':
            executable = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
            shell_command = [executable, '-NoLogo', '-NoProfile', '-NoExit']
        elif shell_type == 'cmd':
            shell_command = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/k']
        else:
            raise YitoError(tr('Shell no soportada'))
        return subprocess.call(list(arguments) if arguments else shell_command, cwd=workspace, env=env)
