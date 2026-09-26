"""Read WiX 3 Burn v2 attached CABs as data; never execute the bundle.

Format reference: wixtoolset/wix3 src/tools/wix/BurnCommon.cs and BurnReader.cs.
"""
from pathlib import Path
import filecmp
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET

from .storage import YitoError, inside, portable_path
from .transfer import MAX_EXPANDED, hash_verifier

NS = '{http://schemas.microsoft.com/wix/2008/Burn}'


def containers(stream):
    stream.seek(0, 2)
    length = stream.tell()

    def read(offset, size):
        if offset < 0 or size < 0 or offset + size > length:
            raise YitoError('Burn: truncated or invalid container range')
        stream.seek(offset)
        data = stream.read(size)
        if len(data) != size:
            raise YitoError('Burn: truncated header')
        return data

    dos = read(0, 64)
    if dos[:2] != b'MZ':
        raise YitoError('Burn: missing DOS header')
    pe = struct.unpack_from('<I', dos, 60)[0]
    header = read(pe, 24)
    if header[:4] != b'PE\0\0':
        raise YitoError('Burn: missing PE header')
    count = struct.unpack_from('<H', header, 6)[0]
    optional = struct.unpack_from('<H', header, 20)[0]
    if not 1 <= count <= 96:
        raise YitoError('Burn: invalid PE section count')
    sections = read(pe + 24 + optional, count * 40)
    matches = [sections[i:i+40] for i in range(0, len(sections), 40)
               if sections[i:i+8] == b'.wixburn']
    if len(matches) != 1:
        raise YitoError('Burn: missing or duplicate .wixburn section')
    raw_size, offset = struct.unpack_from('<II', matches[0], 16)
    if raw_size < 56:
        raise YitoError('Burn: short section')
    data = read(offset, 56)
    magic, version = struct.unpack_from('<II', data)
    stub, _, signature, signature_size, kind, count, ux, attached = struct.unpack_from('<8I', data, 24)
    if magic != 0x00F14300 or version != 2 or kind != 1 or count != 2:
        raise YitoError('Burn: only v2 bundles with UX and one attached CAB are supported')
    start = signature + signature_size if signature else stub + ux
    if stub < offset + 56 or start < stub + ux or not ux or not attached:
        raise YitoError('Burn: invalid or overlapping containers')
    result = [(stub, ux), (start, attached)]
    for position, size in result:
        if size > MAX_EXPANDED or position + size > length:
            raise YitoError('Burn: truncated or oversized CAB')
        cab = read(position, 36)
        if (cab[:4] != b'MSCF' or struct.unpack_from('<I', cab, 8)[0] != size
                or struct.unpack_from('<H', cab, 30)[0] & 3):
            raise YitoError('Burn: invalid or spanning CAB')
    return result


def inventory(manifest, *, log=lambda _: None):
    if len(manifest) > 16 * 1024**2:
        raise YitoError('Burn: oversized manifest')
    try:
        text = manifest.decode('utf-8-sig')
    except UnicodeError as exc:
        raise YitoError('Burn: manifest must be UTF-8') from exc
    if '\0' in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise YitoError('Burn: unsupported XML declaration or oversized manifest')
    try:
        document = ET.fromstring(text)
    except ET.ParseError as exc:
        raise YitoError('Burn: invalid manifest XML') from exc
    if document.tag != NS + 'BurnManifest':
        raise YitoError('Burn: unsupported manifest namespace')
    groups = [document.findall(f'{NS}UX/{NS}Payload'), document.findall(f'{NS}Payload')]
    if sum(map(len, groups)) > 150000:
        raise YitoError('Burn: oversized payload inventory')
    plans, total, external = [], 0, 0
    for group_index, group in enumerate(groups):
        plan, names, ids = [], set(), set()
        for entry in group:
            attributes = entry.attrib
            packaging = attributes.get('Packaging', 'embedded').lower()
            # Like WiX's BurnReader, extract only attached/embedded payloads.
            # External entries describe files outside the CAB: never resolve
            # their SourcePath/DownloadUrl, nor include them in its inventory.
            if packaging == 'external' and group_index == 1:
                external += 1
                continue
            if packaging != 'embedded':
                raise YitoError('Burn: unsupported payload packaging')
            source, target = attributes.get('SourcePath', ''), attributes.get('FilePath', '')
            if not source or not target or len(portable_path(source).parts) != 1:
                raise YitoError('Burn: invalid payload source')
            path = portable_path(target)
            folded = path.as_posix().casefold()
            if not path.parts:
                raise YitoError(f'Burn: empty payload destination for {source}')
            if source.casefold() in ids:
                raise YitoError(f'Burn: duplicate payload source: {source}')
            # Multiple package scopes can carry separate copies of the same
            # destination. Keep every source for validation; merge only after
            # comparing the extracted bytes, never based on metadata alone.
            names.add(folded)
            ids.add(source.casefold())
            try:
                size = int(attributes['FileSize']) if 'FileSize' in attributes else None
            except ValueError as exc:
                raise YitoError('Burn: invalid payload size') from exc
            if size is not None:
                if size < 0:
                    raise YitoError('Burn: invalid payload size')
                total += size
            digest = attributes.get('Hash')
            hash_verifier(digest)
            plan.append((source, path.as_posix(), size, digest))
        for _, target, _, _ in plan:
            if any(parent.as_posix().casefold() in names for parent in portable_path(target).parents):
                raise YitoError('Burn: file/directory collision')
        plans.append(plan)
    if sum(map(len, plans)) > 150000 or total > MAX_EXPANDED:
        raise YitoError('Burn: oversized payload inventory')
    if external:
        log(f'Burn: {external} external payload reference(s) omitted; extracting embedded files only')
    return plans


def extract_burn(archive, destination, root, *, log=lambda _: None):
    from .archives import extract_7zip
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open('rb') as stream, tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        scratch = Path(temporary)
        ranges = containers(stream)
        expanded = []
        remaining = MAX_EXPANDED
        for index, (offset, size) in enumerate(ranges):
            cabinet = scratch / f'{index}.cab'
            stream.seek(offset)
            with cabinet.open('wb') as output:
                left = size
                while left:
                    chunk = stream.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise YitoError('Burn: truncated CAB')
                    output.write(chunk)
                    left -= len(chunk)
            folder = scratch / str(index)
            extract_7zip(cabinet, folder, root, max_expanded=remaining)
            remaining -= sum(path.stat().st_size for path in folder.rglob('*') if path.is_file())
            if remaining < 0:
                raise YitoError('Burn: oversized containers')
            expanded.append(folder)
        manifest = expanded[0] / '0'
        if not manifest.is_file() or manifest.stat().st_size > 16 * 1024**2:
            raise YitoError('Burn: missing or oversized manifest')
        plans = inventory(manifest.read_bytes(), log=log)
        copies = [(manifest, 'UX/manifest.xml')]
        destinations = {'ux/manifest.xml': manifest}
        for index, (folder, plan) in enumerate(zip(expanded, plans)):
            expected = {source.casefold() for source, _, _, _ in plan}
            if index == 0:
                expected.add('0')
            actual = {path.name.casefold() for path in folder.iterdir() if path.is_file()}
            if actual != expected or any(path.is_dir() for path in folder.iterdir()):
                raise YitoError('Burn: CAB contents do not match manifest')
            for source, target, size, digest in plan:
                file = inside(folder, source)
                if size is not None and file.stat().st_size != size:
                    raise YitoError('Burn: payload size mismatch')
                hasher, expected_hash = hash_verifier(digest)
                if hasher:
                    with file.open('rb') as data:
                        while chunk := data.read(1024 * 1024):
                            hasher.update(chunk)
                    if hasher.hexdigest().lower() != expected_hash:
                        raise YitoError('Burn: payload hash mismatch')
                relative = ('UX/' if index == 0 else 'AttachedContainer/') + target
                if relative.casefold() == 'ux/manifest.xml':
                    raise YitoError('Burn: reserved manifest destination')
                previous = destinations.get(relative.casefold())
                if previous is not None:
                    if not filecmp.cmp(previous, file, shallow=False):
                        raise YitoError(f'Burn: conflicting payloads for destination: {relative}')
                    log(f'Burn: identical payload merged: {relative}')
                    continue
                destinations[relative.casefold()] = file
                copies.append((file, relative))
        # Publish to the caller's scratch tree only after all validation succeeds.
        for source, relative in copies:
            target = inside(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
