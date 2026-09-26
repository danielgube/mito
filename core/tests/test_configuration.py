from pathlib import Path
import tempfile
import unittest

from mito.configuration import Configuration, parse
from mito.engine import Engine
from mito.storage import YitoError


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = Engine(self.root)
        self.engine.initialize()
        self.path = self.root/'config.properties'

    def test_global_save_preserves_comments_unknown_properties_and_equals(self):
        self.path.write_text('# My configuration\n\nbucket.main=C:/catalog\nenv.OLD=old\nunknown.setting=keep\n')
        settings = Configuration(self.engine)
        self.assertEqual(self.path.read_bytes(), settings.original)
        values = dict(settings.values)
        del values['env.OLD']
        values.update({'env.TOKEN': 'a=b', 'enable_proxy_authentication': 'true'})
        settings.save(values.items())
        self.assertEqual(parse(self.path.read_text()), values)
        self.assertTrue(self.path.read_text().startswith('# My configuration\n\n'))
        self.assertEqual(self.engine.global_env(), {'TOKEN':'a=b'})

    def test_profile_only_changes_env_and_overrides_global(self):
        self.engine.save_profile('test', {'workspace':'work', 'apps':[], 'env':{'OLD':'old'}})
        self.path.write_text('env.CUSTOM=global\n')
        before = self.engine.profile('test')
        settings = Configuration(self.engine, 'test')
        settings.save([('CUSTOM', 'profile')])
        after = self.engine.profile('test')
        self.assertEqual(after, dict(before, env={'CUSTOM':'profile'}))
        self.assertEqual(self.engine.environment('test')['CUSTOM'], 'profile')
        self.assertEqual(self.path.read_text(), 'env.CUSTOM=global\n')

    def test_invalid_values_leave_file_unchanged(self):
        self.path.write_text('# original\n')
        for pairs in [[('enable_proxy_authentication','maybe')], [('env.PATH','bad')],
                      [('env.X','one'), ('env.x','two')], [('env.X','a\nb')],
                      [('network.timeout_seconds','nan')], [('network.check_status','999')],
                      [('http_proxy','http://user:password@example.org')],
                      [('shortcut.badgeColor','blanco')], [('terminal.defaultShell','bash')]]:
            with self.subTest(pairs=pairs), self.assertRaises(YitoError):
                Configuration(self.engine).save(pairs)
            self.assertEqual(self.path.read_text(), '# original\n')

    def test_concurrent_edit_is_not_overwritten(self):
        self.path.write_text('env.X=one\n')
        settings = Configuration(self.engine)
        self.path.write_text('env.X=external\n')
        with self.assertRaisesRegex(YitoError, 'cambió fuera'):
            settings.save([('env.X','mine')])
        self.assertEqual(self.path.read_text(), 'env.X=external\n')
