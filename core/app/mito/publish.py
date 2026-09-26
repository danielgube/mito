"""Publish numbered releases using Git's existing GitHub credential."""
import hashlib
from datetime import date
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from .release import build_release, detected_version
from .storage import YitoError, read_json, write_json, mutation

REPOSITORY = 'danielgube/mito'
VERSION_FILE = 'core/app/mito/__init__.py'
INSTALL_NOTES = 'Distribución mínima para Windows x64. Extrae el ZIP y ejecuta mito.cmd; el primer arranque descarga el bootstrap.'


def commit_notes(root, head):
    """Use the previous publisher commit as boundary, including merged changes."""
    history = git(root, 'log', '--first-parent', '--format=%H%x00%s', head)
    base = next((line.split('\0', 1)[0] for line in history.splitlines()
                 if '\0' in line and re.fullmatch(r'release: v\d+\.\d+\.\d+', line.split('\0', 1)[1])), None)
    revision = f'{base}..{head}' if base else head
    commits = git(root, 'log', '--reverse', '--format=%H%x00%s', revision)
    lines = []
    for line in commits.splitlines():
        sha, subject = line.split('\0', 1)
        if re.fullmatch(r'release: v\d+\.\d+\.\d+', subject):
            continue
        subject = html.escape(subject, quote=False)
        subject = re.sub(r'([\\`*_{}\[\]()#+.!|~-])', r'\\\1', subject)
        lines.append(f'- {subject} ([{sha[:7]}](https://github.com/{REPOSITORY}/commit/{sha}))')
    summary = '\n'.join(lines) or '- Sin cambios desde la release anterior.'
    if base:
        summary += f'\n\n[Comparar cambios](https://github.com/{REPOSITORY}/compare/{base}...{head})'
    return '### Commits incluidos\n\n' + summary


def updated_changelog(original, version, notes, day):
    """Keep manually authored history and unreleased notes intact."""
    original = original.replace('\r\n', '\n')
    if re.search(rf'^## {re.escape(version)}(?:\s|$)', original, re.M):
        raise YitoError(f'CHANGELOG.md ya contiene la versión {version}')
    entry = f'## {version} — {day}\n\n{notes}\n\n'
    heading = next((m for m in re.finditer(r'^## .+$', original, re.M)
                    if m.group() != '## Sin publicar'), None)
    position = heading.start() if heading else len(original)
    prefix = original[:position].rstrip() + '\n\n'
    return prefix + entry + original[position:]


def git(root, *args, input=None):
    result = subprocess.run(['git', *args], cwd=root, input=input, capture_output=True,
                            text=True, encoding='utf-8', timeout=120,
                            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if result.returncode:
        raise YitoError(f'Git {args[0]} falló; comprueba conexión, permisos y estado del repositorio')
    return result.stdout.strip()


class GitHub:
    def __init__(self, root):
        credential = git(root, 'credential', 'fill', input=f'protocol=https\nhost=github.com\npath={REPOSITORY}.git\n\n')
        self.token = dict(line.split('=',1) for line in credential.splitlines() if '=' in line).get('password')
        if not self.token:
            raise YitoError('Inicia sesión en GitHub con Git Credential Manager')

    def request(self, method, path, data=None, missing=False, upload=False):
        host = 'uploads.github.com' if upload else 'api.github.com'
        url = f'https://{host}/repos/{REPOSITORY}{path}'
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        headers = {'Authorization': f'Bearer {self.token}', 'User-Agent': 'MITO',
                   'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10',
                   'Content-Type': 'application/octet-stream' if upload else 'application/json'}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if missing and exc.code == 404:
                return None
            raise YitoError(f'GitHub devolvió HTTP {exc.code}; vuelve a ejecutar release para reanudar') from None
        except urllib.error.URLError:
            raise YitoError('No se pudo conectar con GitHub; vuelve a ejecutar release para reanudar') from None


def next_version(local, releases, kind='hotfix'):
    if kind not in ('mayor', 'minor', 'hotfix'):
        raise YitoError('Tipo de release inválido: usa mayor, minor o hotfix')
    versions = [tuple(map(int, local.split('.')))]
    for release in releases:
        match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', release['tag_name'])
        if match:
            versions.append(tuple(map(int, match.groups())))
    major, minor, patch = max(versions)
    if kind == 'mayor':
        return f'{major + 1}.0.1'
    if kind == 'minor':
        return f'{major}.{minor + 1}.1'
    return f'{major}.{minor}.{patch + 1}'


def publish(engine, output=None, log=print, *, kind='hotfix'):
    if kind not in ('mayor', 'minor', 'hotfix'):
        raise YitoError('Tipo de release inválido: usa mayor, minor o hotfix')
    with mutation(engine.root / "core/state/publisher"):
        return _publish(engine, output, log, kind=kind)


def _publish(engine, output=None, log=print, *, kind='hotfix'):
    root = engine.root
    if git(root, 'rev-parse', '--show-toplevel').replace('\\','/').casefold() != root.as_posix().casefold():
        raise YitoError('release requiere el checkout Git de MITO')
    remote = git(root, 'remote', 'get-url', 'origin').removesuffix('.git').rstrip('/').casefold()
    if remote not in (f'https://github.com/{REPOSITORY}', f'git@github.com:{REPOSITORY}'):
        raise YitoError(f'origin debe apuntar a {REPOSITORY}')
    if git(root, 'branch', '--show-current') != 'main' or git(root, 'status', '--porcelain'):
        raise YitoError('Haz commit de tus cambios y ejecuta release desde main limpio')
    api = GitHub(root)
    if not api.request('GET', '').get('permissions', {}).get('push'):
        raise YitoError('La cuenta de GitHub no tiene permiso de escritura')
    git(root, 'fetch', '--quiet', 'origin', 'main')
    pending_path = root / 'core/state/pending-release.json'
    pending = read_json(pending_path) if pending_path.exists() else None
    head = git(root, 'rev-parse', 'HEAD')
    if pending:
        if head != pending['commit'] or detected_version(root) != pending['version']:
            raise YitoError('Hay una release pendiente de otro commit; vuelve a ese commit para reanudar')
        version = pending['version']
        log(f'Reanudando v{version}; se conserva la versión pendiente sin aplicar otro incremento…')
    else:
        if head != git(root, 'rev-parse', 'origin/main'):
            raise YitoError('Haz push de main antes de crear la release')
        releases = []
        page = 1
        while True:
            batch = api.request('GET', f'/releases?per_page=100&page={page}')
            releases.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        version = next_version(detected_version(root), releases, kind)
        notes = commit_notes(root, head)
        changelog = root / 'CHANGELOG.md'
        changelog_original = changelog.read_bytes() if changelog.exists() else None
        changelog_updated = updated_changelog(
            changelog_original.decode('utf-8-sig') if changelog_original is not None else '# Changelog\n',
            version, notes, date.today().isoformat())
        log(f'Preparando v{version}. Ejecutando pruebas…')
        result = subprocess.run([sys.executable, '-B', '-I', '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'core/tests'], cwd=root)
        if result.returncode:
            raise YitoError('Las pruebas fallaron; no se publica la release')
        source = root / VERSION_FILE
        original = source.read_text(encoding='utf-8')
        updated, count = re.subn(r'(?m)^__version__\s*=.*$', f'__version__ = "{version}"', original)
        if count != 1:
            raise YitoError('No se pudo actualizar __version__')
        try:
            source.write_text(updated, encoding='utf-8')
            changelog.write_text(changelog_updated, encoding='utf-8')
            git(root, 'add', '--', VERSION_FILE, 'CHANGELOG.md')
            # Use the checkout's last author if user.name is not configured.
            author = git(root, 'show', '-s', '--format=%an', 'HEAD')
            email = git(root, 'show', '-s', '--format=%ae', 'HEAD')
            git(root, '-c', f'user.name={author}', '-c', f'user.email={email}', 'commit', '-m', f'release: v{version}')
        except Exception:
            source.write_text(original, encoding='utf-8')
            if changelog_original is None:
                changelog.unlink(missing_ok=True)
            else:
                changelog.write_bytes(changelog_original)
            git(root, 'restore', '--staged', '--', VERSION_FILE, 'CHANGELOG.md')
            raise
        head = git(root, 'rev-parse', 'HEAD')
        pending = {'version': version, 'commit': head, 'body': INSTALL_NOTES + '\n\n' + notes}
        write_json(pending_path, pending)
    git(root, 'push', 'origin', 'main')
    tag = f'v{version}'
    # An existing tag must identify this exact release commit.
    remote_tag = git(root, 'ls-remote', 'origin', f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}').splitlines()
    if remote_tag and not any(line.split()[0] == head for line in remote_tag):
        raise YitoError(f'El tag {tag} ya apunta a otro commit')
    if pending.get('archive'):
        from pathlib import Path
        archive = Path(pending['archive'])
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != pending['sha256']:
            raise YitoError('El ZIP pendiente falta o ha cambiado')
    else:
        archive = build_release(engine, output)
        pending.update(archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        write_json(pending_path, pending)
    release = api.request('GET', f'/releases/tags/{tag}', missing=True)
    if release is None:
        release = api.request('POST', '/releases', {'tag_name': tag, 'target_commitish': head,
            'name': f'MITO {version}', 'draft': True,
            'body': pending.get('body', INSTALL_NOTES)})
    for asset in (archive, archive.with_suffix('.zip.sha256')):
        data = asset.read_bytes()
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        existing = next((a for a in release['assets'] if a['name'] == asset.name), None)
        if existing:
            if existing.get('digest') != digest:
                raise YitoError(f'El asset remoto {asset.name} no coincide; no se sobrescribe')
        else:
            log(f'Subiendo {asset.name}…')
            uploaded = api.request('POST', f'/releases/{release["id"]}/assets?name={urllib.parse.quote(asset.name)}', data, upload=True)
            if uploaded.get('digest') != digest:
                raise YitoError('GitHub no confirma el SHA-256 del asset subido')
    if release['draft']:
        release = api.request('PATCH', f'/releases/{release["id"]}', {'draft': False, 'make_latest': 'true'})
    pending_path.unlink()
    return release['html_url']
