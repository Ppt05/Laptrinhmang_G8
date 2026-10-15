#!/usr/bin/env python3
"""Sandbox image entry point. Compilation and bots run without network access."""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def compile_bot(language):
    source = sys.stdin.buffer.read(100_001)
    if len(source) > 100_000:
        emit({"ok": False, "error": "Mã nguồn vượt giới hạn 100 KB."})
        return 2
    work = pathlib.Path("/tmp/bot")
    work.mkdir(parents=True, exist_ok=True)
    if language == "python":
        try:
            compile(source.decode("utf-8"), "main.py", "exec")
        except Exception as exc:
            emit({"ok": False, "error": f"Python syntax error: {exc}"})
            return 2
        artifact = source
    elif language == "cpp":
        src = work / "main.cpp"
        target = work / "program"
        src.write_bytes(source)
        result = subprocess.run(
            ["g++", "-std=c++17", "-O2", "-pipe", "-static", "-s", str(src), "-o", str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=25,
            check=False,
        )
        if result.returncode != 0:
            emit({"ok": False, "error": result.stderr.decode("utf-8", "replace")[-6000:]})
            return 2
        artifact = target.read_bytes()
        if len(artifact) > 5_000_000:
            emit({"ok": False, "error": "Tệp thực thi vượt giới hạn 5 MB."})
            return 2
    else:
        emit({"ok": False, "error": "Ngôn ngữ không hợp lệ."})
        return 2
    emit({"ok": True, "artifact": base64.b64encode(artifact).decode("ascii")})
    return 0


def run_bot(language):
    path = pathlib.Path("/tmp/program")
    # Read only the requested bytes. Buffered reads can prefetch the first
    # game-state line and keep it away from the child bot process.
    def read_exact(size):
        chunks = []
        while size:
            chunk = os.read(0, size)
            if not chunk:
                break
            chunks.append(chunk)
            size -= len(chunk)
        return b"".join(chunks)

    header = read_exact(4)
    if len(header) != 4:
        return 70
    size = int.from_bytes(header, "big")
    if size < 1 or size > 5_000_000:
        return 70
    artifact = read_exact(size)
    if len(artifact) != size:
        return 70
    path.write_bytes(artifact)
    if language == "cpp":
        os.chmod(path, 0o500)
        command = [str(path)]
    elif language == "python":
        command = ["python3", "-I", "-S", str(path)]
    else:
        return 64
    try:
        proc = subprocess.Popen(command, stdin=0, stdout=1, stderr=subprocess.DEVNULL)
        return proc.wait()
    except Exception:
        return 71


def main():
    if len(sys.argv) < 2:
        return 64
    if sys.argv[1] == "compile" and len(sys.argv) == 3:
        return compile_bot(sys.argv[2])
    if sys.argv[1] == "run" and len(sys.argv) == 3:
        return run_bot(sys.argv[2])
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
