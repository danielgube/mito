"""Read MSI databases and embedded cabinets as data; never run installation actions."""
import ctypes
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile

from .storage import YitoError, inside, portable_path
from .i18n import tr
from .transfer import MAX_EXPANDED

MAX_ENTRIES = 150000


class Database:
    """Small, read-only Windows Installer database API surface."""

    def __init__(self, path):
        self.api = ctypes.WinDLL('msi.dll', winmode=0x800)
        uint = ctypes.c_uint
        pointer = ctypes.POINTER(uint)
        signatures = {
            'MsiOpenDatabaseW': [ctypes.c_wchar_p, ctypes.c_void_p, pointer],
            'MsiDatabaseOpenViewW': [uint, ctypes.c_wchar_p, pointer],
            'MsiViewExecute': [uint, uint], 'MsiViewFetch': [uint, pointer],
            'MsiCloseHandle': [uint],
            'MsiRecordGetStringW': [uint, uint, ctypes.c_wchar_p, pointer],
            'MsiRecordReadStream': [uint, uint, ctypes.c_void_p, pointer],
            'MsiCreateRecord': [uint],
            'MsiRecordSetStringW': [uint, uint, ctypes.c_wchar_p],
        }
        for name, args in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, uint
        self.handle = uint()
        # MSIDBOPEN_READONLY = 0. Do not use MsiOpenPackage/DoAction/InstallProduct.
        self.check(self.api.MsiOpenDatabaseW(str(Path(path).resolve()), None, ctypes.byref(self.handle)))

    @staticmethod
    def check(status):
        if status:
            raise YitoError(f'MSI: Windows database error {status}')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.api.MsiCloseHandle(self.handle)

    @contextmanager
    def view(self, sql, parameter=None):
        handle = ctypes.c_uint()
        self.check(self.api.MsiDatabaseOpenViewW(self.handle, sql, ctypes.byref(handle)))
        record = 0
        try:
            if parameter is not None:
                record = self.api.MsiCreateRecord(1)
                if not record:
                    raise YitoError('MSI: cannot allocate record')
                self.check(self.api.MsiRecordSetStringW(record, 1, parameter))
            self.check(self.api.MsiViewExecute(handle, record))
            yield handle
        finally:
            if record:
                self.api.MsiCloseHandle(record)
            self.api.MsiCloseHandle(handle)

    def fetch(self, view):
        record = ctypes.c_uint()
        status = self.api.MsiViewFetch(view, ctypes.byref(record))
        if status == 259:  # ERROR_NO_MORE_ITEMS
            return None
        self.check(status)
        return record

    def rows(self, table, columns):
        sql = 'SELECT ' + ', '.join(f'`{c}`' for c in columns) + f' FROM `{table}`'
        rows = []
        with self.view(sql) as view:
            while (record := self.fetch(view)) is not None:
                try:
                    row = []
                    for field in range(1, len(columns) + 1):
                        size = ctypes.c_uint(1024)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        status = self.api.MsiRecordGetStringW(record, field, buffer, ctypes.byref(size))
                        if status == 234 and size.value < 65536:
                            size.value += 1
                            buffer = ctypes.create_unicode_buffer(size.value)
                            status = self.api.MsiRecordGetStringW(record, field, buffer, ctypes.byref(size))
                        self.check(status)
                        row.append(buffer.value)
                    rows.append(row)
                    if len(rows) > MAX_ENTRIES:
                        raise YitoError('MSI: too many table entries')
                finally:
                    self.api.MsiCloseHandle(record)
        return rows

    def stream(self, name, target):
        with self.view('SELECT `Data` FROM `_Streams` WHERE `Name` = ?', name) as view:
            record = self.fetch(view)
            if record is None:
                raise YitoError(f'MSI: missing embedded cabinet {name}')
            try:
                buffer = ctypes.create_string_buffer(1024 * 1024)
                total = 0
                with target.open('wb') as output:
                    while True:
                        size = ctypes.c_uint(len(buffer))
                        self.check(self.api.MsiRecordReadStream(record, 1, buffer, ctypes.byref(size)))
                        if not size.value:
                            break
                        total += size.value
                        if total > MAX_EXPANDED:
                            raise YitoError('MSI: cabinet too large')
                        output.write(buffer.raw[:size.value])
            finally:
                self.api.MsiCloseHandle(record)


def leaf(value, directory=False):
    # Administrative images use source names, including target:source pairs.
    if directory:
        value = value.split(':', 1)[-1]
    value = value.split('|')[-1]
    if directory and value == '.':
        return ''
    path = portable_path(value)
    if not value or len(path.parts) != 1 or value in ('.', '..') or '/' in value or '\\' in value:
        raise YitoError(f'MSI: invalid name {value}')
    return value


def plan(database, destination):
    directories = {key: (parent, name) for key, parent, name in
                   database.rows('Directory', ['Directory', 'Directory_Parent', 'DefaultDir'])}
    resolved = {}
    for key in directories:
        chain, seen, current = [], set(), key
        while current not in resolved:
            if current in seen or current not in directories:
                raise YitoError('MSI: cyclic or missing directory')
            seen.add(current)
            parent, name = directories[current]
            if not parent or parent == current:
                if current != 'TARGETDIR':
                    raise YitoError('MSI: unsupported root directory')
                resolved[current] = Path('.')
                break
            chain.append((current, leaf(name, directory=True)))
            current = parent
        path = resolved[current]
        for current, name in reversed(chain):
            path = path / name
            portable_path(path.as_posix())
            resolved[current] = path
    components = dict(database.rows('Component', ['Component', 'Directory_']))
    media = sorted((int(disk), int(last), cabinet) for disk, last, cabinet in
                   database.rows('Media', ['DiskId', 'LastSequence', 'Cabinet']))
    previous, cabinets = 0, []
    for disk, last, cabinet in media:
        if last < previous or not cabinet.startswith('#') or len(cabinet) < 2:
            raise YitoError('MSI: only embedded cabinets are supported (no external media)')
        cabinets.append((previous, last, cabinet[1:]))
        previous = last
    files, targets, ids, sequences, total = [], set(), set(), set(), 0
    for key, component, name, size, attributes, sequence in database.rows(
            'File', ['File', 'Component_', 'FileName', 'FileSize', 'Attributes', 'Sequence']):
        size, sequence = int(size), int(sequence)
        if size < 0 or sequence < 1 or int(attributes or 0) & 0x2000:
            raise YitoError('MSI: unsupported uncompressed file or invalid size/sequence')
        leaf(key)
        if component not in components or components[component] not in resolved:
            raise YitoError('MSI: missing file component/directory')
        relative = resolved[components[component]] / leaf(name)
        portable_path(relative.as_posix())
        folded = relative.as_posix().casefold()
        if folded in targets or key.casefold() in ids or sequence in sequences:
            raise YitoError('MSI: duplicate file, sequence or destination')
        targets.add(folded)
        ids.add(key.casefold())
        sequences.add(sequence)
        total += size
        cabinet = next((i for i, (low, high, _) in enumerate(cabinets) if low < sequence <= high), None)
        if cabinet is None:
            raise YitoError('MSI: file has no embedded cabinet')
        files.append((cabinet, key, relative, size))
    if not files or total > MAX_EXPANDED:
        raise YitoError('MSI: empty or oversized payload')
    for _, _, relative, _ in files:
        if any(p.as_posix().casefold() in targets
               for p in relative.parents):
            raise YitoError('MSI: file/directory collision')
    return cabinets, files


def extract_msi(archive, destination, root, *, log=lambda _: None):
    from .archives import extract_7zip
    # All scratch output is private and disposable; publication belongs to Engine.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Database(archive) as database, tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        scratch = Path(tmp)
        cabinets, files = plan(database, destination)
        completed = 0
        for index, (_, _, name) in enumerate(cabinets):
            log(tr('MSI: CAB {p1}/{p2}', p1=index + 1, p2=len(cabinets)))
            cabinet = scratch / 'payload.cab'
            expanded = scratch / 'expanded'
            database.stream(name, cabinet)
            # Reject spanning cabinets: they could request another file from disk.
            with cabinet.open('rb') as source:
                header = source.read(36)
            if len(header) != 36 or header[:4] != b'MSCF' or int.from_bytes(header[30:32], 'little') & 3:
                raise YitoError('MSI: invalid or spanning cabinet is unsupported')
            expected = {key.casefold(): (relative, size) for cab, key, relative, size in files if cab == index}
            extract_7zip(cabinet, expanded, root,
                         max_expanded=sum(size for _, size in expected.values()),
                         max_entries=len(expected))
            actual = {p.name.casefold(): p for p in expanded.iterdir() if p.is_file()}
            if set(actual) != set(expected) or any(p.is_dir() for p in expanded.iterdir()):
                raise YitoError('MSI: cabinet contents do not match File table')
            for key, (relative, size) in expected.items():
                source = actual[key]
                if source.stat().st_size != size:
                    raise YitoError(f'MSI: file size mismatch {relative}')
                target = inside(destination, relative.as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                completed += 1
                if completed % 1000 == 0 or completed == len(files):
                    log(tr('Archivos {p1}: {p2}/{p3}', p1='MSI', p2=completed, p3=len(files)))
            shutil.rmtree(expanded)
            cabinet.unlink()
