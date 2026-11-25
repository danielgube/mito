"""Extract Inno {app} (or {code_*}) files as data using the pinned private innounp binary."""
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
import hashlib

from .i18n import tr
from .storage import YitoError, inside, portable_path
from .transfer import MAX_EXPANDED


APP = '{app}'


def inno_root(extract_dir):
    """Inno root selected by a bare constant in extract_dir, as Scoop does with innounp -c.

    {app} is the default. Installers whose DefaultDirName comes from their script
    ({code:Func}) list files under {code_Func}; that root is equally the install
    folder. Built-in system constants ({sys}, {win}, {tmp}, …) stay unsupported."""
    if isinstance(extract_dir, str) and (extract_dir.casefold() == APP
                                         or re.fullmatch(r'\{code_[A-Za-z_][A-Za-z0-9_]*\}', extract_dir)):
        return extract_dir
    return None


def restore_declared_targets(payload, targets):
    """Restore missing declared names only when numbered copies are identical."""
    for relative in dict.fromkeys(targets):
        target = inside(payload, relative)
        if target.exists() or not target.parent.is_dir():
            continue
        pattern = re.compile(re.escape(target.stem) + r',([1-9]\d*)' + re.escape(target.suffix), re.I)
        copies = []
        for path in target.parent.iterdir():
            match = pattern.fullmatch(path.name)
            if match and path.is_file() and not path.is_symlink():
                copies.append((int(match[1]), path))
        copies.sort()
        if len(copies) < 2 or [n for n, _ in copies] != list(range(1, len(copies) + 1)):
            continue
        hashes = set()
        for _, path in copies:
            with path.open('rb') as stream:
                hashes.add(hashlib.file_digest(stream, 'sha256').digest())
        if len(hashes) != 1:
            raise YitoError(f'Inno: ambiguous variants for {relative}')
        shutil.copy2(copies[0][1], target)


def app_inventory(listing, *, root=APP, max_entries=150000, max_expanded=MAX_EXPANDED):
    """Parse innounp 2.71's non-regional UTF-8 verbose table, failing closed.
    Only files under the selected root ({app} by default) are returned."""
    entries = {}
    all_paths = set()
    directories = set()
    started = ended = False
    total = count = 0
    # DateTimeString emits minutes; ExtSp pads the 16-character date to 19,
    # followed by two separator spaces. Keep filename whitespace intact.
    row = re.compile(r'^\s*(\d+)\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2} {5}(.+)$')
    for line in listing.splitlines():
        if not started:
            if line.startswith('Size') and 'Filename' in line:
                started = True
            continue
        if line.startswith('---'):
            if count:
                ended = True
                break
            continue
        match = row.fullmatch(line)
        if not match:
            raise YitoError('Inno: unrecognized file listing')
        size, name = int(match[1]), match[2]
        path = portable_path(name)
        if not path.parts or str(path) != name.replace('/', '\\'):
            raise YitoError('Inno: non-canonical file path')
        folded = str(path).casefold()
        parents = {str(parent).casefold() for parent in path.parents if str(parent) != '.'}
        if folded in all_paths or folded in directories or parents & all_paths:
            raise YitoError('Inno: duplicate or overlapping file path')
        all_paths.add(folded)
        directories.update(parents)
        count += 1
        total += size
        if count > max_entries or total > max_expanded:
            raise YitoError('Inno: archive exceeds extraction limits')
        if path.parts[0].casefold() == root.casefold() and len(path.parts) > 1:
            relative = PureWindowsPath(*path.parts[1:]).as_posix()
            entries[relative.casefold()] = (relative, size)
    if not started or not ended or not entries:
        raise YitoError(f'Inno: no supported {root} payload or incomplete listing')
    return entries


def extract_inno(archive, destination, root, constant=APP):
    if inno_root(constant) is None:
        raise YitoError(f'Inno: unsupported root {constant}')
    executable = root/'core/tools/innounp/innounp.exe'
    if not executable.is_file():
        raise YitoError(tr('Herramienta incompleta: {p1}', p1='innounp'))
    flags = dict(creationflags=subprocess.CREATE_NO_WINDOW) if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}
    # Do not guess newer formats (-n), prompt (-b), use regional output or unpack
    # only one copy of shared file data (-a). Never invoke the setup executable.
    common = ['-b', '-n', '-o', '-h', '-u', '-a']
    def run(arguments, cwd):
        result = subprocess.run([str(executable), *arguments], cwd=cwd,
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding='utf-8', errors='strict', **flags)
        if result.returncode:
            raise YitoError(f'Inno: extraction tool failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}')
        return result.stdout
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        stage = Path(temporary)
        inventory = app_inventory(run(['-v', *common, str(archive)], stage), root=constant)
        output = stage/'files'
        output.mkdir()
        run(['-x', *common, f'-c{constant}', f'-d{output}', str(archive), constant + '\\*'], stage)
        actual = {}
        for path in output.rglob('*'):
            if path.is_symlink() or path.is_junction():
                raise YitoError('Inno: unmanaged link in extracted payload')
            relative = path.relative_to(output).as_posix()
            inside(output, relative)
            if path.is_file():
                folded = relative.casefold()
                if folded in actual:
                    raise YitoError('Inno: duplicate extracted file')
                actual[folded] = (path, path.stat().st_size)
        if {key: size for key, (_, size) in actual.items()} != {key: size for key, (_, size) in inventory.items()}:
            raise YitoError('Inno: extracted inventory differs from validated listing')
        destination.mkdir(parents=True, exist_ok=True)
        for relative, _ in inventory.values():
            target = inside(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(actual[relative.casefold()][0], target)
