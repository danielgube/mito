import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mito.catalog import Catalog, effective
from mito.engine import Engine
from mito.storage import YitoError, inside, mutation, read_json, write_json
from mito.transfer import download, extract_zip


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.engine.save_profile('test', {'workspace': 'test', 'apps': [], 'env': {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_moves_extracted_files_instead_of_copying_them(self):
        # Copiar duplicaba la escritura de cada archivo y el análisis del antivirus.
        self.bucket({'tool': self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'), \
             patch('mito.engine.shutil.copy2', side_effect=AssertionError('copied instead of moved')):
            self.engine.install('tool', 'test', lambda _: None)
        self.assertTrue((self.root / 'core/apps/test/tool/bin/tool.exe').is_file())

    def test_mutation_wait_retries_a_busy_lock_and_default_fails_cleanly(self):
        import threading
        import time
        held, release = threading.Event(), threading.Event()
        def holder():
            with mutation(self.root, 'check.lock'):
                held.set()
                release.wait(5)
        thread = threading.Thread(target=holder)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        held.wait(5)
        # Sin espera: YitoError, nunca PermissionError al leer el byte bloqueado.
        with self.assertRaises(YitoError):
            with mutation(self.root, 'check.lock'):
                pass
        threading.Timer(0.3, release.set).start()
        started = time.monotonic()
        with mutation(self.root, 'check.lock', wait=5):
            self.assertGreater(time.monotonic() - started, 0.2)

    def test_portable_home_has_windows_known_user_folders(self):
        # %USERPROFILE%\Downloads y similares deben existir: Windows no resuelve
        # carpetas conocidas ausentes y Electron aborta al pedir 'downloads'.
        shutil.rmtree(self.root / 'home')
        env = self.engine.environment('test')
        for name in ('Desktop', 'Documents', 'Downloads', 'Music', 'Pictures', 'Videos', 'Saved Games', 'AppData/LocalLow'):
            self.assertTrue((Path(env['USERPROFILE']) / name).is_dir(), name)

    def test_default_profile_only_when_instance_has_none(self):
        with patch('mito.terminal.prepare'):
            self.assertIsNone(self.engine.ensure_default_profile())
            self.assertEqual(self.engine.profiles(), ['test'])
            (self.root / 'profiles/test.json').unlink()
            self.assertEqual(self.engine.ensure_default_profile(), 'default')
            self.assertIsNone(self.engine.ensure_default_profile())
        # Mismo contenido que profiles/default.json del ZIP de release.
        self.assertEqual(read_json(self.root / 'profiles/default.json'), {'workspace': 'default', 'apps': [], 'env': {}})
        self.assertTrue((self.root / 'workspaces/default').is_dir())

    def manifest(self, **changes):
        return dict({'version':'1.0', 'url':'https://example.org/tool.zip', 'hash':'a'*64, 'bin': [['bin/tool.exe', 'tool']], 'env_set': {'JAVA_HOME':'$dir'}, 'env_add_path':'bin'}, **changes)

    def bucket(self, apps):
        write_json(self.root/'core/buckets/test.json', {'schema':1, 'apps':apps})

    def archive_download(self, url, path, expected=None, **kwargs):
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('bin/tool.exe', b'fake fixture')

    def test_paths_reject_escape_and_windows_special_names(self):
        for path in ('../escape', 'C:\\outside', '\\outside', 'folder/file:stream', 'aux.txt', 'dir/file.', 'x/../bad', 'dir/a?b'):
            with self.subTest(path=path), self.assertRaises(YitoError):
                inside(self.root, path)

    def test_zip_traversal_rejected_before_any_extraction(self):
        archive = self.root/'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('good.txt', 'ok')
            z.writestr('../escape', 'bad')
        with self.assertRaises(YitoError):
            extract_zip(archive, self.root/'out')
        self.assertFalse((self.root/'out/good.txt').exists())

    def test_architecture_overrides_common_fields(self):
        m = self.manifest(architecture={'64bit': {'url':'https://example.org/x64.zip'}})
        self.assertEqual(effective(m)['url'], 'https://example.org/x64.zip')
        self.assertEqual(effective(m)['env_set']['JAVA_HOME'], '$dir')

    def test_scripts_and_persist_not_silently_ignored(self):
        for changes in ({'installer':{'file':'setup.exe'}}, {'env_set':{'HOME':'$dir'}}):
            self.assertNotEqual(self.engine.catalog.compatibility('test/app', self.manifest(**changes)), 'Compatible')

    def test_verified_install_and_relative_lock(self):
        self.bucket({'tool':self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('test/tool', 'test', lambda _: None)
        lock = next(iter(self.engine.selected(self.engine.profile('test')).values()))
        self.assertFalse(Path(lock['path']).is_absolute())
        self.assertTrue((self.root/lock['path']/'bin/tool.exe').exists())
        # La suite puede ejecutarse desde una pestaña de MITO, que ya define MITO_PROFILE:
        # se comprueba que el motor no modifica el entorno del proceso, no que esté vacío.
        inherited = os.environ.get('MITO_PROFILE')
        env = self.engine.environment('test')
        self.assertEqual(env['JAVA_HOME'], str(self.root/lock['path']))
        self.assertEqual(env['USERPROFILE'], str(self.root/'home'))
        self.assertEqual(env['MITO_PROFILE'], 'test')
        self.assertEqual(os.environ.get('MITO_PROFILE'), inherited)

    def test_profile_env_overrides_components_without_leaking_to_next_profile(self):
        self.bucket({'tool': self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('tool', 'test', lambda _: None)
        data = self.engine.profile('test')
        data['env'] = {'java_home': 'custom-java', 'MY_OPTION': 'two words', 'EMPTY': ''}
        self.engine.save_profile('test', data)
        env = self.engine.environment('test')
        self.assertEqual(env['JAVA_HOME'], 'custom-java')
        self.assertEqual(env['MY_OPTION'], 'two words')
        self.assertEqual(env['EMPTY'], '')
        self.assertEqual(set(read_json(self.root/'profiles/test.json')), {'workspace', 'apps', 'env'})
        self.assertTrue(all(isinstance(app, str) for app in data['apps']))
        self.engine.save_profile('other', {'workspace': 'other', 'apps': [], 'env': {}})
        with patch.dict(os.environ, env, clear=True):
            other = self.engine.environment('other')
        self.assertNotIn('MY_OPTION', other)
        self.assertNotIn('JAVA_HOME', other)
        self.assertNotIn('EMPTY', other)

    def test_workspace_variable_follows_profile_context_and_cannot_be_overridden(self):
        self.engine.save_profile('other', {'workspace': 'shared-work', 'apps': [], 'env': {}})
        expected = self.root / 'workspaces/shared-work'
        with patch.object(self.engine, '_environment', side_effect=AssertionError('preview must not activate')):
            reference = self.engine.context_variables('other')
        self.assertEqual(reference['MITO_WORKSPACE'], str(expected))
        self.assertFalse(expected.exists())
        inherited = self.engine.environment('test')
        self.assertEqual(inherited['MITO_WORKSPACE'], str(self.root / 'workspaces/test'))
        with patch.dict(os.environ, inherited, clear=True):
            env, workspace = self.engine.execution_context('other')
        self.assertEqual(workspace, expected)
        self.assertEqual(env['MITO_WORKSPACE'], str(workspace))
        self.assertEqual(env['MITO_PROFILE'], 'other')
        self.assertEqual(env['HOME'], reference['HOME'])
        with self.assertRaises(YitoError):
            self.engine.validate_env({'mito_workspace': 'outside'})

    def test_global_env_precedence_reload_and_cleanup(self):
        self.bucket({'tool': self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('tool', 'test', lambda _: None)
        config = self.root / 'config.properties'
        config.write_text('# env.IGNORED=no\nenv.JAVA_HOME=global-java\nenv.COMMON=a=b\nenv.EMPTY=\n', encoding='utf-8')
        first = self.engine.environment('test')
        self.assertEqual(first['JAVA_HOME'], 'global-java')
        self.assertEqual(first['COMMON'], 'a=b')
        self.assertEqual(first['EMPTY'], '')
        self.assertNotIn('IGNORED', first)
        data = self.engine.profile('test')
        data['env'] = {'java_home': 'profile-java'}
        self.engine.save_profile('test', data)
        self.assertEqual(self.engine.environment('test')['JAVA_HOME'], 'profile-java')
        self.engine.save_profile('other', {'workspace': 'other', 'apps': [], 'env': {}})
        self.assertEqual(self.engine.environment('other')['COMMON'], 'a=b')
        config.write_text('env.COMMON=new\n', encoding='utf-8')
        with patch.dict(os.environ, first, clear=True):
            other = self.engine.environment('other')
        self.assertEqual(other['COMMON'], 'new')
        self.assertNotIn('JAVA_HOME', other)
        self.assertNotIn('EMPTY', other)

    def test_global_env_validation(self):
        for content in ('env.PATH=x', 'env.MITO_ROOT=x', 'env.A=1\nenv.a=2',
                        'env.A=1\nenv.A=2', 'env.MISSING', 'env.=x'):
            (self.root/'config.properties').write_text(content, encoding='utf-8')
            with self.subTest(content=content), self.assertRaises(YitoError):
                self.engine.environment('test')

    def test_profile_env_validation(self):
        for env in ({'HOME': 'outside'}, {'Path': 'outside'}, {'MITO_ROOT': 'outside'},
                    {'VALUE': 123}, {'A': 'one', 'a': 'two'}, {'BAD=NAME': 'value'}):
            self.engine.save_profile('test', {'workspace': 'test', 'apps': [], 'env': env})
            with self.subTest(env=env), self.assertRaises(YitoError):
                self.engine.profile('test')

    def test_missing_selection_is_reported_and_can_be_deactivated(self):
        self.engine.save_profile('test', {'workspace': 'test', 'apps': ['missing/app@123'], 'env': {}})
        with self.assertRaisesRegex(YitoError, 'Falta la instalación'):
            self.engine.environment('test')
        with patch('mito.terminal.prepare'):
            self.engine.set_active('test', 'missing/app@123', False)
        self.assertEqual(self.engine.profile('test')['apps'], [])

    def test_command_names_and_generated_batch_preserve_literal_aliases(self):
        import re
        import subprocess
        import sys
        from mito.storage import command_name
        for alias in ('7+ Taskbar Tweaker', 'tool & %PATH% !MITO_PROFILE!', '-tool'):
            manifest = self.manifest(bin=[['bin/tool.exe', alias]])
            self.assertEqual(self.engine.catalog.compatibility('test/tool', manifest), 'Compatible')
            self.bucket({'tool': manifest})
            with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
                self.engine.install('tool', 'test', lambda _: None)
            self.engine.environment('test')
            shim = self.root/'core/state/shims/test'/(command_name(alias)+'.cmd')
            content = shim.read_text()
            harness = self.root/'check.cmd'
            harness.write_text(re.sub(r'"%~dp0[^"\n]+" -B -I -X utf8 -m mito run',
                lambda _: f'"{sys.executable}" -B -I -c "import sys,json;print(json.dumps(sys.argv[1:]))"', content))
            result = subprocess.run(['cmd.exe', '/d', '/v:on', '/c', str(harness)], capture_output=True, text=True, check=True)
            arguments = json.loads(result.stdout)
            self.assertIn('--bin='+alias.lower(), arguments)
        for invalid in ('../escape', 'folder/tool', 'tool\\bad', 'CON', 'bad"name', 'bad:name', 'trailing.', 'trailing '):
            with self.subTest(invalid=invalid), self.assertRaises(YitoError):
                command_name(invalid)
        with self.assertRaises(YitoError):
            from mito.storage import identifier
            identifier('7+ Taskbar Tweaker')

    def test_multiple_downloads_without_hash_are_all_extracted(self):
        manifest = self.manifest(url=['http://example.org/one.zip', 'http://example.org/two.zip'], bin=['one.exe','two.exe'])
        manifest.pop('hash')
        self.bucket({'tool':manifest})
        calls = []
        def fetch(url, path, expected, **kwargs):
            calls.append((url, expected))
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('one.exe' if 'one.zip' in url else 'two.exe', b'fixture')
        with patch('mito.engine.download', fetch):
            installed = self.engine.install('tool', log=lambda _:None, confirm_unverified=lambda _:True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(expected is None for _,expected in calls))
        payload = self.root/next(iter(installed.values()))['path']
        self.assertTrue((payload/'one.exe').is_file())
        self.assertTrue((payload/'two.exe').is_file())

    def test_unverified_dependency_cancelled_before_any_download(self):
        dependency = self.manifest()
        dependency.pop('hash')
        self.bucket({'tool': self.manifest(depends='dependency'), 'dependency': dependency})
        before = self.engine.profile('test')
        with patch('mito.engine.download') as fetch:
            for confirm in (None, lambda _:False):
                with self.assertRaisesRegex(YitoError, 'cancelada'):
                    self.engine.install('tool', 'test', log=lambda _:None, confirm_unverified=confirm)
            fetch.assert_not_called()
        self.assertEqual(self.engine.profile('test'), before)
        self.assertFalse(self.engine.installed())

    def test_failure_preserves_profile(self):
        self.bucket({'tool':self.manifest()})
        before = self.engine.profile('test')
        with patch('mito.engine.download', side_effect=YitoError('hash incorrecto')):
            with self.assertRaises(YitoError):
                self.engine.install('tool', 'test', lambda _:None)
        self.assertEqual(before, self.engine.profile('test'))
        self.assertEqual(list((self.root/'core/staging').iterdir()), [])

    def test_dependency_cycle_preflight(self):
        self.bucket({'one': self.manifest(depends='two'), 'two': self.manifest(depends='one')})
        with patch('mito.engine.download') as dl, self.assertRaisesRegex(YitoError, 'circular'):
            self.engine.install('one', 'test', lambda _:None)
        dl.assert_not_called()

    def test_dependency_incompatibility_preflight(self):
        self.bucket({'one':self.manifest(depends='two'), 'two':self.manifest(installer={'file':'setup.exe'})})
        with patch('mito.engine.download') as dl, self.assertRaises(YitoError):
            self.engine.install('one', 'test', lambda _:None)
        dl.assert_not_called()

    def test_multiple_runtimes_can_be_active(self):
        self.bucket({'one': self.manifest(), 'two': self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('one', 'test', lambda _: None)
            self.engine.install('two', 'test', lambda _: None)
        self.assertEqual(len(self.engine.profile('test')['apps']), 2)
        self.engine.environment('test')

    def test_moving_root_rebuilds_environment(self):
        self.bucket({'tool':self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('tool', 'test', lambda _:None)
        moved = self.root/'moved'
        moved.mkdir()
        import shutil
        for name in ('core', 'profiles', 'home', 'workspaces'):
            shutil.copytree(self.root/name, moved/name)
        env = Engine(moved).environment('test')
        self.assertTrue(env['JAVA_HOME'].startswith(str(moved)))
        self.assertEqual(env['MITO_WORKSPACE'], str(moved / 'workspaces/test'))

    def test_nested_profiles_do_not_stack_path(self):
        with patch.dict(os.environ, {'MITO_HOST_PATH':'original-path', 'PATH':'old-profile;original-path', 'MITO_ACTIVE_VARS':'JAVA_HOME', 'JAVA_HOME':'old-java'}):
            env = self.engine.environment('test')
        self.assertNotIn('old-profile', env['PATH'])
        self.assertNotIn('JAVA_HOME', env)

    def test_hash_mismatch_discards_download(self):
        class Response(io.BytesIO):
            pass
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b'wrong content')
        target = self.root/'artifact'
        with patch('urllib.request.build_opener', return_value=Opener()), self.assertRaisesRegex(YitoError, 'Hash incorrecto'):
            download('https://example.org/file', target, '0'*64)
        self.assertFalse(target.exists())

    def test_ambiguous_bucket_requires_namespace(self):
        self.bucket({'tool':self.manifest()})
        write_json(self.root/'core/buckets/other.json', {'apps':{'tool':self.manifest()}})
        with self.assertRaisesRegex(YitoError, 'ambiguo'):
            self.engine.catalog.resolve('tool')

    def test_vscode_uses_generic_persistence_and_launch(self):
        from mito.catalog import fingerprint
        m = self.manifest(persist='data')
        payload = self.root/'core/apps/extras/vscode'
        record = {'key':'extras/vscode', 'path':'core/apps/extras/vscode', 'manifest':m, 'manifest_sha256':fingerprint(m), 'version':'1.0'}
        write_json(payload/'.mito.json', {'key':'extras/vscode', 'manifest_sha256':fingerprint(m), 'record':record})
        (payload/'bin').mkdir()
        (payload/'bin/tool.exe').touch()
        for name in ('first', 'second'):
            self.engine.save_profile(name, {'workspace':name, 'apps':[self.engine.install_id('extras/vscode',record)], 'env':{}})
        # Old MITO variables are removed by the generic inherited-activation cleanup.
        with patch.dict(os.environ, {'VSCODE_PORTABLE':'old-profile','MITO_ACTIVE_VARS':'VSCODE_PORTABLE'}):
            first = self.engine.environment('first')
            second = self.engine.environment('second')
        self.assertEqual(first['HOME'], second['HOME'])
        self.assertNotIn('VSCODE_PORTABLE',first)
        self.assertNotIn('VSCODE_PORTABLE',second)
        self.assertEqual((payload/'data').resolve(),self.root/'home/persist/extras/vscode/data')
        with patch.object(self.engine, 'environment', return_value=first), patch('mito.engine.subprocess.Popen') as launch:
            self.engine.launch('first','extras/vscode')
        self.assertEqual(launch.call_args.args[0],[str(payload/'bin/tool.exe')])

    def test_batch_launch_appends_user_arguments_after_manifest_arguments(self):
        from mito.catalog import fingerprint
        manifest = {'version':'1', 'bin':[['tool.cmd','tool','fixed "two words"']]}
        payload = self.root/'core/apps/extras/batch-tool'
        payload.mkdir(parents=True)
        (payload/'tool.cmd').write_text('@echo off\n')
        record = {'key':'extras/batch-tool', 'path':'core/apps/extras/batch-tool',
            'manifest':manifest, 'manifest_sha256':fingerprint(manifest), 'version':'1'}
        write_json(payload/'.mito.json', {'key':'extras/batch-tool',
            'manifest_sha256':fingerprint(manifest), 'record':record})
        self.engine.save_profile('test', {'workspace':'test',
            'apps':[self.engine.install_id('extras/batch-tool',record)], 'env':{}})
        with patch.object(self.engine, 'environment', return_value={}), \
                patch('mito.engine.subprocess.Popen') as launch:
            self.engine.launch('test','extras/batch-tool',arguments=['user','a & b'])
        environment = launch.call_args.kwargs['env']
        self.assertEqual([environment[f'MITO_COMMAND_ARG_{index}'] for index in range(5)],
            [str(payload/'tool.cmd'), 'fixed', 'two words', 'user', 'a & b'])

    def test_batch_simple_subcommand_matches_raw_positional_argument(self):
        import subprocess
        from mito.jobs import batch_command
        script = self.root/'dispatch tool.cmd'
        script.write_text('@echo off\nif ""%1"" == ""run"" exit /b 0\nexit /b 17\n')
        environment = os.environ.copy()
        result = subprocess.run(batch_command(script, ['run'], environment),
            env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_component_exemptions_for_hooks(self):
        from mito.catalog import fingerprint
        manifest = self.manifest(installer={'file':'setup.exe'})
        write_json(self.root/'core/policies.json', {'main/nodejs':{'type':'node','manifest_sha256':fingerprint(manifest)}})
        catalog = Catalog(self.root)
        for key in ('main/nodejs','main/nodejs-lts','extras/vscode','other/tool'):
            self.assertEqual(catalog.compatibility(key,manifest),'Pasos no soportados: installer.file')

    def test_pre_install_runs_and_post_install_is_ignored_for_every_component(self):
        for name in ('tool','editor'):
            self.bucket({name:self.manifest(pre_install='Set-Content -LiteralPath "$dir/prepared.txt" -Value $app', post_install='throw "must not execute"')})
            messages=[]
            with patch('mito.engine.download',self.archive_download):
                installed=self.engine.install(name,log=messages.append)
            self.assertTrue(installed)
            record = next(iter(installed.values()))
            self.assertEqual((self.root/record['path']/'prepared.txt').read_text().strip(), name)
            self.assertTrue(any('post_install omitido' in message for message in messages))
            self.assertTrue(any('ejecutando pre_install' in message for message in messages))

    def test_jar_and_icon_install_preserves_bytes_and_launches_profile_java(self):
        from PIL import Image
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('META-INF/MANIFEST.MF', 'Main-Class: Fixture\n')
            archive.writestr('Fixture.class', b'inert fixture')
        icon = io.BytesIO()
        Image.new('RGBA', (32,32), 'red').save(icon, format='ICO')
        artifacts = {'tool.jar':jar.getvalue(), 'icon.ico':icon.getvalue()}
        manifest = self.manifest(url=['https://example.org/tool.jar', 'https://example.org/icon.ico'],
            hash=[hashlib.sha256(value).hexdigest() for value in artifacts.values()],
            bin=[['tool.jar', 'tool', '--fixed']], env_add_path=[],
            shortcuts=[['tool.cmd', 'Tool', '', 'icon.ico']],
            pre_install='Set-Content "$dir/tool.cmd" -Value \'@start javaw -jar "%~dp0tool.jar"\' -Encoding utf8')
        self.bucket({'tool':manifest})
        def fetch(url, path, *args, **kwargs):
            path.write_bytes(artifacts[url.rsplit('/',1)[-1]])
        with patch('mito.engine.download', fetch), patch('mito.terminal.prepare'):
            records = self.engine.install('tool', 'test', log=lambda _:None)
        record = next(iter(records.values()))
        payload = self.root/record['path']
        for name, content in artifacts.items():
            self.assertEqual((payload/name).read_bytes(), content)
        self.assertFalse((payload/'META-INF').exists())
        self.assertIn('javaw -jar', (payload/'tool.cmd').read_text(encoding='utf-8-sig'))
        self.assertEqual(self.engine.application_target(record), ('tool.cmd', []))
        self.assertEqual(self.engine.application_target(dict(record, manifest=dict(manifest, shortcuts=[]))),
                         ('tool.jar', ['--fixed']))
        java = self.root/'home/custom-commands/java.exe'
        java.write_bytes(b'inert executable')
        caller = self.root/'caller'
        caller.mkdir()
        with patch('mito.engine.subprocess.call', return_value=0) as launch, \
             patch('mito.engine.os.getcwd', return_value=str(caller)):
            self.engine.launch('test','tool',binary='tool',arguments=['two words'])
        self.assertEqual(launch.call_args.args[0], [str(java), '-jar', str(payload/'tool.jar'), '--fixed', 'two words'])
        # Un comando de consola (shim) trabaja en el directorio desde el que se invoca,
        # nunca en el workspace: `git init` en la raíz no debe crear el repo en otro sitio.
        self.assertEqual(launch.call_args.kwargs['cwd'], caller)
        self.assertEqual(launch.call_args.kwargs['env']['APPDATA'], str(self.root/'home/AppData/Roaming'))
        with patch('mito.engine.shutil.which', return_value=None), patch('mito.engine.subprocess.call') as launch:
            with self.assertRaisesRegex(YitoError, 'java.exe'):
                self.engine.launch('test','tool',binary='tool')
            launch.assert_not_called()

    def test_declared_shortcut_icons_are_relative_supported_files(self):
        for icon in ('../outside.ico', 'C:/outside.ico', 'icon.dll,0', 'icon.png', {'path':'icon.ico'}):
            with self.subTest(icon=icon):
                manifest = self.manifest(shortcuts=[['tool.exe','Tool','',icon]])
                self.assertNotEqual(self.engine.catalog.compatibility('test/tool', manifest), 'Compatible')

    def test_installer_script_nested_archive_order_context_and_architecture(self):
        private_tools = Path(__file__).resolve().parents[1]/'tools/7zip'
        shutil.copytree(private_tools, self.root/'core/tools/7zip')
        def nested_download(url, path, expected=None, **kwargs):
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, 'w') as archive:
                archive.writestr('Tool App.exe', b'nested fixture')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('$PLUGINSDIR/app-64.7z', nested.getvalue())
                archive.writestr('$PLUGINSDIR/temporary.txt', b'discard')
        script = [
            'if (!(Test-Path "$dir/prepared")) { throw "wrong order" }',
            'if ($fname -ne "dl.7z" -or $architecture -ne "64bit" -or $global) { throw "bad context" }',
            'if ($original_dir -ne $dir -or $manifest.version -ne $version) { throw "bad context" }',
            'Remove-Item "$dir/*" -Exclude "app-64.7z"',
            'Expand-7zipArchive "$dir/app-64.7z" "$dir" -Removal',
        ]
        manifest = self.manifest(bin='Tool App.exe', env_add_path=[],
            pre_install='Set-Content "$dir/prepared" $app',
            installer={'file': 'blocked-base.exe'}, post_install='throw "must not run"',
            architecture={'64bit': {'url':'https://example.org/portable.exe#/dl.7z',
                          'extract_dir':'$PLUGINSDIR', 'installer': {'script': script}}})
        self.bucket({'nested':manifest})
        messages = []
        with patch('mito.engine.download', nested_download):
            records = self.engine.install('nested', log=messages.append)
        payload = self.root/next(iter(records.values()))['path']
        self.assertEqual((payload/'Tool App.exe').read_bytes(), b'nested fixture')
        self.assertFalse((payload/'app-64.7z').exists())
        self.assertFalse((payload/'temporary.txt').exists())
        self.assertLess(messages.index('test/nested: ejecutando pre_install'),
                        messages.index('test/nested: ejecutando installer.script'))
        self.assertLess(messages.index('test/nested: ejecutando installer.script'),
                        messages.index('Validando test/nested'))

    def test_installer_runner_exposes_generic_inno_helpers(self):
        from mito.hooks import SCRIPT
        self.assertIn('function Expand-InnoArchive', SCRIPT)
        self.assertIn('function Get-HelperPath', SCRIPT)
        self.assertIn('function Invoke-ExternalCommand', SCRIPT)
        self.assertIn("-m mito.hooks inno", SCRIPT)

    def test_installer_unsupported_fields_and_malformed_scripts_fail_before_download(self):
        for installer in (True, False, 'setup.exe', [], {'file':'setup.exe'},
                          {'script':'throw "not reached"', 'args':[]},
                          {'script':[], 'keep':False}, {'script':'', 'unknown':None},
                          {'script':None}, {'script':7}, {'script':['ok', 7]}):
            with self.subTest(installer=installer):
                self.bucket({'tool':self.manifest(installer=installer)})
                with patch('mito.engine.download') as download_mock, self.assertRaises(YitoError):
                    self.engine.install('tool', log=lambda _:None)
                download_mock.assert_not_called()
        for installer in (None, {}, {'script':''}, {'script':[]}):
            self.assertEqual(self.engine.catalog.compatibility('test/tool',
                self.manifest(installer=installer)), 'Compatible')

    def test_installer_failure_preserves_installed_version_and_profile(self):
        self.bucket({'tool':self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('tool', 'test', log=lambda _:None)
        before = self.engine.installed()
        profile = self.engine.profile('test')
        for script in ('Set-Content "$dir/bin/tool.exe" changed; throw "installer failure"',
                       'Remove-Item "$dir/bin/tool.exe"',
                       'New-Item -ItemType Junction -Path "$dir/link" -Target "$dir/bin"'):
            with self.subTest(script=script):
                self.bucket({'tool':self.manifest(version='2.0', installer={'script':script})})
                with patch('mito.engine.download', self.archive_download), self.assertRaises(YitoError):
                    self.engine.install('tool', 'test', log=lambda _:None)
                self.assertEqual(self.engine.installed(), before)
                self.assertEqual(self.engine.profile('test'), profile)
                self.assertEqual((self.root/'core/apps/test/tool/bin/tool.exe').read_bytes(), b'fake fixture')

    def test_pre_install_failure_prevents_installer_script(self):
        self.bucket({'tool':self.manifest(pre_install='throw "stop before installer"',
                                         installer={'script':'throw "not reached"'})})
        with patch('mito.engine.download', self.archive_download), patch('mito.hooks.run_installer') as installer:
            with self.assertRaisesRegex(YitoError, 'pre_install falló'):
                self.engine.install('tool', log=lambda _:None)
            installer.assert_not_called()

    def test_pre_install_failure_does_not_publish_or_change_profile(self):
        self.bucket({'broken':self.manifest(pre_install=['Write-Output "preparing"', 'throw "fixture failure"'])})
        before = self.engine.profile('test')
        with patch('mito.engine.download', self.archive_download):
            with self.assertRaisesRegex(YitoError, 'pre_install falló.*fixture failure'):
                self.engine.install('broken', 'test', log=lambda _:None)
        self.assertEqual(self.engine.profile('test'), before)
        self.assertFalse(self.engine.installed())

    def test_extensionless_download_renamed_by_real_pre_install(self):
        content = b'MZ fixture: never executed'
        for url, filename in [('https://example.org/download?channel=release', 'download'),
                              ('https://example.org/get?channel=release#/Tool%20File', 'Tool File')]:
            with self.subTest(url=url):
                manifest = self.manifest(bin='tool.exe', env_add_path=[],
                    architecture={'64bit': {'url': url, 'hash': hashlib.sha256(content).hexdigest()}},
                    pre_install='Rename-Item "$dir\\$fname" "tool.exe"')
                self.bucket({'tool': manifest})
                self.assertEqual(self.engine.catalog.compatibility('test/tool', manifest), 'Compatible')
                with patch('mito.engine.download', side_effect=lambda url, path, *a, **kw: path.write_bytes(content)):
                    records = self.engine.install('tool', log=lambda _: None)
                payload = self.root/next(iter(records.values()))['path']
                self.assertEqual((payload/'tool.exe').read_bytes(), content)
                self.assertFalse((payload/filename).exists())

    def test_pre_install_multiple_download_names_and_extract_to(self):
        self.bucket({'tool': self.manifest(
            url=['https://example.org/First', 'https://example.org/get#/Second'],
            hash=['a'*64, 'b'*64], extract_to=['one', 'two'], bin='tool.exe',
            pre_install=['if ($fname.Count -ne 2) { throw "Missing names" }',
                         'Copy-Item "$dir/one/$($fname[0])" "$dir/tool.exe"',
                         'Copy-Item "$dir/two/$($fname[1])" "$dir/second.exe"'])})
        with patch('mito.engine.download', side_effect=lambda url, path, *a, **kw: path.write_bytes(url.encode())):
            records = self.engine.install('tool', log=lambda _: None)
        payload = self.root/next(iter(records.values()))['path']
        self.assertEqual((payload/'tool.exe').read_bytes(), b'https://example.org/First')
        self.assertEqual((payload/'second.exe').read_bytes(), b'https://example.org/get#/Second')

    def test_extensionless_zip_is_extracted_and_traversal_still_rejected(self):
        self.bucket({'tool': self.manifest(url='https://example.org/download?format=zip')})
        with patch('mito.engine.download', self.archive_download):
            records = self.engine.install('tool', log=lambda _: None)
        payload = self.root/next(iter(records.values()))['path']
        self.assertEqual((payload/'bin/tool.exe').read_bytes(), b'fake fixture')
        def bad_download(url, path, *args, **kwargs):
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('../escape', b'bad')
        self.bucket({'bad': self.manifest(url='https://example.org/download')})
        with patch('mito.engine.download', bad_download), self.assertRaises(YitoError):
            self.engine.install('bad', log=lambda _: None)
        self.assertFalse((self.root/'core/apps/test/bad').exists())

    def test_download_names_are_shared_and_confined(self):
        from mito.transfer import download_filename
        self.assertEqual(download_filename('https://example.org/Tool%2EEXE?x=1'), 'Tool.EXE')
        for ending in ('', '#/../escape', '#/dir/file', '#/C%3Aevil', '#/NUL',
                       '#/a%5Cb', '#/bad%3Fname', '#/..', '#/file.'):
            url = 'https://example.org/' + ending
            with self.subTest(url=url):
                with self.assertRaises(YitoError):
                    download_filename(url)
                self.assertNotEqual(self.engine.catalog.compatibility('test/tool', self.manifest(url=url)), 'Compatible')
        self.assertNotEqual(self.engine.catalog.compatibility('test/tool',
            self.manifest(url='https://example.org/tool.iso')), 'Compatible')

    def test_pre_install_nested_archive_helper(self):
        from mito.hooks import expand
        private_tools = Path(__file__).resolve().parents[1]/'tools/7zip'
        shutil.copytree(private_tools, self.root/'core/tools/7zip')
        def nested_download(url, path, expected=None, **kwargs):
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, 'w') as archive:
                archive.writestr('bin/tool.exe', b'nested fixture')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('$PLUGINSDIR/app-64.7z', nested.getvalue())
        self.bucket({'nested':self.manifest(pre_install=[
            'Expand-7zipArchive "$dir/`$PLUGINSDIR/app-64.7z" "$dir"',
            'Remove-Item -LiteralPath "$dir/`$PLUGINSDIR" -Recurse -Force',
        ])})
        with patch('mito.engine.download', nested_download):
            records = self.engine.install('nested', log=lambda _:None)
        payload = self.root/next(iter(records.values()))['path']
        self.assertEqual((payload/'bin/tool.exe').read_bytes(), b'nested fixture')
        self.assertFalse((payload/'$PLUGINSDIR').exists())
        with self.assertRaisesRegex(YitoError, 'dentro del payload'):
            expand(self.root, payload, payload/'nested.7z', self.root/'outside')

    def test_pre_install_persist_scope_and_architecture_override(self):
        script = [
            'if (Test-Path "$persist_dir/saved.txt") { Copy-Item "$persist_dir/saved.txt" "$dir/seed.txt" }',
            'Set-Content "$dir/home.txt" $env:USERPROFILE',
        ]
        for name, shortcuts in [('shared', []), ('isolated', [['bin/tool.exe', 'Tool']])]:
            persisted = self.root/'home/persist/test'/name
            persisted.mkdir(parents=True)
            (persisted/'saved.txt').write_text('private data')
            self.bucket({name:self.manifest(persist='data', shortcuts=shortcuts,
                        pre_install='throw "wrong architecture"', architecture={'64bit':{'pre_install':script}})})
            with patch('mito.engine.download', self.archive_download):
                records = self.engine.install(name, log=lambda _:None)
            payload = self.root/next(iter(records.values()))['path']
            self.assertEqual((payload/'seed.txt').exists(), name == 'shared')
            self.assertEqual((payload/'home.txt').read_text().strip(), str(self.root/'home'))

    def test_altered_lock_is_rejected_on_activation(self):
        self.bucket({'tool':self.manifest()})
        with patch('mito.engine.download', self.archive_download), patch('mito.terminal.prepare'):
            self.engine.install('tool', 'test', lambda _:None)
        record = next(iter(self.engine.installed().values()))
        marker = self.root / record['path'] / '.mito.json'
        metadata = read_json(marker)
        metadata['record']['manifest']['env_set']['JAVA_HOME'] = 'wrong'
        write_json(marker, metadata)
        with self.assertRaisesRegex(YitoError, 'incoherente'):
            self.engine.environment('test')


if __name__ == '__main__':
    unittest.main()
