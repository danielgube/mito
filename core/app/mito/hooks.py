"""Run catalog installation scripts. This is trusted code, not a sandbox."""
from .i18n import tr
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .storage import YitoError, inside


SCRIPT = r'''
param([string]$ContextPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $context = Get-Content -LiteralPath $ContextPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $dir = $context.dir
    $original_dir = $dir
    $persist_dir = $context.persist_dir
    $app = $context.app
    $bucket = $context.bucket
    $bucketsdir = $context.bucketsdir
    $version = $context.version
    $architecture = '64bit'
    $global = $false
    $manifest = $context.manifest
    $fname = $context.fname
    function info { param([string]$Message) Write-Output $Message }
    function warn { param([string]$Message) Write-Output "Aviso: $Message" }
    function error { param([string]$Message) throw $Message }
    function abort { param([string]$Message) throw $Message }
    function Expand-7zipArchive {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true, Position=0)][string]$Path,
            [Parameter(Position=1)][string]$DestinationPath = (Split-Path $Path),
            [string]$ExtractDir = '.',
            [switch]$Removal
        )
        & $context.python -B -I -m mito.hooks $context.root $dir $Path $DestinationPath $ExtractDir
        if ($LASTEXITCODE -ne 0) { throw 'Expand-7zipArchive falló' }
        if ($Removal) { Remove-Item -LiteralPath $Path -Force }
    }
    function Expand-InnoArchive {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true, Position=0)][string]$Path,
            [Parameter(Position=1)][string]$DestinationPath = (Split-Path $Path),
            [string]$ExtractDir = '{app}',
            [switch]$Removal
        )
        & $context.python -B -I -m mito.hooks inno $context.root $dir $Path $DestinationPath $ExtractDir
        if ($LASTEXITCODE -ne 0) { throw 'Expand-InnoArchive falló' }
        if ($Removal) { Remove-Item -LiteralPath $Path -Force }
    }
    function Expand-DarkArchive {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true, Position=0)][string]$Path,
            [Parameter(Position=1)][string]$DestinationPath = (Split-Path $Path),
            [switch]$Removal
        )
        & $context.python -B -I -m mito.hooks dark $context.root $dir $Path $DestinationPath '.'
        if ($LASTEXITCODE -ne 0) { throw 'Expand-DarkArchive falló' }
        if ($Removal) { Remove-Item -LiteralPath $Path -Force }
    }
    function Expand-MsiArchive {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true, Position=0)][string]$Path,
            [Parameter(Position=1)][string]$DestinationPath = (Split-Path $Path),
            [string]$ExtractDir = '.',
            [switch]$Removal
        )
        & $context.python -B -I -m mito.hooks msi $context.root $dir $Path $DestinationPath $ExtractDir
        if ($LASTEXITCODE -ne 0) { throw 'Expand-MsiArchive falló' }
        if ($Removal) { Remove-Item -LiteralPath $Path -Force }
    }
    function Get-HelperPath {
        [CmdletBinding()]
        param([Parameter(Mandatory=$true)][string]$Helper)
        if ($Helper -ne 'Innounp') { throw "Helper no soportado: $Helper" }
        $helperPath = Join-Path $context.root 'core/tools/innounp/innounp.exe'
        if (!(Test-Path -LiteralPath $helperPath -PathType Leaf)) { throw 'Herramienta incompleta: innounp' }
        return $helperPath
    }
    function Invoke-ExternalCommand {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true)][string]$FilePath,
            [string[]]$ArgumentList = @(),
            [string]$LogPath
        )
        $output = & $FilePath @ArgumentList 2>&1
        $code = $LASTEXITCODE
        if ($LogPath) { $output | Out-File -LiteralPath $LogPath -Encoding utf8 }
        if ($code -ne 0) { throw "$FilePath falló ($code): $($output -join [Environment]::NewLine)" }
        return $output
    }
    Set-Location -LiteralPath $dir
    if ($context.file) {
        $installerArgs = @($context.arguments)
        $global:LASTEXITCODE = 0
        & $context.file @installerArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        & ([scriptblock]::Create($context.script))
    }
} catch {
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
'''


def run_pre_install(root, key, manifest, payload, log):
    run_script(root, key, manifest, payload, log, 'pre_install', manifest.get('pre_install'))


def run_installer(root, key, manifest, payload, log):
    from .catalog import installer_definition
    installer = installer_definition(manifest)
    if not installer:
        return
    if 'file' in installer:
        file = inside(payload, installer['file'])
        if not file.is_file():
            raise YitoError(tr('No existe {p1}', p1=installer['file']))
        run_script(root, key, manifest, payload, log, 'installer.file', None,
                   file=file, arguments=installer.get('args'))
    else:
        run_script(root, key, manifest, payload, log, 'installer.script', installer['script'])


def run_script(root, key, manifest, payload, log, hook, script, *, file=None, arguments=None):
    if not script and file is None:
        return
    from .network import proxy_environment
    from .isolation import isolated
    home = root / 'home'
    env = dict(os.environ)
    env['PATH'] = env.get('MITO_HOST_PATH', env.get('PATH', ''))
    for name in env.get('MITO_ACTIVE_VARS', '').split(';'):
        if name:
            env.pop(name, None)
    env = proxy_environment(root, env)
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home/'AppData/Roaming'),
               LOCALAPPDATA=str(home/'AppData/Local'), TEMP=str(home/'tmp'), TMP=str(home/'tmp'),
               HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):], MITO_ROOT=str(root))
    env.pop('MITO_PROFILE', None)
    env.pop('MITO_ACTIVE_VARS', None)
    # Isolated payloads are templates: never seed them from a user's profile.
    persist = payload.parent / 'persist' if isolated({'manifest': manifest}) else root/'home/persist'/key
    persist.mkdir(parents=True, exist_ok=True)
    bucket, app = key.split('/')
    from .catalog import array
    from .transfer import download_filename
    filenames = [download_filename(url) for url in array(manifest['url'])]
    context = dict(root=str(root), python=str(Path(sys.executable).with_name('python.exe')), dir=str(payload), persist_dir=str(persist),
                   app=app, bucket=bucket, bucketsdir=str(payload.parent/'hook-buckets'),
                   version=str(manifest['version']), manifest=manifest,
                   fname=filenames[0] if len(filenames) == 1 else filenames,
                   script='\n'.join(script) if isinstance(script, list) else script)
    if file is not None:
        from .arguments import split
        values = dict(dir=str(payload), original_dir=str(payload), persist_dir=str(persist),
                      version=str(manifest['version']), architecture='64bit', global_='False')
        values['global'] = values.pop('global_')
        pattern = re.compile(r'\$\{(' + '|'.join(values) + r')\}|\$(' + '|'.join(values) + r')\b')
        context.update(file=str(file), arguments=[pattern.sub(
            lambda match: values[match[1] or match[2]], arg) for arg in split(arguments)])
    context_path = payload.parent/'hook-context.json'
    context_path.write_text(json.dumps(context, ensure_ascii=False), encoding='utf-8')
    runner = payload.parent/(hook + '.ps1')
    runner.write_text(SCRIPT, encoding='utf-8-sig')
    log(tr('{p0}: ejecutando {hook}', p0=key, hook=hook))
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                 'Bypass', '-File', str(runner), str(context_path)],
                                cwd=payload, env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        raise YitoError(tr('{p0}: no se pudo ejecutar {hook}: {p2}', p0=key, hook=hook, p2=exc)) from exc
    for line in result.stdout.splitlines():
        log(f'{key}: {line}')
    if result.returncode:
        raise YitoError(tr('{p0}: {hook} falló ({p2}): {p4}', p0=key, hook=hook, p2=result.returncode, p4=result.stderr.strip()))
    if result.stderr.strip():
        log(f'{key}: {result.stderr.strip()}')
    for path in payload.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise YitoError(tr('{p0}: {hook} creó un enlace no gestionado: {p2}', p0=key, hook=hook, p2=path.name))


def expand(root, payload, archive, destination, extract_dir='.'):
    """Generic Scoop helper using MITO's validated extractor inside staging."""
    from .archives import extract_7zip
    payload = payload.resolve()
    archive, destination = archive.resolve(), destination.resolve()
    if not archive.is_relative_to(payload) or not destination.is_relative_to(payload):
        raise YitoError(tr('Expand-7zipArchive requiere rutas dentro del payload'))
    with tempfile.TemporaryDirectory(dir=payload.parent) as tmp:
        expanded = Path(tmp)
        extract_7zip(archive, expanded, root)
        source = inside(expanded, extract_dir)
        if not source.is_dir():
            raise YitoError(tr('Expand-7zipArchive: ExtractDir no encontrado'))
        shutil.copytree(source, destination, dirs_exist_ok=True)


def expand_inno(root, payload, archive, destination, extract_dir='{app}'):
    """Implement Scoop's Inno helper through MITO's validated private extractor."""
    from .inno import extract_inno
    payload = payload.resolve()
    archive, destination = archive.resolve(), destination.resolve()
    if not archive.is_relative_to(payload) or not destination.is_relative_to(payload):
        message = tr('Expand-7zipArchive requiere rutas dentro del payload')
        raise YitoError(message.replace('Expand-7zipArchive', 'Expand-InnoArchive'))
    if extract_dir.casefold() != '{app}':
        message = tr('Expand-7zipArchive: ExtractDir no encontrado')
        raise YitoError(message.replace('Expand-7zipArchive', 'Expand-InnoArchive'))
    with tempfile.TemporaryDirectory(dir=payload.parent) as tmp:
        expanded = Path(tmp)
        extract_inno(archive, expanded, root)
        shutil.copytree(expanded, destination, dirs_exist_ok=True)


def expand_installer(kind, root, payload, archive, destination, extract_dir='.'):
    """Generic data-only installer extractors, confined like the other helpers."""
    from .burn import extract_burn
    from .msi import extract_msi
    payload = payload.resolve()
    archive, destination = archive.resolve(), destination.resolve()
    if not archive.is_relative_to(payload) or not destination.is_relative_to(payload):
        raise YitoError(f'Expand-{kind}Archive requiere rutas dentro del payload')
    with tempfile.TemporaryDirectory(dir=payload.parent) as temporary:
        expanded = Path(temporary)
        if kind == 'dark':
            extract_burn(archive, expanded, root, log=print)
        elif kind == 'msi':
            extract_msi(archive, expanded, root, log=print)
        else:
            raise YitoError(f'Extractor no soportado: {kind}')
        source = inside(expanded, extract_dir)
        if not source.is_dir():
            raise YitoError(f'Expand-{kind}Archive: ExtractDir no encontrado')
        shutil.copytree(source, destination, dirs_exist_ok=True)


if __name__ == '__main__':
    try:
        arguments = sys.argv[1:]
        if arguments and arguments[0] in ('dark', 'msi'):
            expand_installer(arguments[0], *(Path(arg) for arg in arguments[1:5]), arguments[5])
        elif arguments and arguments[0] == 'inno':
            expand_inno(*(Path(arg) for arg in arguments[1:5]), arguments[5])
        else:
            expand(*(Path(arg) for arg in arguments[:4]), arguments[4])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
