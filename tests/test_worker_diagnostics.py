import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import worker


class WorkerDiagnosticsTests(unittest.TestCase):
    def test_empty_compiler_output_preserves_missing_image_error(self):
        process = Mock(returncode=125)
        process.communicate.return_value = (b"", b"Error response from daemon: No such image: sha256:missing")
        source = SimpleNamespace(source="def choose_move(s): return s['legal_moves'][0]", language="python")
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, "ARTIFACTS", Path(directory)), \
             patch.object(worker, "docker_cmd", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "exit 125.*No such image"):
                worker.compile_submission(None, source)

    def test_stderr_is_captured_only_for_compilation(self):
        with patch.object(worker.subprocess, "Popen") as popen:
            worker.docker_cmd("compile", "python")
            self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
            worker.docker_cmd("run", "python")
            self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
