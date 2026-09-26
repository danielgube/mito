"""Build a clean distributable from an explicit allowlist, never user data."""
import ast
import re
import hashlib
import json
from pathlib import Path
import zipfile
from .storage import YitoError, mutation
from .i18n import LANGUAGES

DEFAULT_CONFIG = """bucket.main=https://github.com/ScoopInstaller/Main
bucket.extras=https://github.com/ScoopInstaller/Extras
bucket.versions=https://github.com/ScoopInstaller/Versions
bucket.java=https://github.com/ScoopInstaller/Java
shortcut.badgeText=MITO
shortcut.badgeColor=naranja
terminal.defaultShell=cmd
mito.language=system
mito.update_url=https://api.github.com/repos/danielgube/mito/releases/latest
mcp.jobs.monitor=false
# Error reports to the public MITO project. Set false to keep only local logs.
posthog.enabled=true
posthog.host=https://eu.i.posthog.com
posthog.api_key=phc_nJpJbs9U45dYkyKoiHdHpRnu98MaW6ir3WSkkEZGdobs
"""


def detected_version(root):
    """Read the packaged source without executing it or depending on Git/network."""
    source = root / 'core/app/mito/__init__.py'
    try:
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        values = [ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets)]
    except (OSError, SyntaxError, ValueError, TypeError) as exc:
        raise YitoError('No se puede leer la versión de core/app/mito/__init__.py') from exc
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', values[0]):
        raise YitoError('__version__ debe declarar una única versión mayor.menor.parche')
    return values[0]


def build_release(engine, output=None):
    version = detected_version(engine.root)
    destination = Path(output).resolve() if output else engine.root / 'releases'
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f'mito-{version}-windows-x64.zip'
    files = [engine.root / path for path in ('mito.cmd', 'core/bootstrap.ps1', 'core/bootstrap.py', 'core/bootstrap.lock.json', 'core/assets/branding/mito.ico', 'core/assets/branding/pocketools.ico')]
    files += [p for p in (engine.root / 'core/app/mito').rglob('*.py') if '__pycache__' not in p.parts]
    files += [engine.root / 'core/app/mito/locales' / f'{language}.json' for language in LANGUAGES]
    if any(not p.is_file() or not p.resolve().is_relative_to(engine.root) for p in files):
        raise YitoError('Faltan archivos de distribución o hay enlaces fuera de MITO')
    with mutation(engine.root):
        if archive.exists():
            raise YitoError(f'La release ya existe: {archive}')
        try:
            with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for path in sorted(files):
                    z.write(path, path.relative_to(engine.root).as_posix())
                z.writestr('config.properties', DEFAULT_CONFIG)
                z.writestr('profiles/default.json', json.dumps({'workspace': 'default', 'apps': [], 'env': {}}, indent=2))
                z.writestr('release.json', json.dumps({'version': version, 'platform': 'windows-x64', 'kind': 'bootstrap'}, indent=2))
            with zipfile.ZipFile(archive) as z:
                if z.testzip():
                    raise YitoError('El ZIP generado no supera la verificación')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    return archive
