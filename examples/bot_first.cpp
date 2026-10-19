// API: chooseMove(const BoardState& state) returns Move.
// state: side, move_number, board_size, pieces, legal_moves.
// Piece: id, side, type, x, y (coordinates 0..8).
// Move: piece_id, from, to, capture (optional<string>).
// state.at(x,y): const Piece* or nullptr for an empty square.
// state.board[y][x]: optional<Piece>. Types are provided.
// Return a legal move. No main() or input/output code.
Move chooseMove(const BoardState& state) {
    return state.legal_moves.front();
}
