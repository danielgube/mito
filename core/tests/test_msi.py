import ctypes
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mito.archives import extract
from mito.catalog import Catalog
from mito.components import remove
from mito.engine import Engine
from mito.msi import Database, plan
from mito.storage import YitoError, write_json


class Tables:
    def __init__(self):
        self.tables = {
            'Directory': [['TARGETDIR', '', 'SourceDir'], ['APP', 'TARGETDIR', 'TARGET:SHORT|Package'],
                          ['BIN', 'APP', 'bin']],
            'Component': [['comp', 'BIN']],
            'Media': [['1', '1', '#cab']],
            'File': [['payload', 'comp', 'TOOL.EXE|tool.exe', '7', '16384', '1']],
        }

    def rows(self, table, columns):
        return self.tables[table]


def create_fixture(folder, content=b'fixture', filename='tool.exe'):
    """Generate a real MSI/CAB locally, without installing a product or SDK."""
    source = folder / 'payload'
    source.write_bytes(content)
    cabinet = folder / 'fixture.cab'
    subprocess.run(['makecab.exe', '/D', 'CompressionType=MSZIP', str(source), str(cabinet)],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    api = ctypes.WinDLL('msi.dll', winmode=0x800)
    uint, pointer = ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)
    signatures = {
        'MsiOpenDatabaseW': [ctypes.c_wchar_p, ctypes.c_void_p, pointer],
        'MsiDatabaseOpenViewW': [uint, ctypes.c_wchar_p, pointer],
        'MsiViewExecute': [uint, uint], 'MsiCloseHandle': [uint],
        'MsiCreateRecord': [uint], 'MsiRecordSetStreamW': [uint, uint, ctypes.c_wchar_p],
        'MsiDatabaseCommit': [uint],
    }
    for name, args in signatures.items():
        getattr(api, name).argtypes, getattr(api, name).restype = args, uint
    path, handle = folder / 'fixture.msi', uint()
    Database.check(api.MsiOpenDatabaseW(str(path), 3, ctypes.byref(handle)))
    def sql(query, record=0):
        view = uint()
        Database.check(api.MsiDatabaseOpenViewW(handle, query, ctypes.byref(view)))
        try:
            Database.check(api.MsiViewExecute(view, record))
        finally:
            api.MsiCloseHandle(view)
    try:
        sql('CREATE TABLE `Directory` (`Directory` CHAR(72) NOT NULL, `Directory_Parent` CHAR(72), `DefaultDir` CHAR(255) NOT NULL PRIMARY KEY `Directory`)')
        sql('CREATE TABLE `Component` (`Component` CHAR(72) NOT NULL, `Directory_` CHAR(72) NOT NULL PRIMARY KEY `Component`)')
        sql('CREATE TABLE `Media` (`DiskId` SHORT NOT NULL, `LastSequence` SHORT NOT NULL, `Cabinet` CHAR(255) PRIMARY KEY `DiskId`)')
        sql('CREATE TABLE `File` (`File` CHAR(72) NOT NULL, `Component_` CHAR(72) NOT NULL, `FileName` CHAR(255) NOT NULL, `FileSize` LONG NOT NULL, `Attributes` SHORT, `Sequence` SHORT NOT NULL PRIMARY KEY `File`)')
        sql("INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES ('TARGETDIR', '', 'SourceDir')")
        sql("INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES ('APP', 'TARGETDIR', 'TARGET:SHORT|Package')")
        sql("INSERT INTO `Directory` (`Directory`, `Directory_Parent`, `DefaultDir`) VALUES ('BIN', 'APP', 'bin')")
        sql("INSERT INTO `Component` (`Component`, `Directory_`) VALUES ('comp', 'BIN')")
        sql("INSERT INTO `Media` (`DiskId`, `LastSequence`, `Cabinet`) VALUES (1, 1, '#cab')")
        sql(f"INSERT INTO `File` (`File`, `Component_`, `FileName`, `FileSize`, `Attributes`, `Sequence`) VALUES ('payload', 'comp', '{filename}', {len(content)}, 16384, 1)")
        # An extractor must ignore actions entirely. This action would fail an install.
        sql('CREATE TABLE `CustomAction` (`Action` CHAR(72) NOT NULL, `Type` SHORT NOT NULL, `Source` CHAR(72), `Target` CHAR(255) PRIMARY KEY `Action`)')
        sql("INSERT INTO `CustomAction` (`Action`, `Type`, `Source`, `Target`) VALUES ('MustNeverRun', 19, '', 'MITO must not execute MSI actions')")
        sql('CREATE TABLE `InstallExecuteSequence` (`Action` CHAR(72) NOT NULL, `Condition` CHAR(255), `Sequence` SHORT PRIMARY KEY `Action`)')
        sql("INSERT INTO `InstallExecuteSequence` (`Action`, `Condition`, `Sequence`) VALUES ('MustNeverRun', '', 1)")
        record = api.MsiCreateRecord(1)
        try:
            Database.check(api.MsiRecordSetStreamW(record, 1, str(cabinet)))
            sql("INSERT INTO `_Streams` (`Name`, `Data`) VALUES ('cab', ?)", record)
        finally:
            api.MsiCloseHandle(record)
        Database.check(api.MsiDatabaseCommit(handle))
    finally:
        api.MsiCloseHandle(handle)
    return path


class MsiTests(unittest.TestCase):
    def test_source_names_and_embedded_media_plan(self):
        cabinets, files = plan(Tables(), Path('unused'))
        self.assertEqual(cabinets, [(0, 1, 'cab')])
        self.assertEqual(files, [(0, 'payload', Path('Package/bin/tool.exe'), 7)])

    def test_invalid_paths_references_duplicates_and_media(self):
        changes = [
            ('Directory', 1, 2, '../escape'), ('Directory', 1, 2, 'C:\\escape'),
            ('Directory', 1, 1, 'BIN'), ('Directory', 1, 1, 'missing'),
            ('File', 0, 2, '..\\escape.exe'), ('File', 0, 2, 'aux.exe'),
            ('File', 0, 1, 'missing'), ('File', 0, 3, '-1'),
            ('File', 0, 4, '8192'), ('File', 0, 5, '2'),
            ('Media', 0, 2, 'external.cab'), ('Media', 0, 2, ''),
        ]
        for table, row, column, value in changes:
            with self.subTest(change=(table, column, value)), self.assertRaises((YitoError, ValueError)):
                db = Tables()
                db.tables[table][row][column] = value
                plan(db, Path('unused'))
        db = Tables()
        db.tables['File'].append(['other', 'comp', 'TOOL.EXE', '7', '16384', '2'])
        db.tables['Media'][0][1] = '2'
        with self.assertRaisesRegex(YitoError, 'duplicate'):
            plan(db, Path('unused'))
        with patch('mito.msi.MAX_EXPANDED', 1), self.assertRaises(YitoError):
            plan(Tables(), Path('unused'))

    def test_real_msi_lifecycle_and_failed_update_preserves_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root)
            engine.initialize()
            tools = root / 'core/tools/7zip'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'tools/7zip', tools)
            fixture = root / 'fixture'
            fixture.mkdir()
            msi = create_fixture(fixture)
            original_hash = hashlib.sha256(msi.read_bytes()).hexdigest()
            manifest = {'version': '1', 'url': 'https://example.org/download#/tool.MSI',
                        'hash': original_hash, 'extract_dir': 'Package', 'extract_to': 'app',
                        'bin': 'app/bin/tool.exe'}
            self.assertEqual(Catalog(root).compatibility('test/tool', manifest), 'Compatible')
            self.assertNotEqual(Catalog(root).compatibility('test/tool', dict(manifest, installer={'file': 'setup.exe'})), 'Compatible')
            write_json(root / 'core/buckets/test.json', {'apps': {'tool': manifest}})
            engine.save_profile('test', {'workspace': 'test', 'apps': [], 'env': {}})
            def download(url, target, expected, **kwargs):
                self.assertEqual(expected, original_hash)
                shutil.copyfile(msi, target)
            with patch('mito.engine.download', download), patch('mito.terminal.prepare'):
                logs = []
                engine.install('test/tool', log=logs.append)
                # The last download message must be followed by visible extraction
                # and preparation progress, before the installation succeeds.
                phases = ['Extrayendo tool.MSI', 'MSI: CAB 1/1', 'Archivos MSI: 1/1',
                          'Preparando test/tool', 'Archivos test/tool: 1/1', 'Validando test/tool']
                positions = [logs.index(phase) for phase in phases]
                self.assertEqual(positions, sorted(positions))
                self.assertFalse(engine.profile('test')['apps'])
                self.assertEqual(engine.installed()['test/tool']['version'], '1')
                target = root / 'core/apps/test/tool/app/bin/tool.exe'
                self.assertEqual(target.read_bytes(), b'fixture')
                engine.install('test/tool', 'test', log=lambda _: None)
                self.assertEqual(engine.profile('test')['apps'], ['test/tool'])
                self.assertEqual(hashlib.sha256(msi.read_bytes()).hexdigest(), original_hash)
                newer = dict(manifest, version='2', bin='missing.exe')
                write_json(root / 'core/buckets/test.json', {'apps': {'tool': newer}})
                with self.assertRaises(YitoError):
                    engine.install('test/tool', log=lambda _: None)
                self.assertEqual(target.read_bytes(), b'fixture')
                self.assertEqual(engine.installed()['test/tool']['version'], '1')
                write_json(root / 'core/buckets/test.json', {'apps': {'tool': dict(manifest, version='2')}})
                engine.install('test/tool', log=lambda _: None)
                self.assertEqual(engine.installed()['test/tool']['version'], '2')
                remove(engine, 'test/tool', log=lambda _: None)
                self.assertFalse(engine.installed())
                self.assertFalse(engine.profile('test')['apps'])

    def test_corrupt_msi_is_not_copied_as_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'download'
            archive.write_bytes(b'invalid MSI')
            with self.assertRaises(YitoError):
                extract(archive, root / 'out', root, 'tool.msi')
            self.assertFalse((root / 'out').exists())

    def test_cabinet_corruption_and_external_media_fail_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            msi = create_fixture(root)
            tools = root / 'core/tools/7zip'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'tools/7zip', tools)
            original = Database.rows
            for failure in ('external', 'missing', 'size', 'spanning'):
                destination = root / failure
                def rows(db, table, columns):
                    result = original(db, table, columns)
                    if table == 'Media' and failure == 'external':
                        result[0][2] = 'external.cab'
                    if table == 'File' and failure == 'missing':
                        result[0][0] = 'absent'
                    if table == 'File' and failure == 'size':
                        result[0][3] = '8'
                    return result
                stream = Database.stream
                def write_stream(db, name, target):
                    stream(db, name, target)
                    if failure == 'spanning':
                        data = bytearray(target.read_bytes())
                        data[30] |= 1
                        target.write_bytes(data)
                with self.subTest(failure=failure), patch.object(Database, 'rows', rows), patch.object(Database, 'stream', write_stream), self.assertRaises(YitoError):
                    extract(msi, destination, root, 'package.msi')
                self.assertFalse(destination.exists())
