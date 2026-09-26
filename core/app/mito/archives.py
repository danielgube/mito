"""Archive extraction, never executing a downloaded installer."""
from .i18n import tr
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile
from .storage import YitoError, inside
from .transfer import MAX_EXPANDED, extract_zip


def extract(archive, destination, root, filename, *, log=lambda _: None, innosetup=False):
    """Prepare a download; return True for extraction, False for a loose file."""
    log(tr('Extrayendo {p1}', p1=filename))
    if innosetup and Path(filename).suffix.lower() == '.exe':
        from .inno import extract_inno
        extract_inno(archive, destination, root)
        return True
    # JARs are ZIP containers but must remain executable Java artifacts.
    # An explicit #/archive.zip alias still requests ordinary extraction.
    if Path(filename).suffix.lower() in ('.jar', '.ico'):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, inside(destination, filename))
        return False
    if Path(filename).suffix.lower() == '.msi':
        from .msi import extract_msi
        extract_msi(archive, destination, root, log=log)
        return True
    if zipfile.is_zipfile(archive):
        try:
            extract_zip(archive, destination)
        except NotImplementedError:
            # ZIP paths, sizes and links were validated before reading any member.
            # Some ZIP methods (e.g. Deflate64) require the bundled extractor.
            extract_7zip(archive, destination, root)
        return True
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if len(members) > 150000 or sum(x.size for x in members) > MAX_EXPANDED:
                raise YitoError(tr('TAR demasiado grande'))
            seen = set()
            for member in members:
                target = inside(destination, member.name)
                if not (member.isfile() or member.isdir()) or str(target).casefold() in seen:
                    raise YitoError(tr('TAR con enlaces, dispositivos o entradas duplicadas'))
                seen.add(str(target).casefold())
            for member in members:
                target = inside(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as inp, target.open('wb') as out:
                        shutil.copyfileobj(inp, out)
        return True
    suffix = Path(filename).suffix.lower()
    with archive.open('rb') as source:
        signature = source.read(8)
    if suffix in ('.7z', '.rar') or (not suffix and signature.startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07'))):
        extract_7zip(archive, destination, root)
        return True
    if suffix in ('', '.exe', '.dll', '.jar', '.ps1', '.cmd', '.bat', '.com'):
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, inside(destination, filename))
        return False
    raise YitoError(tr('Formato de archivo todavía no soportado: {p1}', p1=(filename)))


def extract_7zip(archive, destination, root, *, max_expanded=MAX_EXPANDED, max_entries=150000):
    executable = root / 'core/tools/7zip/7z.exe'
    if not executable.is_file():
        raise YitoError(tr('Falta el extractor privado 7-Zip; ejecuta bootstrap'))
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}
    result = subprocess.run([str(executable), 'l', '-slt', '-ba', '-sccUTF-8', str(archive)], capture_output=True, text=True, encoding='utf-8', check=True, **flags)
    records = []
    record = {}
    for line in result.stdout.splitlines() + ['']:
        if not line.strip():
            if record:
                records.append(record)
            record = {}
        elif ' = ' in line:
            key, value = line.split(' = ', 1)
            record[key] = value
    total, seen = 0, set()
    for entry in records:
        if 'Path' not in entry:
            raise YitoError(tr('Listado 7z no reconocido'))
        target = inside(destination, entry['Path'])
        if str(target).casefold() in seen or any(entry.get(k) not in (None, '', '-') for k in ('Symbolic Link', 'Hard Link')) or 'l' in entry.get('Attributes', ''):
            raise YitoError(tr('7z con enlaces o entradas duplicadas'))
        if entry.get('Encrypted') == '+':
            raise YitoError(tr('No se admiten archivos cifrados'))
        seen.add(str(target).casefold())
        total += int(entry.get('Size', '0') or 0)
    if len(records) > max_entries or total > max_expanded:
        raise YitoError(tr('7z demasiado grande'))
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(executable), 'x', str(archive), f'-o{destination}', '-y', '-bd'], capture_output=True, check=True, **flags)
    for path in destination.rglob('*'):
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(destination.resolve()):
            raise YitoError(tr('Enlace inesperado tras extraer 7z'))
    return
