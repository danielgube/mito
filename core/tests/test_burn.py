import hashlib
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito.burn import containers, inventory, extract_burn, NS
from mito.hooks import expand_installer
from mito.storage import YitoError


def bundle(signed=False):
    data = bytearray(2048)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<H', data, 134, 1)
    struct.pack_into('<H', data, 148, 224)
    data[376:384] = b'.wixburn'
    struct.pack_into('<II', data, 392, 512, 512)
    struct.pack_into('<II', data, 512, 0x00F14300, 2)
    struct.pack_into('<8I', data, 536, 1024, 0, 1124 if signed else 0,
                     20 if signed else 0, 1, 2, 100, 100)
    for position in (1024, 1144 if signed else 1124):
        data[position:position+4] = b'MSCF'
        struct.pack_into('<I', data, position+8, 100)
    return bytes(data)


def manifest(target='nested/tool.msi', **changes):
    attributes = dict(SourcePath='a0', FilePath=target, Packaging='embedded',
                      FileSize='4', Hash=hashlib.sha1(b'tool').hexdigest())
    attributes.update(changes)
    pairs = ' '.join(f'{key}="{value}"' for key, value in attributes.items())
    return f'<BurnManifest xmlns="{NS[1:-1]}"><UX/><Payload {pairs}/></BurnManifest>'.encode()


def mixed_manifest():
    return manifest().replace(b'</BurnManifest>',
        b'<Payload Packaging="external" SourcePath="remote/debug.msi" '
        b'FilePath="debug.msi" DownloadUrl="https://example.invalid/debug.msi"/>'
        b'</BurnManifest>')


class BurnTests(unittest.TestCase):
    def test_extracts_real_cabs_using_private_7zip(self):
        runtime = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'0').write_bytes(mixed_manifest())
            (root/'a0').write_bytes(b'tool')
            cabinets = []
            for name in ('0', 'a0'):
                target = root/f'{name}.cab'
                subprocess.run(['makecab.exe', str(root/name), str(target)],
                               check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                cabinets.append(target.read_bytes())
            data = bytearray(bundle()[:1024])
            struct.pack_into('<II', data, 560, *(len(cab) for cab in cabinets))
            archive = root/'setup.exe'
            archive.write_bytes(data + b''.join(cabinets))
            extract_burn(archive, root/'output', runtime)
            self.assertEqual((root/'output/AttachedContainer/nested/tool.msi').read_bytes(), b'tool')

    def test_reads_container_offsets_with_and_without_engine_signature(self):
        for signed in (False, True):
            self.assertEqual(containers(io.BytesIO(bundle(signed))),
                             [(1024, 100), (1144 if signed else 1124, 100)])

    def test_rejects_truncated_unsupported_overlapping_and_spanning_containers(self):
        for offset, value in [(516, 3), (560, 0), (552, 2), (556, 3),
                              (544, 1), (1054, 1), (564, 999999)]:
            data = bytearray(bundle())
            struct.pack_into('<I', data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(YitoError):
                containers(io.BytesIO(data))
        with self.assertRaises(YitoError):
            containers(io.BytesIO(bundle()[:1150]))

    def test_rejects_unsafe_duplicate_unknown_packaging_and_xml_payloads(self):
        invalid = [manifest('../escape'), manifest('C:/escape'), manifest('NUL.txt'),
                   manifest(SourcePath='../a0'), manifest(Packaging='unknown'),
                   manifest().replace(b'<UX/>', b'<UX><Payload Packaging="external"/></UX>'),
                   manifest(FileSize='-1'), manifest(Hash='bad'),
                   manifest().replace(b'<UX/>', b'<UX/><Payload SourcePath="a0" FilePath="other.msi"/>'),
                   manifest().replace(b'<UX/>', b'<UX/><Payload SourcePath="a1" FilePath="nested/tool.msi/child"/>'),
                   b'<!DOCTYPE x [<!ENTITY e "test">]>' + manifest()]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(YitoError):
                inventory(data)

    def test_external_references_are_excluded_from_embedded_inventory_and_logged(self):
        messages = []
        self.assertEqual(inventory(mixed_manifest(), log=messages.append), inventory(manifest()))
        self.assertEqual(len(messages), 1)
        self.assertIn('1 external payload reference(s) omitted', messages[0])

    def test_repeated_destinations_require_identical_extracted_bytes(self):
        for second, declared_hash in [(b'tool', True), (b'evil', True), (b'evil', False)]:
            with self.subTest(second=second, declared_hash=declared_hash), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root/'setup.exe'
                archive.write_bytes(bundle())
                # Different valid hashes must not allow different bytes to
                # overwrite a common target. Missing hashes must not allow it either.
                digest = f' Hash="{hashlib.sha1(second).hexdigest()}"' if declared_hash else ''
                duplicate = (f'<Payload SourcePath="a1" FilePath="NESTED/TOOL.MSI" '
                             f'Packaging="embedded" FileSize="4"{digest}/>').encode()
                xml = manifest().replace(b'</BurnManifest>', duplicate + b'</BurnManifest>')
                self.assertEqual(len(inventory(xml)[1]), 2)
                def extract(cabinet, destination, runtime, **kwargs):
                    destination.mkdir()
                    if cabinet.stem == '0':
                        (destination/'0').write_bytes(xml)
                    else:
                        (destination/'a0').write_bytes(b'tool')
                        (destination/'a1').write_bytes(second)
                with patch('mito.archives.extract_7zip', side_effect=extract):
                    if second == b'tool':
                        messages = []
                        extract_burn(archive, root/'output', root, log=messages.append)
                        files = list((root/'output/AttachedContainer').rglob('*.msi'))
                        self.assertEqual(len(files), 1)
                        self.assertEqual(files[0].read_bytes(), b'tool')
                        self.assertTrue(any('identical payload merged' in item for item in messages))
                    else:
                        with self.assertRaisesRegex(YitoError, 'conflicting payloads.*TOOL.MSI'):
                            extract_burn(archive, root/'output', root)
                        self.assertFalse((root/'output').exists())

    def test_all_payloads_verified_before_output_is_written(self):
        for content, extra in [(b'tool', False), (b'evil', False), (b'tool', True), (None, False)]:
            with self.subTest(content=content, extra=extra), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root/'setup.exe'
                archive.write_bytes(bundle())
                def extract(cabinet, destination, runtime, **kwargs):
                    destination.mkdir()
                    if cabinet.stem == '0':
                        (destination/'0').write_bytes(mixed_manifest())
                    else:
                        if content is not None:
                            (destination/'a0').write_bytes(content)
                        if extra:
                            (destination/'unknown').write_bytes(b'extra')
                with patch('mito.archives.extract_7zip', side_effect=extract), \
                        patch('mito.transfer.download', side_effect=AssertionError('external download')) as download:
                    if content == b'tool' and not extra:
                        extract_burn(archive, root/'output', root)
                        self.assertEqual((root/'output/AttachedContainer/nested/tool.msi').read_bytes(), b'tool')
                        self.assertFalse((root/'output/AttachedContainer/debug.msi').exists())
                    else:
                        with self.assertRaises(YitoError):
                            extract_burn(archive, root/'output', root)
                        self.assertFalse((root/'output').exists())
                    download.assert_not_called()

    def test_hook_helpers_are_confined_and_msi_uses_existing_extractor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            payload = root/'payload'
            payload.mkdir()
            archive = payload/'tool.msi'
            archive.write_bytes(b'inert')
            def extract(source, destination, runtime, **kwargs):
                (destination/'tool.exe').write_bytes(b'tool')
            with patch('mito.msi.extract_msi', side_effect=extract) as extractor:
                expand_installer('msi', root, payload, archive, payload)
                self.assertEqual((payload/'tool.exe').read_bytes(), b'tool')
                self.assertEqual(extractor.call_count, 1)
            for kind in ('msi', 'dark'):
                with self.assertRaises(YitoError):
                    expand_installer(kind, root, payload, archive, root/'outside')
