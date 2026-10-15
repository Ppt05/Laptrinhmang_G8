import subprocess
import sys
import time
import unittest

from app.worker import read_line_timeout


class WorkerProtocolTests(unittest.TestCase):
    def start_writer(self, source):
        return subprocess.Popen(
            [sys.executable, "-u", "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def test_complete_line(self):
        proc = self.start_writer("import sys; sys.stdout.buffer.write(b'{}\\n'); sys.stdout.flush()")
        try:
            self.assertEqual(read_line_timeout(proc.stdout, 1, bytearray()), b"{}\n")
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()

    def test_partial_line_times_out(self):
        proc = self.start_writer(
            "import sys,time; sys.stdout.buffer.write(b'{'); sys.stdout.flush(); time.sleep(5)"
        )
        try:
            started = time.monotonic()
            self.assertIsNone(read_line_timeout(proc.stdout, 0.2, bytearray()))
            self.assertLess(time.monotonic() - started, 1)
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()

    def test_second_line_is_buffered(self):
        proc = self.start_writer(
            "import sys,time; sys.stdout.buffer.write(b'first\\nsecond\\n'); "
            "sys.stdout.flush(); time.sleep(5)"
        )
        pending = bytearray()
        try:
            self.assertEqual(read_line_timeout(proc.stdout, 1, pending), b"first\n")
            self.assertEqual(read_line_timeout(proc.stdout, 1, pending), b"second\n")
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()

    def test_overlong_line_is_rejected(self):
        proc = self.start_writer(
            "import sys,time; sys.stdout.buffer.write(b'x'*300+b'\\n'); "
            "sys.stdout.flush(); time.sleep(5)"
        )
        try:
            self.assertIsNone(read_line_timeout(proc.stdout, 1, bytearray()))
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
