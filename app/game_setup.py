"""Shared starting position for the OTTv2 engine and match replay."""

FILES = "abcdefghi"
INITIAL = [
    ("N01", "north", "paper", "e8"), ("N02", "north", "rock", "f8"),
    ("N03", "north", "scissors", "e7"), ("N04", "north", "paper", "f7"),
    ("N05", "north", "rock", "g7"), ("N06", "north", "scissors", "f6"),
    ("N07", "north", "paper", "g6"), ("N08", "north", "rock", "h6"),
    ("N09", "north", "scissors", "g5"), ("N10", "north", "paper", "h5"),
    ("S01", "south", "paper", "b5"), ("S02", "south", "scissors", "c5"),
    ("S03", "south", "rock", "b4"), ("S04", "south", "paper", "c4"),
    ("S05", "south", "scissors", "d4"), ("S06", "south", "rock", "c3"),
    ("S07", "south", "paper", "d3"), ("S08", "south", "scissors", "e3"),
    ("S09", "south", "rock", "d2"), ("S10", "south", "paper", "e2"),
]


def coord(square):
    return FILES.index(square[0]), int(square[1:]) - 1


def square(x, y):
    return f"{FILES[x]}{y + 1}"


def new_game():
    return {
        "pieces": [
            {"id": piece_id, "side": side, "type": kind,
             "x": coord(position)[0], "y": coord(position)[1]}
            for piece_id, side, kind, position in INITIAL
        ],
        "turn": "south", "move_number": 1, "winner": None, "win_reason": None,
    }
