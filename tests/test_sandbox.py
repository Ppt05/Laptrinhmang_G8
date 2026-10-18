import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox import entrypoint


class SandboxTests(unittest.TestCase):
    def test_compiler_deadline_is_submission_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch.object(entrypoint.pathlib, "Path", return_value=Path(directory)), \
                 patch.object(entrypoint.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"int main(){}"))), \
                 patch.object(entrypoint.subprocess, "run", side_effect=subprocess.TimeoutExpired("g++",25)), \
                 contextlib.redirect_stdout(output):
                code=entrypoint.compile_bot("cpp")
            result=json.loads(output.getvalue())
            self.assertEqual(code,2)
            self.assertFalse(result["ok"])
            self.assertIn("25 seconds",result["error"])
