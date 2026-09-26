from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from mito.engine import Engine
from mito.catalog import fingerprint
from mito.shortcuts import application_link, badge_text, badge_color, application_icon
from mito.storage import YitoError, read_json, write_json


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.engine = Engine(self.root)
        self.engine.initialize()
        base = self.root/'core/assets/branding/mito.ico'
        base.parent.mkdir(parents=True)
        Image.new('RGBA',(256,256),'blue').save(base,sizes=[(16,16),(32,32),(256,256)])
        manifest = {'version':'1', 'shortcuts':[['app.exe','My application']]}
        self.record = {'key':'extras/app','manifest':manifest,'version':'1','manifest_sha256':fingerprint(manifest),'path':'core/apps/extras/app'}
        self.installation = self.engine.install_id('extras/app', self.record)
        payload = self.root/self.record['path']
        payload.mkdir(parents=True)
        (payload/'app.exe').write_bytes(b'fixture')
        write_json(payload/'.mito.json',{'key':'extras/app','record':self.record,'manifest_sha256':fingerprint(manifest)})
        self.engine.save_profile('test',{'workspace':'test','apps':[self.installation],'env':{}})

    def tearDown(self):
        self.temp.cleanup()

    @unittest.skipUnless(os.name == 'nt', 'Windows Shell links required')
    def test_retarget_link_preserves_icon_and_ignores_customized_links(self):
        from mito.shortcuts import literal, retarget_link, write_link
        path = self.root / 'pocketool.lnk'
        old = [str(self.root / 'old.exe'), '--title', 'Tool (test)', 'a;b']
        new = [str(self.root / 'pythonw.exe'), '-m', 'mito', 'pocketool-run', '--arguments64', 'YWJj']
        icon = self.root / 'core/assets/branding/mito.ico'
        write_link(path, old, self.root, icon)

        def read_link():
            script = ('$ws = New-Object -ComObject WScript.Shell; '
                f'$lnk = $ws.CreateShortcut({literal(path)}); '
                '@{target=$lnk.TargetPath; arguments=$lnk.Arguments; icon=$lnk.IconLocation; '
                'working=$lnk.WorkingDirectory; style=$lnk.WindowStyle} | ConvertTo-Json -Compress')
            result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return json.loads(result.stdout)

        before = read_link()
        self.assertTrue(retarget_link(path, [old[0]], old[1:], new))
        after = read_link()
        self.assertEqual(after['target'], new[0])
        self.assertEqual(after['arguments'], subprocess.list2cmdline(new[1:]))
        for field in ('icon', 'working', 'style'):
            self.assertEqual(after[field], before[field])
        self.assertTrue(retarget_link(path, [old[0]], old[1:], new))
        write_link(path, [*old, '--customized'], self.root, icon)
        customized = path.read_bytes()
        self.assertFalse(retarget_link(path, [old[0]], old[1:], new))
        self.assertEqual(path.read_bytes(), customized)

    def test_creating_profile_does_not_publish_shortcuts(self):
        self.engine.create_profile('new')
        self.assertFalse(list(self.root.glob('*.lnk')))
        self.assertFalse((self.root/'core/state/shortcuts').exists())

    def test_desktop_link_pins_profile_and_uses_badged_icon(self):
        desktop = self.root/'test-desktop'
        with patch('mito.shortcuts.desktop_path',return_value=desktop), patch('mito.shortcuts.subprocess.run'), patch('mito.shortcuts.write_link') as write:
            result = application_link(self.engine,'test',self.installation,'AB12')
        self.assertEqual(result.parent,desktop)
        argv = write.call_args.args[1]
        self.assertEqual(argv[argv.index('--profile')+1],'test')
        self.assertEqual(argv[argv.index('--app')+1],'extras/app')
        self.assertEqual(result.name, 'test - extras-app.lnk')
        with Image.open(write.call_args.args[3]) as icon:
            self.assertIn((256,256),icon.ico.sizes())
        self.assertFalse(list(self.root.glob('*.lnk')))

    def test_badge_color_is_saved_and_changes_generated_icon(self):
        (self.root/'config.properties').write_text('shortcut.badgeColor=azul\n', encoding='utf-8')
        self.assertEqual(badge_color(self.engine), 'azul')
        with patch('mito.shortcuts.desktop_path', return_value=self.root/'desktop'), patch('mito.shortcuts.write_link') as write:
            application_link(self.engine, 'test', self.installation, 'TEST')
            blue = write.call_args.args[3]
            application_link(self.engine, 'test', self.installation, 'TEST', color='rojo')
            red = write.call_args.args[3]
        self.assertNotEqual(blue, red)
        registry = next((self.root/'core/state/application-shortcuts').glob('*.json'))
        self.assertEqual(read_json(registry)['color'], 'rojo')
        with self.assertRaises(YitoError):
            badge_color(self.engine, 'invalido')

    def test_badge_color_changes_background_and_keeps_white_letters(self):
        from mito.icons import _add_mito_badge, BADGE_COLORS
        source = Image.new('RGBA', (32, 32), 'blue')
        for name in ('negro', 'rojo', 'azul', 'naranja', 'rosa', 'verde', 'marron', 'morado'):
            with self.subTest(color=name):
                result = _add_mito_badge(source, 'A', BADGE_COLORS[name])
                self.assertEqual(result.getpixel((31, 31)), (*BADGE_COLORS[name], 255))
                self.assertIn((255, 255, 255, 255), result.getdata())

    def test_coupled_shortcut_records_mode_and_opens_terminal_runner(self):
        desktop = self.root/'test-desktop'
        with patch('mito.shortcuts.desktop_path', return_value=desktop), patch('mito.shortcuts.write_link') as write:
            application_link(self.engine, 'test', self.installation, 'TERM', coupled=True,
                arguments=['run','two words'])
        argv = write.call_args.args[1]
        self.assertIn('--coupled', argv)
        self.assertEqual(argv[-3:], ['--','run','two words'])
        registry = next((self.root/'core/state/application-shortcuts').glob('*.json'))
        self.assertTrue(read_json(registry)['coupled'])
        self.assertEqual(read_json(registry)['arguments'], ['run','two words'])

    def test_application_name_uses_manifest_shortcut_label(self):
        self.assertEqual(self.engine.application_name(self.record), 'My application')

    def test_declared_icon_is_used_and_refreshed_for_current_registry(self):
        from mito.shortcuts import migrate_application_links
        payload = self.root/self.record['path']
        manifest = dict(self.record['manifest'], architecture={'64bit':{
            'shortcuts':[['absent.exe','Absent','','absent.ico'], ['app.exe','App','','brand.ico']]}})
        record = dict(self.record, manifest=manifest, manifest_sha256=fingerprint(manifest))
        write_json(payload/'.mito.json', {'key':record['key'], 'record':record,
                                        'manifest_sha256':record['manifest_sha256']})
        source = payload/'brand.ico'
        Image.new('RGBA',(256,256),'red').save(source, sizes=[(32,32),(256,256)])
        def write(path, argv, root, icon):
            path.write_text(str(icon))
        with patch('mito.shortcuts.desktop_path',return_value=self.root/'desktop'), patch('mito.shortcuts.write_link',side_effect=write):
            link = application_link(self.engine,'test',self.installation,'DEV')
            first = Path(link.read_text())
            with Image.open(first) as image:
                self.assertEqual(image.ico.getimage((256,256)).getpixel((0,0)), (255,0,0,255))
            Image.new('RGBA',(256,256),'lime').save(source, sizes=[(32,32),(256,256)])
            backups = {}
            migrate_application_links(self.engine, record['key'], record['key'], record, backups, lambda _:None)
            second = Path(link.read_text())
            self.assertNotEqual(first, second)
            with Image.open(second) as image:
                self.assertEqual(image.ico.getimage((256,256)).getpixel((0,0)), (0,255,0,255))
            self.assertIn(link, backups)
            self.assertEqual(len(list(link.parent.glob('*.lnk'))), 1)

    def test_declared_icon_profile_scope_fallback_and_confinement(self):
        from mito.shortcuts import component_icon_source
        from mito.isolation import payload_path
        manifest = dict(self.record['manifest'], persist='data', shortcuts=[['app.exe','App','','brand.ico']])
        record = dict(self.record, manifest=manifest)
        payload = payload_path(self.root,record,'test')
        payload.mkdir(parents=True)
        icon = payload/'brand.ico'
        icon.write_bytes(b'invalid icon')
        self.assertEqual(component_icon_source(self.engine,record,'test'),icon)
        generated = application_icon(self.engine,'bad','DEV',icon)
        with Image.open(generated) as image:
            self.assertEqual(image.ico.getimage((256,256)).getpixel((0,0)),(0,0,255,255))
        icon.unlink()
        self.assertEqual(component_icon_source(self.engine,record,'test'),payload/'app.exe')
        manifest['shortcuts'][0][3] = 'C:/outside.ico'
        with self.assertRaises(YitoError):
            component_icon_source(self.engine,record,'test')

    def test_owned_launch_assigns_before_running_and_waits_for_job_tree(self):
        with patch('mito.processes.ProcessJob') as job_type:
            process = job_type.return_value.spawn.return_value
            process.wait.return_value = 7
            result = self.engine.launch('test', 'extras/app', owned=True)
        self.assertEqual(result, 7)
        self.assertEqual(job_type.return_value.spawn.call_args.kwargs['creationflags'], 0)
        job_type.return_value.wait_empty.assert_called_once_with()
        job_type.return_value.close.assert_called_once_with()

    def test_inactive_or_nonlaunchable_components_rejected(self):
        self.assertTrue(self.engine.can_open(self.record))
        runtime = dict(self.record,manifest={'version':'11','env_add_path':'bin'})
        self.assertFalse(self.engine.can_open(runtime))
        missing = dict(self.record,manifest={'shortcuts':[['absent.exe','Missing']]})
        self.assertFalse(self.engine.can_open(missing))
        self.engine.save_profile('test',{'workspace':'test','apps':[], 'env':{}})
        with self.assertRaises(YitoError), patch('mito.shortcuts.desktop_path') as desktop:
            application_link(self.engine,'test',self.installation)
        desktop.assert_not_called()

    def test_shortcut_identity_stays_stable_after_version_change(self):
        desktop = self.root/'desktop'
        def write(path, argv, root, icon):
            path.write_text(' '.join(argv))
        with patch('mito.shortcuts.desktop_path', return_value=desktop), patch('mito.shortcuts.write_link', side_effect=write):
            first = application_link(self.engine, 'test', self.installation, 'DEV')
            manifest = dict(self.record['manifest'], version='2')
            record = dict(self.record, version='2', manifest=manifest, manifest_sha256=fingerprint(manifest), path='core/apps/extras/app')
            write_json(self.root/record['path']/'.mito.json', {'key':'extras/app', 'record':record, 'manifest_sha256':fingerprint(manifest)})
            (self.root/record['path']/'app.exe').write_bytes(b'new')
            new_id = self.engine.install_id('extras/app', record)
            self.engine.save_profile('test', {'workspace':'test', 'apps':[new_id], 'env':{}})
            second = application_link(self.engine, 'test', new_id, 'DEV')
        self.assertEqual(first, second)
        self.assertEqual(len(list(desktop.glob('*.lnk'))), 1)
        registries = list((self.root/'core/state/application-shortcuts').glob('*.json'))
        self.assertEqual(len(registries), 1)
        self.assertEqual(read_json(registries[0])['app'], 'extras/app')
        self.assertNotIn('@', first.read_text())
        # The unchanged command resolves the selected replacement via the engine.
        with patch.object(self.engine, 'environment', return_value={}), patch('mito.engine.subprocess.Popen') as launch:
            self.engine.launch('test', 'extras/app')
        self.assertEqual(launch.call_args.args[0], [str(self.root/record['path']/'app.exe')])

    def test_badge_default_limit_and_profile_changes_keep_icon(self):
        self.assertEqual(badge_text(self.engine),'MITO')
        self.assertEqual(badge_text(self.engine,'ab12'),'AB12')
        for value in ('ABCDE','A B','!'):
            with self.assertRaises(YitoError):
                badge_text(self.engine,value)
        icon = application_icon(self.engine,'test','DEV')
        previous = icon.read_bytes()
        self.engine.set_active('test',self.installation,False)
        self.assertEqual(icon.read_bytes(),previous)

    def test_badge_preserves_icon_and_native_sizes(self):
        icon = application_icon(self.engine,'app','MITO')
        with Image.open(icon) as result:
            self.assertEqual(result.ico.sizes(),{(16,16),(32,32),(256,256)})
            for size in result.ico.sizes():
                frame = result.ico.getimage(size).convert('RGBA')
                self.assertEqual(frame.getpixel((0,0)),(0,0,255,255))
                self.assertGreater(len(frame.crop((size[0]//2,size[1]//2,*size)).getcolors(size[0]*size[1])),1)
        self.assertNotEqual(icon,application_icon(self.engine,'app','DEV'))

    def test_mito_shortcut_opens_manager_with_selected_profile(self):
        from mito.shortcuts import mito_link
        from mito.terminal import command
        terminal = self.root/'core/tools/windows-terminal/WindowsTerminal.exe'
        terminal.parent.mkdir(parents=True)
        terminal.touch()
        with patch('mito.shortcuts.desktop_path',return_value=self.root/'desktop'), patch('mito.terminal_branding.ensure_branded_executable', return_value=terminal), patch('mito.shortcuts.write_link') as write:
            mito_link(self.engine,'test','DEV1')
        argv = write.call_args.args[1]
        self.assertEqual(argv[0], str(terminal))
        self.assertEqual(argv[-3:],['ui','--profile','test'])
        self.assertTrue(write.call_args.args[3].is_file())
        commandline = command(self.engine,selected_profile='test')
        self.assertEqual(commandline[-3:],['ui','--profile','test'])
        self.assertNotIn('shell',commandline)

    def test_delete_profile_preserves_data_and_last_profile(self):
        self.engine.create_profile('second')
        workspace = self.root/'workspaces/second/keep.txt'
        workspace.write_text('project')
        self.engine.delete_profile('second')
        self.assertTrue(workspace.exists())
        self.assertTrue((self.root/self.record['path']).exists())
        self.assertEqual(self.engine.profiles(),['test'])
        with self.assertRaises(YitoError):
            self.engine.delete_profile('test')
        self.assertEqual(self.engine.profiles(),['test'])
