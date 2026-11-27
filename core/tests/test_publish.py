from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from mito.engine import Engine
from mito.publish import commit_notes, updated_changelog, next_version, publish
from mito.storage import write_json, YitoError


class PublishTests(unittest.TestCase):
    def test_new_release_commits_changelog_and_restores_it_on_commit_failure(self):
        for fail_commit in (False, True):
            with self.subTest(fail_commit=fail_commit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                engine = Engine(root)
                source = root / 'core/app/mito/__init__.py'
                source.parent.mkdir(parents=True)
                source.write_text('__version__ = "0.1.0"\n', encoding='utf-8')
                changelog = root / 'CHANGELOG.md'
                original = b'# Changelog\r\n\r\n## Sin publicar\r\n\r\nManual\r\n'
                changelog.write_bytes(original)
                committed = []

                def fake_git(root, *args, **kwargs):
                    if 'commit' in args:
                        if fail_commit:
                            raise YitoError('commit fallido')
                        committed.append(changelog.read_text(encoding='utf-8'))
                    if args == ('push', 'origin', 'main'):
                        raise YitoError('red no disponible')
                    return {('rev-parse', '--show-toplevel'): root.as_posix(),
                            ('remote', 'get-url', 'origin'): 'https://github.com/danielgube/mito.git',
                            ('branch', '--show-current'): 'main',
                            ('rev-parse', 'HEAD'): 'abc',
                            ('rev-parse', 'origin/main'): 'abc'}.get(args, '')

                with patch('mito.publish.git', side_effect=fake_git) as git, \
                        patch('mito.publish.GitHub') as api, \
                        patch('mito.publish.commit_notes', return_value='### Commits incluidos\n\n- Mejora'), \
                        patch('mito.publish.subprocess.run') as run:
                    api.return_value.request.side_effect = [{'permissions': {'push': True}}, []]
                    run.return_value.returncode = 0
                    with self.assertRaises(YitoError):
                        publish(engine, log=lambda _: None)
                pending = root / 'core/state/pending-release.json'
                if fail_commit:
                    self.assertEqual(changelog.read_bytes(), original)
                    self.assertIn('0.1.0', source.read_text())
                    self.assertFalse(pending.exists())
                    self.assertIn(('restore', '--staged', '--', 'core/app/mito/__init__.py', 'CHANGELOG.md'),
                                  [call.args[1:] for call in git.call_args_list])
                else:
                    self.assertIn('## 0.1.1', committed[0])
                    self.assertIn('- Mejora', committed[0])
                    self.assertIn('Manual', committed[0])
                    self.assertIn('- Mejora', pending.read_text(encoding='utf-8'))

    def test_notes_use_previous_release_and_include_merged_commits(self):
        with patch('mito.publish.git', side_effect=[
            'abc\0Mejora\nbase\0release: v0.1.10\nold\0Anterior',
            'branch\0Corrección [MSI] <texto>\nabc\0Mejora',
        ]) as git:
            notes = commit_notes(Path('.'), 'abc')
        self.assertEqual(git.call_args.args[-1], 'base..abc')
        self.assertIn('Corrección \\[MSI\\] &lt;texto&gt;', notes)
        self.assertIn('/commit/branch', notes)
        self.assertIn('/compare/base...abc', notes)
        self.assertNotIn('release:', notes)

    def test_first_release_and_empty_range(self):
        with patch('mito.publish.git', side_effect=['abc\0Inicial', 'abc\0Inicial']) as git:
            notes = commit_notes(Path('.'), 'abc')
        self.assertEqual(git.call_args.args[-1], 'abc')
        self.assertIn('Inicial', notes)
        self.assertNotIn('/compare/', notes)
        with patch('mito.publish.git', side_effect=['abc\0release: v1.0.0', '']):
            self.assertIn('Sin cambios', commit_notes(Path('.'), 'abc'))

    def test_changelog_preserves_manual_sections_and_refuses_duplicates(self):
        original = '# Changelog\r\n\r\n## Sin publicar\r\n\r\nManual\r\n\r\n## 0.1.5 — fecha\r\n\r\nHistoria\r\n'
        result = updated_changelog(original, '0.1.11', 'Notas', '2026-09-16')
        self.assertIn('## Sin publicar\n\nManual\n\n## 0.1.11 — 2026-09-16\n\nNotas', result)
        self.assertTrue(result.endswith('## 0.1.5 — fecha\n\nHistoria\n'))
        with self.assertRaises(YitoError):
            updated_changelog(result, '0.1.11', 'Notas', '2026-09-16')
        self.assertIn('## 1.0.0', updated_changelog('# Changelog\n', '1.0.0', 'Notas', 'fecha'))

    def test_git_uses_resolved_executable_and_reports_cause(self):
        # En una pestaña de MITO `git` es un shim .cmd: CreateProcess sin shell no lo encuentra.
        import subprocess
        from mito import publish as module
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = str(root / 'core/apps/main/git/cmd/git.exe')
            ok = subprocess.CompletedProcess([], 0, stdout='abc\n', stderr='')
            failed = subprocess.CompletedProcess([], 128, stdout='', stderr='hint\nfatal: unable to access: schannel failure\n')
            with patch('mito.pocketools.git_executable', return_value=executable), \
                 patch('mito.publish.subprocess.run', side_effect=[ok, failed]) as run:
                self.assertEqual(module.git(root, 'rev-parse', 'HEAD'), 'abc')
                self.assertEqual(run.call_args.args[0], [executable, 'rev-parse', 'HEAD'])
                with self.assertRaisesRegex(YitoError, 'schannel failure'):
                    module.git(root, 'push')

    def test_numeric_increment(self):
        self.assertEqual(next_version('0.1.0',[]),'0.1.1')
        self.assertEqual(next_version('0.1.9',[{'tag_name':'v0.1.10'}]),'0.1.11')
        self.assertEqual(next_version('2.0.0',[{'tag_name':'v1.9.99'}]),'2.0.1')

    def test_release_kinds_reset_lower_numbers(self):
        for kind, expected in [('mayor', '3.0.1'), ('minor', '2.5.1'), ('hotfix', '2.4.10')]:
            with self.subTest(kind=kind):
                self.assertEqual(next_version('2.4.9', [], kind), expected)
                self.assertEqual(next_version('0.1.10', [{'tag_name': 'v2.4.9'}], kind), expected)
        with self.assertRaises(YitoError):
            next_version('0.1.10', [], 'invalid')

    def test_resume_uploads_and_publishes_without_bumping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            engine=Engine(root)
            source=root/'core/app/mito/__init__.py'
            source.parent.mkdir(parents=True)
            source.write_text('__version__ = "0.1.1"')
            archive=root/'test.zip'
            archive.write_bytes(b'zip')
            archive.with_suffix('.zip.sha256').write_text('checksum')
            pending=root/'core/state/pending-release.json'
            write_json(pending,{'version':'0.1.1','commit':'abc','body':'Notas guardadas','archive':str(archive),'sha256':hashlib.sha256(b'zip').hexdigest()})
            def git(root,*args,**kwargs):
                return {('rev-parse','--show-toplevel'):root.as_posix(),('remote','get-url','origin'):'https://github.com/danielgube/mito.git',('branch','--show-current'):'main',('rev-parse','HEAD'):'abc'}.get(args,'')
            calls=[]
            class API:
                def __init__(self,root):pass
                def request(self,method,path,data=None,**kwargs):
                    calls.append((method,path))
                    if path=='':return {'permissions':{'push':True}}
                    if path.startswith('/releases?per_page'):return []
                    if method=='POST' and path=='/releases':
                        self_body = data['body']
                        if self_body != 'Notas guardadas':
                            raise AssertionError(self_body)
                        return {'id':1,'assets':[],'draft':True}
                    if 'assets?' in path:return {'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
                    return {'html_url':'https://github.com/danielgube/mito/releases/tag/v0.1.1'}
            with patch('mito.publish.git',git),patch('mito.publish.GitHub',API),patch('mito.publish.build_release') as build:
                url=publish(engine,log=lambda _:None,kind='mayor')
                build.assert_not_called()
            self.assertTrue(url.endswith('v0.1.1'))
            self.assertFalse(pending.exists())
            self.assertEqual(calls[-1],('PATCH','/releases/1'))

    def test_stale_pending_release_is_discarded_only_if_unpublished_and_confirmed(self):
        from mito.storage import read_json
        for case in ('declined', 'published', 'confirmed'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                engine = Engine(root)
                source = root / 'core/app/mito/__init__.py'
                source.parent.mkdir(parents=True)
                source.write_text('__version__ = "0.1.1"\n', encoding='utf-8')
                pending = root / 'core/state/pending-release.json'
                write_json(pending, {'version': '0.1.1', 'commit': 'old', 'body': 'Notas'})
                asked = []

                def git(root, *args, **kwargs):
                    if args == ('push', 'origin', 'main'):
                        raise YitoError('red no disponible')
                    if args[:2] == ('ls-remote', 'origin'):
                        return 'old\trefs/tags/v0.1.1' if case == 'published' else ''
                    return {('rev-parse', '--show-toplevel'): root.as_posix(),
                            ('remote', 'get-url', 'origin'): 'https://github.com/danielgube/mito.git',
                            ('branch', '--show-current'): 'main',
                            ('rev-parse', 'HEAD'): 'abc',
                            ('rev-parse', 'origin/main'): 'abc',
                            ('show', '-s', '--format=%an', 'HEAD'): 'autor'}.get(args, '')

                with patch('mito.publish.git', git), patch('mito.publish.GitHub') as api, \
                        patch('mito.publish.commit_notes', return_value='- Iconos'), \
                        patch('mito.publish.subprocess.run') as run:
                    api.return_value.request.side_effect = [{'permissions': {'push': True}}, []]
                    run.return_value.returncode = 0
                    with self.assertRaises(YitoError) as error:
                        publish(engine, log=lambda _: None,
                                confirm=lambda lines: asked.append(lines) or case == 'confirmed')
                state = read_json(pending)
                if case == 'confirmed':
                    # Restarted from HEAD with the next version; it stopped at the push.
                    self.assertEqual(str(error.exception), 'red no disponible')
                    self.assertEqual((state['version'], state['commit']), ('0.1.2', 'abc'))
                else:
                    self.assertEqual(state['commit'], 'old')
                    self.assertIn('pendiente de otro commit', str(error.exception))
                    # Something already on GitHub is never offered for discarding.
                    self.assertEqual(len(asked), 0 if case == 'published' else 1)

    def test_github_retries_idempotent_requests_but_never_post(self):
        import http.client, io
        from mito import publish as module
        api = module.GitHub.__new__(module.GitHub)
        api.token = 'token'
        dropped = http.client.RemoteDisconnected('Remote end closed connection without response')
        ok = io.BytesIO(b'{"id": 1}')
        with patch.object(module.time, 'sleep'), patch('builtins.print'):
            api.opener = unittest.mock.Mock(**{'open.side_effect': [dropped, ok]})
            self.assertEqual(api.request('GET', '/releases?per_page=100&page=1'), {'id': 1})
            api.opener = unittest.mock.Mock(**{'open.side_effect': [dropped]})
            with self.assertRaisesRegex(YitoError, 'reanudar'):
                api.request('POST', '/releases', {'tag_name': 'v1.0.1'})
            self.assertEqual(api.opener.open.call_count, 1)

    def test_resume_reuses_draft_and_replaces_incomplete_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            engine = Engine(root)
            source = root/'core/app/mito/__init__.py'
            source.parent.mkdir(parents=True)
            source.write_text('__version__ = "0.1.1"')
            archive = root/'mito.zip'
            archive.write_bytes(b'zip')
            archive.with_suffix('.zip.sha256').write_text('checksum')
            write_json(root/'core/state/pending-release.json', {'version': '0.1.1', 'commit': 'abc', 'body': 'Notas',
                       'archive': str(archive), 'sha256': hashlib.sha256(b'zip').hexdigest()})
            def git(root, *args, **kwargs):
                return {('rev-parse', '--show-toplevel'): root.as_posix(), ('remote', 'get-url', 'origin'): 'https://github.com/danielgube/mito.git',
                        ('branch', '--show-current'): 'main', ('rev-parse', 'HEAD'): 'abc'}.get(args, '')
            draft = {'id': 7, 'tag_name': 'v0.1.1', 'draft': True,
                     'assets': [{'id': 9, 'name': 'mito.zip', 'state': 'starter'}]}
            calls = []
            class API:
                def __init__(self, root): pass
                def request(self, method, path, data=None, **kwargs):
                    calls.append((method, path))
                    if path == '': return {'permissions': {'push': True}}
                    if path.startswith('/releases?per_page'): return [draft]
                    if 'assets?' in path: return {'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
                    return {'html_url': 'https://github.com/danielgube/mito/releases/tag/v0.1.1'}
            with patch('mito.publish.git', git), patch('mito.publish.GitHub', API):
                publish(engine, log=lambda _: None)
            self.assertNotIn(('POST', '/releases'), calls)
            self.assertIn(('DELETE', '/releases/assets/9'), calls)
            self.assertEqual(calls[-1], ('PATCH', '/releases/7'))

    def test_dirty_checkout_does_not_authenticate_or_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp))
            def git(root,*args,**kwargs):
                return {('rev-parse','--show-toplevel'):root.as_posix(),('remote','get-url','origin'):'https://github.com/danielgube/mito.git',('branch','--show-current'):'main',('status','--porcelain'):' M README.md'}.get(args,'')
            with patch('mito.publish.git',git),patch('mito.publish.GitHub') as api:
                with self.assertRaises(YitoError):publish(engine)
                api.assert_not_called()
