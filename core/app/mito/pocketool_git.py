"""Explicit authoring operations in the user's Pocketool repository."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from .i18n import tr
from .network import proxy_environment, validate_url
from .pocketools import git_executable
from .storage import YitoError, inside, mutation, portable_path, rename_released


class Git:
    def __init__(self, engine, folder):
        self.folder = Path(folder)
        self.environment = proxy_environment(engine.root, dict(os.environ))
        for key in list(self.environment):
            if key.upper().startswith('GIT_'):
                self.environment.pop(key)
        self.environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never', GIT_ATTR_NOSYSTEM='1')
        empty = engine.root / 'core/state/pocketool-git-empty'
        empty.mkdir(parents=True, exist_ok=True)
        self.argv = [git_executable(engine), '--literal-pathspecs',
            '-c', f'core.hooksPath={empty}', '-c', 'core.fsmonitor=false',
            '-c', 'submodule.recurse=false', '-c', 'commit.gpgSign=false',
            '-c', 'protocol.allow=never', '-c', 'protocol.http.allow=always',
            '-c', 'protocol.https.allow=always']
        # Git authoring must not run configured clean/process filters either.
        filters = self.run(['config', '--name-only', '--get-regexp',
                            r'^filter\..*\.(clean|smudge|process|required)$'], optional=True)
        for key in filters.decode('utf-8', 'replace').splitlines():
            self.argv += ['-c', key + ('=false' if key.endswith('.required') else '=')]

    def run(self, arguments, optional=False):
        try:
            result = subprocess.run([*self.argv, *arguments], cwd=self.folder,
                env=self.environment, capture_output=True, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise YitoError(tr('Git agotó el tiempo de sincronización')) from exc
        if result.returncode and not (optional and result.returncode == 1):
            detail = (result.stderr or result.stdout).decode('utf-8', 'replace').strip()[-3000:]
            raise YitoError('Git: ' + detail)
        return result.stdout

    def text(self, arguments):
        return self.run(arguments).decode('utf-8', 'strict').strip()


def repository(engine, folder):
    git = Git(engine, folder)
    top = Path(git.text(['rev-parse', '--show-toplevel'])).resolve()
    if top != Path(folder).resolve():
        raise YitoError(tr('Selecciona la raíz del repositorio Git.'))
    return git


def initialize(engine, folder, url=''):
    if url:
        validate_url(url)
    git = Git(engine, folder)
    git.run(['init', '--initial-branch=main', '--template=', '.'])
    if url:
        git.run(['remote', 'add', 'origin', url])


def clone(engine, url, destination):
    """Materialize regular blobs, without checkout, filters, hooks or submodules."""
    validate_url(url)
    destination = Path(destination)
    if destination.exists():
        raise YitoError(tr('Para clonar, elige una carpeta que todavía no exista.'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.mito-clone-') as tmp:
        stage = Path(tmp)
        Git(engine, stage).run(['clone', '--no-checkout', '--no-recurse-submodules',
                              '--single-branch', '--template=', '--', url, str(stage / 'repo')])
        folder = stage / 'repo'
        git = Git(engine, folder)
        # Empty remotes have an unborn HEAD; they are valid new authoring repos.
        head = git.run(['rev-parse', '--verify', '--quiet', 'HEAD'], optional=True)
        if head:
            entries = git.run(['ls-tree', '-r', '-l', '-z', 'HEAD']).split(b'\0')
            total, seen = 0, set()
            if len(entries) > 10001:
                raise YitoError(tr('El repositorio excede los límites de un paquete de scripts.'))
            for entry in entries:
                if not entry:
                    continue
                metadata, encoded = entry.split(b'\t', 1)
                mode, kind, oid, size = metadata.split()
                relative = encoded.decode('utf-8', 'strict')
                portable_path(relative)
                if mode not in (b'100644', b'100755') or kind != b'blob' or any(
                        part.casefold() == '.git' for part in Path(relative).parts):
                    raise YitoError(tr('La pocketool contiene enlaces o submódulos no admitidos.'))
                if relative.casefold() in seen:
                    raise YitoError(tr('El repositorio contiene rutas repetidas en Windows.'))
                seen.add(relative.casefold())
                total += int(size)
                if int(size) > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
                    raise YitoError(tr('El repositorio excede los límites de un paquete de scripts.'))
                target = inside(folder, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git.run(['cat-file', 'blob', oid.decode('ascii')]))
            git.run(['read-tree', 'HEAD'])
        # Antivirus or EDR may hold the freshly written .git files for a moment.
        rename_released(folder, destination)


def file_stamp(folder, relative):
    portable_path(relative)
    if any(part.casefold() == '.git' for part in Path(relative).parts):
        raise YitoError(tr('No se puede incluir metadata de Git.'))
    path = inside(folder, relative)
    for item in (path, *path.parents):
        if item == folder:
            break
        if item.is_symlink() or item.is_junction():
            raise YitoError(tr('La pocketool contiene enlaces no admitidos'))
    if not path.exists():
        return None
    if not path.is_file():
        raise YitoError(tr('Selecciona archivos normales del paquete.'))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def review(engine, folder):
    git = repository(engine, folder)
    branch = git.text(['symbolic-ref', '--quiet', '--short', 'HEAD'])
    for state in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
        path = Path(git.text(['rev-parse', '--git-path', state]))
        if not path.is_absolute():
            path = folder / path
        if path.exists():
            raise YitoError(tr('Termina la operación pendiente de Git antes de guardar un commit.'))
    status = git.run(['status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames'])
    changes = []
    for entry in status.split(b'\0'):
        if not entry:
            continue
        state, relative = entry[:2].decode('ascii'), entry[3:].decode('utf-8')
        if 'U' in state or state in ('AA', 'DD'):
            raise YitoError(tr('Resuelve los conflictos de Git antes de crear el commit.'))
        changes.append({'path': relative, 'status': state, 'stamp': file_stamp(folder, relative)})
    remotes = git.text(['remote']).splitlines()
    url = git.text(['remote', 'get-url', 'origin']) if 'origin' in remotes else ''
    head = git.run(['rev-parse', '--verify', '--quiet', 'HEAD'], optional=True).decode().strip()
    return {'branch': branch, 'head': head, 'url': url, 'changes': changes}


def commit(engine, folder, reviewed, selected, message, url='', push=False):
    """Commit exactly reviewed paths; pre-existing staged work is never swept in."""
    if not message.strip() or '\0' in message:
        raise YitoError(tr('Introduce el mensaje del commit.'))
    if push:
        validate_url(url)
    chosen = sorted(set(selected))
    with mutation(engine.root, 'pocketool-author.lock'):
        current = review(engine, folder)
        if current != reviewed:
            raise YitoError(tr('El repositorio cambió desde la revisión. Vuelve a abrir Git.'))
        available = {item['path'] for item in current['changes']}
        if not set(chosen).issubset(available) or (not chosen and not push):
            raise YitoError(tr('Selecciona los archivos que quieres incluir en el commit.'))
        git = repository(engine, folder)
        if url:
            validate_url(url)
            if current['url'] and current['url'] != url:
                raise YitoError(tr('La URL no coincide con origin. Cambia el remoto desde Git.'))
            if not current['url']:
                git.run(['remote', 'add', 'origin', url])
        if chosen:
            git.run(['add', '--', *chosen])
            git.run(['commit', '--only', '-m', message.strip(), '--', *chosen])
        result = {'committed': bool(chosen), 'pushed': False, 'push_error': None}
        if push:
            try:
                # Explicit reviewed URL avoids a hidden pushurl choosing another destination.
                git.run(['push', '--', url, 'HEAD:refs/heads/' + current['branch']])
                result['pushed'] = True
            except (YitoError, OSError) as exc:
                result['push_error'] = str(exc)
        return result
