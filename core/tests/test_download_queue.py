import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mito.download_queue import STATE, DownloadQueue
from mito.engine import Engine
from mito.storage import YitoError, read_json, write_json


class DownloadQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(Path(self.tmp.name).resolve())
        self.engine.initialize()
        self.order = []
        refresh = patch.object(self.engine.catalog, 'refresh_keys', return_value=1)
        refresh.start()
        self.addCleanup(refresh.stop)

    def wait(self, queue):
        thread = queue.thread
        if thread is not None:
            thread.join(10)

    def status(self, queue):
        return [(item['target'], item['status']) for item in queue.snapshot()]

    def test_downloads_run_in_order_once_and_are_persisted(self):
        queue = DownloadQueue(self.engine)
        with patch.object(self.engine, 'download_only', side_effect=lambda target, **kwargs: self.order.append(target)):
            queue.enqueue('install', 'main/a')
            queue.enqueue('install', 'main/b')
            queue.enqueue('install', 'main/a')
            self.wait(queue)
        self.assertEqual(self.order, ['main/a', 'main/b'])
        self.assertEqual(self.status(queue), [('main/a', 'done'), ('main/b', 'done')])
        self.assertEqual(read_json(self.engine.root/STATE),
                         [{'kind': 'install', 'target': 'main/a', 'status': 'done'},
                          {'kind': 'install', 'target': 'main/b', 'status': 'done'}])

    def test_reopening_resumes_pending_and_drops_downloads_no_longer_available(self):
        write_json(self.engine.root/STATE, [{'kind': 'install', 'target': 'main/c', 'status': 'pending'},
                                           {'kind': 'install', 'target': 'main/gone', 'status': 'done'},
                                           {'kind': 'other', 'target': 'x'}, 'invalid'])
        queue = DownloadQueue(self.engine)
        with patch.object(self.engine, 'download_only', side_effect=lambda target, **kwargs: self.order.append(target)):
            self.assertEqual(queue.load(), 1)
            self.wait(queue)
        self.assertEqual(self.order, ['main/c'])
        self.assertEqual(self.status(queue), [('main/c', 'done')])

    def test_failure_is_reported_and_the_queue_continues(self):
        logs = []
        def fake(target, **kwargs):
            if target == 'main/bad':
                raise YitoError('network down')
            self.order.append(target)
        queue = DownloadQueue(self.engine, log=lambda message, level='info': logs.append((level, message)))
        with patch.object(self.engine, 'download_only', side_effect=fake):
            queue.enqueue('install', 'main/bad')
            queue.enqueue('install', 'main/ok')
            self.wait(queue)
        self.assertEqual(self.status(queue), [('main/bad', 'failed'), ('main/ok', 'done')])
        self.assertIn('network down', queue.snapshot()[0]['message'])
        self.assertTrue(any(level == 'error' for level, _ in logs))
        # A failed item is not persisted: reopening does not retry it on its own.
        self.assertEqual(read_json(self.engine.root/STATE), [{'kind': 'install', 'target': 'main/ok', 'status': 'done'}])

    def test_cancelling_a_pending_item_skips_it(self):
        started, release = threading.Event(), threading.Event()
        def fake(target, **kwargs):
            started.set()
            release.wait(10)
            self.order.append(target)
        queue = DownloadQueue(self.engine)
        with patch.object(self.engine, 'download_only', side_effect=fake):
            queue.enqueue('install', 'main/first')
            started.wait(10)
            second = queue.enqueue('install', 'main/second')
            queue.cancel(second['id'])
            self.assertEqual(queue.active(), 1)
            release.set()
            self.wait(queue)
        self.assertEqual(self.order, ['main/first'])
        self.assertEqual(self.status(queue), [('main/first', 'done'), ('main/second', 'cancelled')])

    def test_updates_use_the_same_checks_as_updating(self):
        queue = DownloadQueue(self.engine)
        with patch('mito.components.download_update', side_effect=lambda engine, target, log, confirm_unverified=None:
                   self.order.append(('update', target))):
            queue.enqueue('update', 'main/tool')
            self.wait(queue)
        self.assertEqual(self.order, [('update', 'main/tool')])


if __name__ == '__main__':
    unittest.main()
