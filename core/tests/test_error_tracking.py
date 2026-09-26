import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from mito import error_tracking as tracking
from mito.__main__ import main
from mito.storage import YitoError


class ErrorTrackingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        tracking.flush()
        tracking._seen.clear()
        tracking._attempted.clear()

    def configure(self):
        (self.root / 'config.properties').write_text(
            'posthog.enabled=true\nposthog.host=https://eu.i.posthog.com\n'
            'posthog.api_key=phc_example\n', encoding='utf-8')

    def error(self):
        try:
            raise ValueError('password=private C:/Users/private/file.txt')
        except ValueError as exc:
            return exc

    def test_disabled_does_not_send(self):
        with patch.object(tracking, '_send') as send:
            self.assertFalse(tracking.capture(self.root, self.error()))
            send.assert_not_called()

    def test_trace_is_private_and_deduplicated(self):
        self.configure()
        with patch.object(tracking, '_send') as send:
            self.assertTrue(tracking.capture(self.root, self.error()))
            self.assertFalse(tracking.capture(self.root, self.error()))
            tracking.flush()
        send.assert_called_once()
        payload = send.call_args.args[3]
        self.assertEqual(payload['event'], '$exception')
        self.assertFalse(payload['properties']['$process_person_profile'])
        exception = payload['properties']['$exception_list'][0]
        self.assertEqual(exception['type'], 'ValueError')
        self.assertEqual(exception['stacktrace']['frames'][-1]['function'], 'error')
        self.assertNotIn('private', json.dumps(payload))
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_chain_and_interrupt(self):
        try:
            try:
                raise OSError('secret')
            except OSError as exc:
                raise RuntimeError('also secret') from exc
        except RuntimeError as exc:
            items = tracking.exception_list(exc, False)
        self.assertEqual([e['type'] for e in items], ['RuntimeError', 'OSError'])
        self.assertFalse(items[0]['mechanism']['handled'])
        self.assertFalse(tracking.capture(self.root, KeyboardInterrupt()))

    def test_process_limit(self):
        self.configure()
        with patch.object(tracking, '_send') as send:
            for index in range(21):
                error = type('Error' + str(index), (Exception,), {})()
                self.assertEqual(tracking.capture(self.root, error), index < 20)
            tracking.flush()
        self.assertEqual(send.call_count, 20)

    def test_incomplete_configuration_and_invalid_host(self):
        for host in ['', 'file:///tmp/events', 'https://user:password@example.com',
                     'https://eu.i.posthog.com/?secret=value']:
            with self.subTest(host=host):
                (self.root / 'config.properties').write_text(
                    'posthog.enabled=true\nposthog.api_key=phc_example\nposthog.host=' + host,
                    encoding='utf-8')
                with patch.object(tracking, '_send') as send:
                    self.assertFalse(tracking.capture(self.root, self.error()))
                    send.assert_not_called()

    def test_send_failure_is_not_retried(self):
        self.configure()
        with patch.object(tracking, '_send', side_effect=OSError('offline')) as send:
            self.assertTrue(tracking.capture(self.root, self.error()))
            tracking.flush()
        send.assert_called_once()
        self.assertEqual(tracking._pending.unfinished_tasks, 0)

    def test_http_payload_and_timeout(self):
        client = MagicMock()
        client.open.return_value.__enter__.return_value.status = 200
        with patch.object(tracking, 'opener', return_value=client) as opener:
            tracking._send(self.root, 'https://eu.i.posthog.com/', 'phc_example',
                           {'event': '$exception', 'properties': {}})
        request = client.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://eu.i.posthog.com/i/v0/e/')
        self.assertEqual(json.loads(request.data)['token'], 'phc_example')
        self.assertEqual(client.open.call_args.kwargs['timeout'], 5)
        self.assertIsInstance(opener.call_args.args[2], tracking.NoRedirect)

    def test_cli_reports_and_preserves_exit_code(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch('mito.__main__.capture') as capture, \
                patch('mito.__main__.flush'), patch('sys.stderr'):
            engine = engine_type.return_value
            engine.root = self.root
            error = YitoError('failed')
            engine.initialize.side_effect = error
            self.assertEqual(main(['installed']), 1)
        capture.assert_called_once_with(self.root, error, operation='cli.installed')

    def test_explicit_diagnostic_reports_http_result(self):
        self.configure()
        with patch.object(tracking, '_send', return_value=200) as send:
            self.assertEqual(tracking.check(self.root), 200)
        self.assertTrue(send.call_args.args[3]['properties']['mito_diagnostic'])
        self.assertEqual(send.call_args.kwargs['timeout'], 10)

    def test_diagnostic_failure_is_visible_and_not_recaptured(self):
        with patch('mito.__main__.Engine') as engine_type, \
                patch.object(tracking, 'check', side_effect=OSError('offline')), \
                patch('mito.__main__.capture') as capture, \
                patch('mito.__main__.flush'), patch('sys.stderr') as stderr:
            engine_type.return_value.root = self.root
            self.assertEqual(main(['posthog-check']), 1)
        capture.assert_not_called()
        engine_type.return_value.initialize.assert_not_called()
        self.assertIn('offline', ''.join(call.args[0] for call in stderr.write.call_args_list))

    def test_local_log_exists_when_posthog_is_disabled(self):
        self.assertFalse(tracking.capture(self.root, self.error(), operation='install'))
        path = self.root / 'logs/mito.log'
        record = json.loads(path.read_text().splitlines()[-1])
        self.assertEqual(record['operation'], 'install')
        self.assertEqual(record['exceptions'][0]['type'], 'ValueError')
        self.assertNotIn('private', path.read_text())
        self.assertFalse((path.parent / 'pending').exists())

    def test_offline_event_survives_and_retries_with_same_uuid(self):
        self.configure()
        with patch.object(tracking, '_send', side_effect=OSError('DNS failed')):
            tracking.capture(self.root, self.error())
            tracking.flush()
        files = list((self.root / 'logs/pending').glob('*.json'))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text())
        self.assertNotIn('phc_example', files[0].read_text())
        tracking._attempted.clear()  # Next process startup.
        with patch.object(tracking, '_send', return_value=200) as send:
            tracking.resume(self.root)
            tracking.flush()
        self.assertEqual(send.call_args.args[3]['uuid'], saved['event']['uuid'])
        self.assertEqual(send.call_args.args[3]['timestamp'], saved['event']['timestamp'])
        self.assertFalse(files[0].exists())

    def test_outbox_does_not_send_to_different_project_or_when_disabled(self):
        self.configure()
        with patch.object(tracking, '_send', side_effect=OSError('offline')):
            tracking.capture(self.root, self.error()); tracking.flush()
        for config in ('posthog.enabled=false', 'posthog.enabled=true\nposthog.host=https://eu.i.posthog.com\nposthog.api_key=phc_different'):
            tracking._attempted.clear()
            (self.root / 'config.properties').write_text(config)
            with patch.object(tracking, '_send') as send:
                tracking.resume(self.root); tracking.flush()
                send.assert_not_called()

    def test_disk_outbox_is_bounded_and_expires(self):
        import os
        import time
        self.configure()
        host, token = tracking._configuration(self.root)
        with patch.object(tracking, 'MAX_EVENTS', 2):
            paths = [tracking._store(self.root, tracking._payload(self.root, self.error(), True, 'test'), host, token) for _ in range(3)]
            self.assertEqual(len(list(paths[-1].parent.glob('*.json'))), 2)
            for path in paths[-1].parent.glob('*.json'):
                os.utime(path, (time.time()-8*86400, time.time()-8*86400))
            tracking._prune(paths[-1].parent)
            self.assertEqual(list(paths[-1].parent.glob('*.json')), [])

    def test_report_includes_message_context_and_no_geoip(self):
        from mito.diagnostics import step
        try:
            with step(self.root, 'install', 'extract', 'main/example'):
                raise OSError('Cannot extract: password="two words"')
        except OSError as exc:
            payload = tracking._payload(self.root, exc, True, 'ui.install')
        props = payload['properties']
        self.assertEqual(props['mito_phase'], 'extract')
        self.assertEqual(props['mito_component'], 'main/example')
        self.assertIn('Cannot extract', props['$exception_list'][0]['value'])
        self.assertNotIn('two words', json.dumps(props))
        self.assertTrue(props['$geoip_disable'])


if __name__ == '__main__':
    unittest.main()
