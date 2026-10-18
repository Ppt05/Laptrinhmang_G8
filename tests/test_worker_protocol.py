import subprocess
import sys
import time
import unittest

from app.worker import read_line_timeout, parse_reply


class WorkerProtocolTests(unittest.TestCase):
    def test_official_output_schema(self):
        moves=[{"piece_id":"S01","to":"b2"}]
        self.assertEqual(parse_reply(b'{"piece_id":"S01","to":"b2"}\n',moves),moves[0])
        for raw in (b'{"piece_id":"S01","to":"b2","debug":1}\n',
                    b'{"piece_id":"S01","to":"a1","to":"b2"}\n',
                    b'{"piece_id":1,"to":"b2"}\n', b'["S01","b2"]\n',
                    b'{"piece_id":"S01","to":"b2"}', b'{"piece_id":"S01","to":"b3"}\n'):
            self.assertIsNone(parse_reply(raw,moves))
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
