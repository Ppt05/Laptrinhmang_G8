# API: choose_move(state) returns a move dict.
# state: side, move_number, board_size, pieces, legal_moves.
# Piece: id, side, type, x, y (coordinates 0..8).
# Move: piece_id, from, to, capture (None or enemy ID).
# Return a legal move, or {"piece_id": ..., "to": ...}.
# Judge handles input/output. Add helper functions as needed.
def choose_move(state):
    return state["legal_moves"][0]
