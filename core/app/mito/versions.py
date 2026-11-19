"""Versiones previas de un componente bucket/app, sin adaptadores por aplicación.

Dos fuentes genéricas, como Scoop:
- Histórico Git del bucket (sólo repositorios GitHub): el manifiesto real publicado
  para esa versión, con su URL y su hash.
- autoupdate del manifiesto actual (`scoop install app@versión`): sustituye las
  variables de versión y obtiene el hash si autoupdate.hash lo permite; si no, la
  descarga queda sin hash y requiere la confirmación habitual.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import tempfile
import urllib.parse

from .i18n import tr
from .storage import YitoError, identifier, read_json
from .transfer import download

GITHUB = re.compile(r'https?://github\.com/([\w-]+)/([\w.-]+?)(?:\.git)?/?')
HISTORY_PAGES = 5
GIT_ATTEMPTS = 6
MAX_TEXT = 8 * 1024 * 1024
VERSION_FIELDS = ('underscoreVersion', 'dashVersion', 'cleanVersion', 'majorVersion', 'minorVersion',
                  'patchVersion', 'buildVersion', 'preReleaseVersion', 'matchHead', 'matchTail', 'version')
HASH_PATTERNS = {'sha256': '([a-fA-F0-9]{64})', 'sha512': '([a-fA-F0-9]{128})',
                 'sha1': '([a-fA-F0-9]{40})', 'md5': '([a-fA-F0-9]{32})', 'checksum': '([a-fA-F0-9]{32,128})'}
MESSAGE_PATTERNS = (
    re.compile(r'update to version\s+v?([0-9][0-9A-Za-z.+_-]*?)(?=[\s,;:)]|$)', re.IGNORECASE),
    re.compile(r'^[\w.-]+@v?([0-9][0-9A-Za-z.+_-]*?):'),
    re.compile(r'^(?:add|update|upgrade|bump)\b.*?\bv?(\d+(?:\.\d+)+[0-9A-Za-z.+_-]*?)(?=[\s,;:)]|$)', re.IGNORECASE),
)


def fetch_text(root, url, max_bytes=MAX_TEXT):
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / 'response'
        download(url, target, root=root, max_bytes=max_bytes, attempts=2, delay=1)
        return target.read_bytes().decode('utf-8-sig', errors='replace')


def validate_version(version):
    version = str(version).strip()
    if not version:
        raise YitoError(tr('Selecciona o escribe una versión'))
    identifier(version)
    return version


def autoupdate_error(detail):
    # El detalle es técnico (campo o variable de Scoop) y no se traduce.
    return YitoError(tr('El manifiesto no permite generar esa versión con autoupdate: {p1}', p1=detail))


# --- Histórico Git -------------------------------------------------------------

def repository(catalog, key):
    """(owner, repo, app) del bucket si su origen es GitHub; None si no lo es."""
    bucket, app = map(identifier, key.split('/'))
    index = catalog.folder / f'{bucket}.json'
    source = catalog.sources().get(bucket) or (read_json(index).get('source') if index.exists() else None)
    match = GITHUB.fullmatch(source or '')
    return (match[1], match[2], app) if match else None


def message_version(message):
    first = message.strip().splitlines()[0] if message.strip() else ''
    for pattern in MESSAGE_PATTERNS:
        found = pattern.search(first)
        if found:
            return found[1].rstrip('.')
    return None


def mentions(message, version):
    first = message.strip().splitlines()[0] if message.strip() else ''
    return re.search(r'(?<![0-9A-Za-z.])v?' + re.escape(version) + r'(?![0-9A-Za-z]|\.[0-9A-Za-z])', first) is not None


def history(root, catalog, key):
    """Commits del manifiesto en el bucket, del más reciente al más antiguo."""
    found = repository(catalog, key)
    if not found:
        return None
    owner, repo, app = found
    for path in (f'bucket/{app}.json', f'{app}.json'):
        commits = []
        for page in range(1, HISTORY_PAGES + 1):
            query = urllib.parse.urlencode({'path': path, 'per_page': 100, 'page': page})
            batch = json.loads(fetch_text(root, f'https://api.github.com/repos/{owner}/{repo}/commits?{query}'))
            if not isinstance(batch, list):
                raise YitoError(tr('No se pudo consultar el histórico del bucket: {p1}', p1='GitHub API'))
            for item in batch:
                commit = item.get('commit') or {}
                commits.append({'sha': str(item.get('sha', '')), 'message': str(commit.get('message', '')),
                                'date': str((commit.get('author') or {}).get('date', ''))[:10]})
            if len(batch) < 100:
                break
        if commits:
            return {'owner': owner, 'repo': repo, 'path': path, 'commits': commits}
    return {'owner': owner, 'repo': repo, 'path': f'bucket/{app}.json', 'commits': []}


def history_versions(found):
    """Versiones únicas deducidas de los mensajes, de la más reciente a la más antigua."""
    result, seen = [], set()
    for commit in (found or {}).get('commits', []):
        version = message_version(commit['message'])
        if version and version not in seen:
            seen.add(version)
            result.append({'version': version, 'date': commit['date']})
    return result


def manifest_from_history(root, found, version):
    """Manifiesto exacto de esa versión: el commit más reciente cuyo manifiesto la declara.
    Así se recogen correcciones posteriores como «app@1.2.3: Fix hash»."""
    attempts = 0
    for commit in found['commits']:
        if not mentions(commit['message'], version) or not re.fullmatch(r'[0-9a-f]{7,40}', commit['sha']):
            continue
        attempts += 1
        if attempts > GIT_ATTEMPTS:
            break
        url = f'https://raw.githubusercontent.com/{found["owner"]}/{found["repo"]}/{commit["sha"]}/{found["path"]}'
        manifest = json.loads(fetch_text(root, url))
        if isinstance(manifest, dict) and str(manifest.get('version')) == version:
            return manifest, commit
    return None, None


# --- autoupdate ----------------------------------------------------------------

def version_variables(version):
    version = str(version)
    parts = re.split(r'[._-]', version)
    head = re.match(r'\d+\.\d+(?:\.\d+)?', version)
    values = {
        'version': version,
        'underscoreVersion': version.replace('.', '_'),
        'dashVersion': version.replace('.', '-'),
        'cleanVersion': version.replace('.', '').replace('-', ''),
        'majorVersion': parts[0] if len(parts) > 0 else '',
        'minorVersion': parts[1] if len(parts) > 1 else '',
        'patchVersion': parts[2] if len(parts) > 2 else '',
        'buildVersion': parts[3] if len(parts) > 3 else '',
        'preReleaseVersion': version.split('-', 1)[1] if '-' in version else '',
        'matchHead': head[0] if head else version,
        'matchTail': version[head.end():] if head else '',
    }
    return values


def substitute(value, variables, escape=False):
    if isinstance(value, str):
        # Como Scoop: nombres sin distinguir mayúsculas; los más largos primero.
        lookup = {name.lower(): text for name, text in variables.items()}
        names = '|'.join(sorted(map(re.escape, variables), key=len, reverse=True))
        pattern = r'\$(?:\{(' + names + r')\}|(' + names + r'))'
        def replace(match):
            text = lookup[(match[1] or match[2]).lower()]
            return re.escape(text) if escape else text
        return re.sub(pattern, replace, value, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [substitute(item, variables, escape) for item in value]
    if isinstance(value, dict):
        return {name: substitute(item, variables, escape) for name, item in value.items()}
    return value


def unresolved(value):
    """Variables de checkver ($match1, $matchVersion…) no se pueden reproducir sin checkver."""
    text = json.dumps(value)
    return sorted(set(re.findall(r'\$\{?(match\w*)\}?', text)) - {'matchHead', 'matchTail'})


def remote_basename(url):
    path = urllib.parse.urlsplit(url.split('#', 1)[0]).path
    return urllib.parse.unquote(path.rsplit('/', 1)[-1])


def hash_from_spec(root, spec, url, variables):
    """Hash según autoupdate.hash (modo extract: url + regex/find). Devuelve (hash, motivo).
    El motivo es un detalle técnico sin traducir."""
    if isinstance(spec, str):
        spec = {'url': spec}
    if not isinstance(spec, dict) or not isinstance(spec.get('url'), str):
        return None, 'autoupdate.hash.url'
    if spec.get('mode', 'extract') != 'extract' or 'jsonpath' in spec or 'xpath' in spec:
        return None, 'autoupdate.hash.mode=' + str(spec.get('mode', 'jsonpath/xpath'))
    clean = url.split('#', 1)[0]
    basename = remote_basename(clean)
    context = dict(variables, url=clean, baseurl=clean.rsplit('/', 1)[0], basename=basename)
    hash_url = substitute(spec['url'], context)
    if unresolved(hash_url):
        return None, 'checkver: ' + ', '.join('$' + name for name in unresolved(hash_url))
    try:
        text = fetch_text(root, hash_url)
    except Exception as exc:
        return None, str(exc)
    pattern = spec.get('regex') or spec.get('find')
    try:
        if pattern:
            expression = substitute(pattern, context, escape=True)
            for name, group in HASH_PATTERNS.items():
                expression = expression.replace('$' + name, group)
            match = re.search(expression, text)
        else:
            match = (re.search(r'([a-fA-F0-9]{32,128})[ \t]+\*?' + re.escape(basename) + r'\b', text)
                     or re.search(re.escape(basename) + r'[^\n]*?\b([a-fA-F0-9]{64}|[a-fA-F0-9]{40}|[a-fA-F0-9]{32})\b', text)
                     or re.fullmatch(r'\s*([a-fA-F0-9]{32,128})\s*', text))
    except re.error as exc:
        return None, 'regex: ' + str(exc)
    digest = (match.group(1) if match.groups() else match.group(0)).lower() if match else ''
    prefix = {32: 'md5:', 40: 'sha1:', 64: '', 128: 'sha512:'}.get(len(digest))
    if prefix is None or not re.fullmatch(r'[a-f0-9]+', digest):
        return None, 'hash not found: ' + hash_url
    return prefix + digest, None


def manifest_from_autoupdate(root, manifest, version, log=print):
    """Manifiesto para otra versión a partir de autoupdate, como scoop install app@versión."""
    auto = manifest.get('autoupdate')
    if not isinstance(auto, dict):
        raise autoupdate_error('autoupdate')
    variables = version_variables(version)
    result = copy.deepcopy(manifest)
    result['version'] = version

    def apply(target, block):
        changed_url = 'url' in block
        for field, value in block.items():
            if field not in ('architecture', 'hash'):
                target[field] = substitute(value, variables)
        if changed_url:
            target.pop('hash', None)
        return changed_url

    holder, spec = result, auto.get('hash')
    top_changed = apply(result, auto)
    architectures = auto.get('architecture') or {}
    for name, block in architectures.items():
        if isinstance(block, dict):
            target = result.setdefault('architecture', {}).setdefault(name, {})
            if apply(target, block) and name == '64bit':
                holder, spec = target, block.get('hash', auto.get('hash'))
    if holder is result and not top_changed:
        raise autoupdate_error('autoupdate.url')
    problems = unresolved({key: value for key, value in result.items() if key not in ('autoupdate', 'checkver')})
    if problems:
        raise autoupdate_error('checkver: ' + ', '.join('$' + p for p in problems))
    urls = holder['url'] if isinstance(holder['url'], list) else [holder['url']]
    specs = spec if isinstance(spec, list) else [spec] * len(urls)
    hashes = []
    for url, current in zip(urls, specs):
        digest, reason = hash_from_spec(root, current, url, variables) if current is not None else (None, 'autoupdate.hash')
        if reason:
            log(tr('{p1}: no se pudo obtener el hash ({p3}). La descarga quedará sin hash y pedirá confirmación.', p1=remote_basename(url), p3=reason))
        hashes.append(digest)
    if all(hashes):
        holder['hash'] = hashes if isinstance(holder['url'], list) else hashes[0]
    return result


def resolve(root, catalog, key, version, found=None, log=print):
    """Manifiesto de bucket/app en esa versión: histórico Git primero, autoupdate después.
    Devuelve (manifiesto, retención) donde retención describe el origen."""
    version = validate_version(version)
    _, current = catalog.resolve(key)
    if str(current.get('version')) == version:
        return current, {'version': version, 'source': 'catalog'}
    if found is None:
        try:
            found = history(root, catalog, key)
        except Exception as exc:
            log(tr('No se pudo consultar el histórico del bucket: {p1}', p1=exc))
    if found and found.get('commits'):
        manifest, commit = manifest_from_history(root, found, version)
        if manifest is not None:
            return manifest, {'version': version, 'source': 'git', 'commit': commit['sha']}
    log(tr('{p1} {p3} no está en el histórico; se genera desde autoupdate.', p1=key, p3=version))
    return manifest_from_autoupdate(root, current, version, log), {'version': version, 'source': 'autoupdate'}
