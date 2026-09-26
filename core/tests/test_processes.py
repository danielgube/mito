import subprocess
import sys
import time
import unittest

from mito.processes import ProcessJob


class ProcessJobTests(unittest.TestCase):
    def test_wait_empty_keeps_ownership_after_launcher_exits(self):
        group = ProcessJob()
        try:
            child = 'import time; time.sleep(1)'
            launcher = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",%r])' % child
            process = group.spawn([sys.executable, '-c', launcher])
            self.assertEqual(process.wait(timeout=10), 0)
            started = time.monotonic()
            group.wait_empty()
            self.assertGreater(time.monotonic() - started, .3)
        finally:
            group.terminate()
            group.close()


if __name__ == '__main__':
    unittest.main()
