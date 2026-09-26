from pathlib import Path
import tempfile
import unittest
import zipfile
from mito.engine import Engine
from mito.release import build_release, detected_version
from mito.storage import YitoError
from mito.i18n import LANGUAGES, LOCALES


class ReleaseTests(unittest.TestCase):
    def test_clean_archive_excludes_instance_and_local_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            engine=Engine(root)
            engine.initialize()
            for name in ('mito.cmd','README.md','core/bootstrap.ps1','core/bootstrap.py','core/bootstrap.lock.json','core/app/mito/__main__.py','core/assets/branding/mito.ico','core/assets/branding/pocketools.ico','core/assets/branding/mito_shortcut.jpg','core/assets/branding/mito_icon.png','core/app/mito/debug.txt','core/tools/python/python.exe','home/secret.txt','core/apps/secret.txt','pocketools/corporate/private.cmd','config.properties'):
                p=root/name
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text('private' if name=='config.properties' else 'fixture')
            (root/'core/app/mito/__init__.py').write_text('__version__ = "2.3.4"')
            locales = root/'core/app/mito/locales'
            locales.mkdir()
            for language in LANGUAGES:
                (locales/f'{language}.json').write_bytes((LOCALES/f'{language}.json').read_bytes())
            (locales/'private.json').write_text('private')
            (root/'core/state/host-applications.json').write_text('{"external":"C:/host/private.exe"}')
            (root/'logs/pending').mkdir(parents=True)
            (root/'logs/mito.log').write_text('private error log')
            (root/'logs/pending/event.json').write_text('private pending event')
            for name in ('mcp_server', 'mcp_service', 'mcp_tools', 'mcp_ui', 'jobs', 'processes'):
                (root/f'core/app/mito/{name}.py').write_text('# MCP fixture')
            mcp_state = root/'core/state/mcp/jobs/session/job'
            mcp_state.mkdir(parents=True)
            (mcp_state/'stdout').write_text('private command output')
            (root/'core/state/mcp/settings.json').write_text('{"port": 12345}')
            archive=build_release(engine)
            self.assertEqual(archive.name,'mito-2.3.4-windows-x64.zip')
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertIn('core/app/mito/__main__.py',z.namelist())
                for name in ('mcp_server', 'mcp_service', 'mcp_tools', 'mcp_ui', 'jobs', 'processes'):
                    self.assertIn(f'core/app/mito/{name}.py', z.namelist())
                self.assertFalse(any('/state/mcp/' in name for name in z.namelist()))
                for language in LANGUAGES:
                    self.assertEqual(z.read(f'core/app/mito/locales/{language}.json'), (LOCALES/f'{language}.json').read_bytes())
                self.assertNotIn('core/app/mito/locales/private.json', z.namelist())
                self.assertIn('mito.language=system', z.read('config.properties').decode('utf-8'))
                self.assertIn('profiles/default.json',z.namelist())
                self.assertIn('core/assets/branding/mito.ico',z.namelist())
                self.assertIn('core/assets/branding/pocketools.ico',z.namelist())
                for excluded in ('README.md','core/assets/branding/mito_shortcut.jpg','core/assets/branding/mito_icon.png','core/app/mito/debug.txt','core/tools/python/python.exe'):
                    self.assertNotIn(excluded,z.namelist())
                self.assertNotIn('home/secret.txt',z.namelist())
                self.assertNotIn('core/apps/secret.txt',z.namelist())
                self.assertNotIn('pocketools/corporate/private.cmd',z.namelist())
                self.assertNotIn('core/state/host-applications.json',z.namelist())
                self.assertFalse(any(name.startswith('logs/') for name in z.namelist()))
                self.assertIn('posthog.enabled=true', z.read('config.properties').decode())
                self.assertIn('posthog.host=https://eu.i.posthog.com', z.read('config.properties').decode())
                self.assertNotIn(b'private',z.read('config.properties'))
            self.assertTrue(archive.with_suffix('.zip.sha256').exists())
            with self.assertRaises(YitoError):
                build_release(engine)

    def test_version_is_validated_without_executing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'core/app/mito/__init__.py'
            source.parent.mkdir(parents=True)
            for value in ('__version__ = "../bad"','__version__ = "01.2.3"','__version__ = str(123)','x = 1'):
                source.write_text(value)
                with self.assertRaises(YitoError):
                    detected_version(root)
            source.write_text('__version__ = "1.2.3"\nraise RuntimeError("must not execute")')
            self.assertEqual(detected_version(root),'1.2.3')
