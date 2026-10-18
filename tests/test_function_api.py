"""Exercise the supplied callback harnesses without a database or Docker."""
import base64
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox import entrypoint


def compile_source(language, source, directory):
    output = io.StringIO()
    with patch.object(entrypoint.pathlib, "Path", return_value=Path(directory)), \
         patch.object(entrypoint.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(source.encode()))), \
         contextlib.redirect_stdout(output):
        code = entrypoint.compile_bot(language)
    return code, json.loads(output.getvalue())


def states():
    # Escaped identifiers also verify that parsing and output quoting round-trip.
    identifier = 'S"\\\n🌱'
    south = {"legal_moves": [{"capture": None, "to": "b2", "from": "a1", "piece_id": identifier}],
             "pieces": [{"y": 0, "x": 0, "type": "paper", "side": "south", "id": identifier},
                        {"id": "N01", "side": "north", "type": "rock", "x": 1, "y": 1}],
             "board_size": 9, "move_number": 1, "side": "south", "protocol_version": 2}
    north = {"protocol_version": 2, "side": "north", "move_number": 4, "board_size": 9,
             "pieces": [{"id": "N01", "side": "north", "type": "rock", "x": 2, "y": 3}],
             "legal_moves": [{"piece_id": "N01", "from": "c4", "to": "b3", "capture": identifier}]}
    return [south, north]


class FunctionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        source = r'''
Move chooseMove(const BoardState& state) {
    if (state.protocol_version != 2 || state.board_size != 9 || state.move_number < 1)
        throw std::runtime_error("metadata");
    if (state.at(-1, 0) || state.at(0, 9)) throw std::runtime_error("bounds");
    for (const auto& piece : state.pieces) {
        const Piece* square = state.at(piece.x, piece.y);
        if (!square || square->id != piece.id || square->type != piece.type || square->side != piece.side)
            throw std::runtime_error("piece lookup");
    }
    int occupied = 0;
    for (int y = 0; y < 9; ++y) for (int x = 0; x < 9; ++x)
        if (state.board[y][x]) ++occupied;
    if (occupied != int(state.pieces.size())) throw std::runtime_error("stale board");
    if (state.side == "north" && (state.at(0, 0) || state.at(1, 1)))
        throw std::runtime_error("previous position retained");
    const Move& move = state.legal_moves.at(0);
    if (move.from.empty() || move.to.empty() || move.piece_id.empty()) throw std::runtime_error("move fields");
    if (bool(move.capture) != (state.side == "north")) throw std::runtime_error("capture");
    return move;
}
'''
        code, result = compile_source("cpp", source, cls.temp.name)
        if code != 0:
            cls.temp.cleanup()
            raise AssertionError(result)
        cls.program = Path(cls.temp.name) / "api-test"
        cls.program.write_bytes(base64.b64decode(result["artifact"]))
        cls.program.chmod(0o700)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_cpp_typed_board_multiple_turns_both_sides_and_escaped_ids(self):
        inputs = states()
        result = subprocess.run([str(self.program)], input="".join(json.dumps(s) + "\n" for s in inputs),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],
                         [{"piece_id": s["legal_moves"][0]["piece_id"], "to": s["legal_moves"][0]["to"]} for s in inputs])

    def test_cpp_rejects_invalid_json_and_positions(self):
        bad = states()[0]
        bad["pieces"][0]["x"] = 9
        for line in (json.dumps(bad), '{"side":"south","side":"north"}', '{"side":"\\ud800"}'):
            with self.subTest(line=line):
                result = subprocess.run([str(self.program)], input=line + "\n", text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_python_callback_strips_metadata_across_turns(self):
        source = 'def choose_move(state):\n return state["legal_moves"][0]\n'
        code, result = compile_source("python", source, self.temp.name)
        self.assertEqual(code, 0, result)
        result = subprocess.run([sys.executable, "-I", "-S", "-c", base64.b64decode(result["artifact"]).decode()],
                                input="".join(json.dumps(s) + "\n" for s in states()), text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],
                         [{"piece_id": s["legal_moves"][0]["piece_id"], "to": s["legal_moves"][0]["to"]} for s in states()])

    def test_full_program_without_callback_reports_compile_error(self):
        for language, source in (("python", "print('old program')"), ("cpp", "int main() {}")):
            with self.subTest(language=language):
                code, result = compile_source(language, source, self.temp.name)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                self.assertIn("choose_move" if language == "python" else "chooseMove", result["error"])


if __name__ == "__main__":
    unittest.main()
