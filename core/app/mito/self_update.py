"""Explicit release updates, applied by a disposable runtime outside the instance."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from .release import detected_version
from .storage import YitoError, inside, mutation, read_json, write_json
from .transfer import download, extract_zip, hash_verifier, WebRedirect

LATEST = 'https://api.github.com/repos/danielgube/mito/releases/latest'
FIXED = ('mito.cmd', 'core/bootstrap.ps1', 'core/bootstrap.py',
         'core/bootstrap.lock.json', 'core/assets/branding/mito.ico', 'release.json')
OPTIONAL_ASSETS = ('core/assets/branding/pocketools.ico',)
MANAGED = (*FIXED, *OPTIONAL_ASSETS, 'core/app/mito')
MAX_RELEASE = 64 * 1024 * 1024


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise YitoError('Versión de release no válida')
    return tuple(map(int, value.split('.')))


def asset_url(tag, asset):
    # The configured release endpoint is the authority for download locations;
    # attachment/CDN paths need not resemble GitHub's repository paths.
    from .network import validate_url
    url = asset.get('browser_download_url', '')
    validate_url(url)
    return url


def check(root, *, force=False):
    local = check_offline(root)
    if local:
        return local
    current = detected_version(root)
    from .network import properties, validate_url
    from .update_checks import daily
    source = properties(root).get('mito.update_url', LATEST)
    validate_url(source)
    return daily(root, 'mito-update', {'source': source, 'current': current},
                 lambda: check_remote(root, current, source), force=force)


def check_remote(root, current, source):
    from .network import opener
    request = urllib.request.Request(source, headers={'User-Agent': 'MITO/' + current,
                                                     'Accept': 'application/json'})
    with opener(root, None, WebRedirect()).open(request, timeout=15) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise YitoError('Respuesta de actualizaciones demasiado grande')
    info = release_info(json.loads(data), current)
    if info and info.get('checksum_url'):
        request = urllib.request.Request(info['checksum_url'], headers={'User-Agent': 'MITO/' + current})
        with opener(root, None, WebRedirect()).open(request, timeout=15) as response:
            checksum = response.read(4097)
        if len(checksum) > 4096:
            raise YitoError('Archivo SHA-256 demasiado grande')
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?' + re.escape(info['name']) + r'\s*', checksum.decode('ascii'))
        if not match:
            raise YitoError('El archivo SHA-256 no corresponde al ZIP de MITO')
        info['hash'] = 'sha256:' + match[1].lower()
    return info


def check_offline(root):
    """Prefer the newest local release; no network access or package execution."""
    current = detected_version(root)
    folder = safe_target(root, 'update')
    candidates = []
    for archive in folder.glob('mito-*-windows-x64.zip'):
        match = re.fullmatch(r'mito-(\d+\.\d+\.\d+)-windows-x64\.zip', archive.name)
        if match and version(match[1]) > version(current):
            candidates.append((version(match[1]), archive, match[1]))
    if not candidates:
        return None
    _, archive, latest = max(candidates)
    archive = safe_target(root, 'update/' + archive.name)
    size = archive.stat().st_size
    if not 0 < size <= MAX_RELEASE:
        raise YitoError('Tamaño de release local no válido')
    import zipfile
    with zipfile.ZipFile(archive) as zipped:
        metadata = zipped.getinfo('release.json')
        if metadata.file_size > 4096:
            raise YitoError('Metadatos de release local demasiado grandes')
        if json.loads(zipped.read(metadata)) != {'version': latest, 'platform': 'windows-x64', 'kind': 'bootstrap'}:
            raise YitoError('El ZIP local no corresponde a la versión de su nombre')
    digest = None
    sidecar = safe_target(root, 'update/' + archive.name + '.sha256')
    if sidecar.exists():
        with sidecar.open('rb') as stream:
            checksum = stream.read(4097)
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?' + re.escape(archive.name) + r'\s*', checksum.decode('ascii'))
        if len(checksum) > 4096 or not match:
            raise YitoError('El archivo SHA-256 local no corresponde al ZIP de MITO')
        digest = 'sha256:' + match[1].lower()
    return {'current': current, 'version': latest, 'name': archive.name, 'size': size,
            'hash': digest, 'local_path': str(archive.resolve())}


def copy_offline(root, info, target):
    source = safe_target(root, 'update/' + info['name'])
    if source.resolve() != Path(info['local_path']):
        raise YitoError('La ruta del ZIP local ha cambiado')
    verifier, expected = hash_verifier(info['hash'])
    total = 0
    try:
        with source.open('rb') as inp, target.open('wb') as out:
            while chunk := inp.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_RELEASE or total > info['size']:
                    raise YitoError('El ZIP local ha cambiado o excede el límite')
                if verifier:
                    verifier.update(chunk)
                out.write(chunk)
        if total != info['size'] or (verifier and verifier.hexdigest() != expected):
            raise YitoError('Tamaño o hash incorrecto del ZIP local')
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def release_info(data, current):
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data['tag_name']
    latest = tag.removeprefix('v')
    if version(latest) <= version(current):
        return None
    name = f'mito-{latest}-windows-x64.zip'
    assets = [a for a in data['assets'] if a.get('name') == name]
    if len(assets) != 1:
        raise YitoError('La release no contiene un único ZIP Windows x64 de MITO')
    asset = assets[0]
    size = asset.get('size')
    if type(size) is not int or not 0 < size <= MAX_RELEASE:
        raise YitoError('Tamaño de release no válido')
    digest = asset.get('digest')
    verifier, _ = hash_verifier(digest)
    result = {'current': current, 'version': latest, 'url': asset_url(tag, asset),
              'name': name, 'size': size, 'hash': digest if verifier else None}
    if not verifier:
        checksums = [a for a in data['assets'] if a.get('name') == name + '.sha256']
        if len(checksums) > 1:
            raise YitoError('La release contiene varios archivos SHA-256 para el mismo ZIP')
        if checksums:
            result['checksum_url'] = asset_url(tag, checksums[0])
    return result


def require_distribution(root):
    if (root / '.git').exists():
        raise YitoError('Esta instancia es un checkout Git. Actualízala mediante Git para conservar los cambios de desarrollo.')


def validate_payload(payload, expected, *, prepared_tools=False):
    metadata = read_json(payload / 'release.json')
    if metadata != {'version': expected, 'platform': 'windows-x64', 'kind': 'bootstrap'}:
        raise YitoError('Metadatos de release incompatibles')
    if detected_version(payload) != expected:
        raise YitoError('La versión del código no coincide con la release')
    for relative in (*FIXED, 'core/app/mito/__main__.py'):
        if not inside(payload, relative).is_file():
            raise YitoError('Falta un archivo obligatorio: ' + relative)
    for file in payload.rglob('*'):
        relative = file.relative_to(payload).as_posix()
        if file.is_symlink() or file.is_junction():
            raise YitoError('La release contiene enlaces')
        if prepared_tools and (relative.startswith(('core/tools/', 'core/wheels/', 'logs/')) or relative == 'core/state/bootstrap-tools.json'):
            continue
        if file.is_file() and not (relative in (*FIXED, *OPTIONAL_ASSETS) or relative in ('config.properties', 'profiles/default.json')
                                  or (relative.startswith('core/app/mito/')
                                      and (relative.endswith('.py') or re.fullmatch(r'core/app/mito/locales/[^/]+\.json', relative)))):
            raise YitoError('Archivo ajeno a la distribución: ' + relative)
    # Bootstrap remains pinned, including every transitive wheel and tool artifact.
    lock = read_json(payload / 'core/bootstrap.lock.json')
    artifacts = [lock['python'], *lock['wheels']]
    artifacts += [a for tool in lock['tools'] for a in tool['artifacts']]
    for artifact in artifacts:
        if not re.fullmatch('[a-fA-F0-9]{64}', str(artifact.get('sha256', ''))):
            raise YitoError('El bootstrap de la release debe declarar todos sus SHA-256')
        if urllib.parse.urlsplit(artifact['url']).scheme not in ('http', 'https'):
            raise YitoError('URL de bootstrap no válida')


def host_environment(work):
    """No profile PATH, portable home, terminal identity or inherited app variables."""
    system = Path(os.environ['SystemRoot'])
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMPUTERNAME', 'USERNAME', 'USERDOMAIN', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}}
    env.update(PATH=str(system / 'System32') + os.pathsep + str(system),
               COMSPEC=str(system / 'System32/cmd.exe'))
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        env[key] = str(work)
    env['HOMEDRIVE'], env['HOMEPATH'] = os.path.splitdrive(str(work))
    return env


def snapshot_runner(root, work):
    runtime = work / 'runner'
    runtime.mkdir()
    source = root / 'core/tools/python'
    for file in source.iterdir():
        if file.is_file() and file.suffix.lower() in ('.exe', '.dll', '.pyd', '.zip'):
            shutil.copy2(file, runtime / file.name)
    stdlib = next(runtime.glob('python3*.zip'))
    pth = next(source.glob('python*._pth')).name
    (runtime / pth).write_text(stdlib.name + '\n.\n../app\n', encoding='ascii')
    shutil.copytree(Path(__file__).parent, work / 'app/mito',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def prepare(root, info, *, allow_unverified=False, log=lambda message: None):
    require_distribution(root)
    if detected_version(root) != info['current'] or version(info['version']) <= version(info['current']):
        raise YitoError('La versión instalada ha cambiado; vuelve a comprobar las actualizaciones')
    if not hash_verifier(info['hash'])[0] and not allow_unverified:
        raise YitoError('Se requiere confirmación previa para actualizar MITO sin hash')
    from .host_data import host_folder
    base = host_folder('cache') / 'Temp'
    base.mkdir(parents=True, exist_ok=True)
    if base.resolve().is_relative_to(root.resolve()):
        raise YitoError('El actualizador necesita una carpeta temporal fuera de MITO')
    work = Path(tempfile.mkdtemp(prefix='mito-update-', dir=base))
    # Keep failed preparation for diagnosis; never publish before all validation succeeds.
    offline = bool(info.get('local_path'))
    log(('Preparando ZIP local de MITO ' if offline else 'Descargando MITO ') + info['version'] + '…')
    archive = work / 'release.zip'
    if offline:
        copy_offline(root, info, archive)
    else:
        download(info['url'], archive, info['hash'], root=root, max_bytes=MAX_RELEASE)
    if archive.stat().st_size != info['size']:
        raise YitoError('El tamaño del ZIP no coincide con la release')
    import zipfile
    with zipfile.ZipFile(archive) as zipped:
        if len(zipped.infolist()) > 10000 or sum(i.file_size for i in zipped.infolist()) > MAX_RELEASE * 4:
            raise YitoError('El contenido de la release excede el límite')
    payload = work / 'payload'
    extract_zip(archive, payload)
    validate_payload(payload, info['version'])
    tools_changed = read_json(root / 'core/bootstrap.lock.json') != read_json(payload / 'core/bootstrap.lock.json')
    if offline and tools_changed:
        raise YitoError('Esta release necesita un bootstrap distinto, no incluido en el ZIP mínimo. La actualización offline no puede continuar; MITO permanece intacto.')
    snapshot_runner(root, work)
    if tools_changed:
        log('Preparando el nuevo bootstrap verificado fuera de la instancia…')
        shutil.copy2(root / 'config.properties', payload / 'config.properties')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        with (work / 'bootstrap.log').open('w', encoding='utf-8') as output:
            result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(payload / 'core/bootstrap.ps1'), '-Approved'], cwd=work,
                                    env=host_environment(work), stdout=output, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise YitoError('Falló el bootstrap; instancia intacta. Log: ' + str(work / 'bootstrap.log'))
    write_json(work / 'request.json', {'root': str(root.resolve()), 'info': info, 'tools_changed': tools_changed})
    return work


def launch(work):
    env = host_environment(work)
    env['MITO_UPDATE_PYTHON'] = str(work / 'runner/python.exe')
    env['MITO_UPDATE_REQUEST'] = str(work / 'request.json')
    command = '"%MITO_UPDATE_PYTHON%" -B -I -X utf8 -m mito.self_update "%MITO_UPDATE_REQUEST%"'
    # The visible CMD is explicitly requested; it has no cwd/runtime dependency on MITO.
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
    return subprocess.Popen('"' + env['COMSPEC'] + '" /d /v:off /s /c "' + command + '"',
                            cwd=work, env=env, close_fds=True, creationflags=flags)


def launch_ready(work):
    process = launch(work)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (work / 'ready.json').is_file():
            return process
        if process.poll() is not None:
            break
        time.sleep(.1)
    raise YitoError('El CMD de actualización no confirmó el arranque. El gestor permanece abierto. Preparación: ' + str(work))


def running_processes(root):
    """Read-only process inventory; never terminate the user's applications."""
    script = """$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
$root = [IO.Path]::GetFullPath($env:MITO_UPDATE_ROOT).TrimEnd('\\') + '\\'
@(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { "$($_.ProcessId): $($_.Name)" }) | ConvertTo-Json -Compress
"""
    env = dict(os.environ, MITO_UPDATE_ROOT=str(root))
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, encoding='utf-8', timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise YitoError('No se pudieron comprobar los procesos abiertos: ' + result.stderr)
    value = json.loads(result.stdout or '[]')
    return [value] if isinstance(value, str) else (value or [])


def safe_target(root, relative):
    path = inside(root, relative)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise YitoError('Ruta administrada redirigida: ' + relative)
    return path


def apply(root, payload, info, *, tools_changed=False, processes=running_processes):
    require_distribution(root)
    validate_payload(payload, info['version'], prepared_tools=tools_changed)
    if version(info['version']) <= version(info['current']):
        raise YitoError('La release no es posterior a la versión instalada')
    paths = [path for path in MANAGED if path not in OPTIONAL_ASSETS or (payload / path).is_file()]
    if tools_changed:
        paths += ['core/tools', 'core/state/bootstrap-tools.json']
    safe_target(root, 'core/state')
    with mutation(root):
        if detected_version(root) != info['current']:
            raise YitoError('La versión instalada cambió desde la preparación')
        if processes(root):
            raise YitoError('Todavía hay procesos de MITO abiertos. Cierra todas sus ventanas y aplicaciones.')
        for relative in paths:
            safe_target(root, relative)
        staging = safe_target(root, 'core/staging')
        staging.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='self-update-', dir=staging))
        incoming, backup = stage / 'incoming', stage / 'backup'
        for relative in paths:
            source, target = payload / relative, incoming / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        if processes(root):
            raise YitoError('Se abrió un proceso de MITO durante la preparación; cierra sus aplicaciones y vuelve a intentarlo.')
        moved, published = [], []
        try:
            for relative in paths:
                target = safe_target(root, relative)
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.rename(old)
                    moved.append(relative)
                (incoming / relative).rename(target)
                published.append(relative)
        except BaseException:
            failures = []
            for relative in reversed(published):
                try:
                    target = safe_target(root, relative)
                    target.rename(incoming / relative)
                except OSError as exc:
                    failures.append(str(exc))
            for relative in reversed(moved):
                try:
                    (backup / relative).rename(safe_target(root, relative))
                except OSError as exc:
                    failures.append(str(exc))
            if failures:
                raise YitoError('Restauración incompleta. Conserva ' + str(stage) + ': ' + '; '.join(failures))
            raise
        # Retain the previous program/runtime for manual recovery, never user data.
        return backup


def main():
    import sys
    request = Path(sys.argv[1]).resolve()
    root = None
    try:
        data = read_json(request)
        root = Path(data['root'])
        write_json(request.parent / 'ready.json', {'pid': os.getpid()})
        print('MITO ' + data['info']['version'] + ': actualización preparada.')
        print('Guarda tu trabajo y cierra TODAS las ventanas de MITO, pestañas, aplicaciones y MCP.')
        print('Cierra también las aplicaciones externas abiertas con el entorno de MITO.')
        input('Cuando hayas terminado, pulsa Intro para actualizar (Ctrl+C cancela): ')
        while active := running_processes(root):
            print('Todavía están abiertos: ' + ', '.join(active))
            input('Ciérralos y pulsa Intro para volver a comprobar (Ctrl+C cancela): ')
        backup = apply(root, request.parent / 'payload', data['info'], tools_changed=data['tools_changed'])
        print('MITO actualizado correctamente. Copia anterior: ' + str(backup))
        print('Puedes volver a abrir mito.cmd o tu acceso directo.')
        write_json(request.parent / 'result.json', {'status': 'complete', 'backup': str(backup)})
    except (Exception, KeyboardInterrupt) as exc:
        if root is not None and isinstance(exc, Exception):
            from .error_tracking import capture, flush
            capture(root, exc, operation='self-update.apply')
            flush()
        print('Actualización cancelada o fallida: ' + str(exc))
        write_json(request.parent / 'result.json', {'status': 'failed', 'error': str(exc)})
    input('Pulsa Intro para cerrar esta ventana…')


if __name__ == '__main__':
    main()
