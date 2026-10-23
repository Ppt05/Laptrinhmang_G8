"""Database-free engine shared by the trusted worker and local lab."""
import json
from .game_setup import coord, new_game, square

BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

def legal_moves(game, side):
    occupied = {(p["x"], p["y"]): p for p in game["pieces"]}
    result = []
    for p in game["pieces"]:
        if p["side"] != side:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                x, y = p["x"] + dx, p["y"] + dy
                if not (0 <= x < 9 and 0 <= y < 9):
                    continue
                other = occupied.get((x, y))
                if other and (other["side"] == side or BEATS[p["type"]] != other["type"]):
                    continue
                result.append({"piece_id": p["id"], "from": square(p["x"], p["y"]), "to": square(x, y),
                               "capture": other["id"] if other else None})
    return result

def apply_move(game, move):
    nxt = json.loads(json.dumps(game))
    piece = next(p for p in nxt["pieces"] if p["id"] == move["piece_id"])
    x, y = coord(move["to"])
    captured = next((p for p in nxt["pieces"] if p["x"] == x and p["y"] == y), None)
    if captured:
        nxt["pieces"].remove(captured)
    piece["x"], piece["y"] = x, y
    goal = (0, 0) if piece["side"] == "north" else (8, 8)
    if (x, y) == goal:
        nxt["winner"], nxt["win_reason"] = piece["side"], "goal"
    elif captured and not any(p["side"] == captured["side"] and p["type"] == captured["type"] for p in nxt["pieces"]):
        nxt["winner"], nxt["win_reason"] = piece["side"], "elimination"
    else:
        nxt["turn"] = "north" if piece["side"] == "south" else "south"
    nxt["move_number"] += 1
    return nxt

def position_key(game):
    pieces = sorted((p["id"], p["side"], p["type"], p["x"], p["y"]) for p in game["pieces"])
    return json.dumps([game["turn"], pieces], separators=(",", ":"))

def parse_reply(raw, choices):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        reply = json.loads(raw.decode("utf-8"), object_pairs_hook=unique) if raw and len(raw) <= 256 and raw.endswith(b"\n") else None
        if not isinstance(reply, dict) or set(reply) != {"piece_id", "to"} or not all(isinstance(v, str) for v in reply.values()):
            return None
        return next((mv for mv in choices if mv["piece_id"] == reply["piece_id"] and mv["to"] == reply["to"]), None)
    except (UnicodeDecodeError, ValueError):
        return None


class MatchEngine:
    """One match with the same end conditions used by the tournament worker."""

    def __init__(self, max_moves=200):
        self.game = new_game()
        self.max_moves = max_moves
        self.turns = []
        self.repetitions = {position_key(self.game): 1}

    @property
    def finished(self):
        return bool(self.game['winner'] or self.game['win_reason'])

    def observation(self):
        if self.finished:
            return None
        if self.game['move_number'] > self.max_moves:
            self.game['win_reason'] = 'move_limit_draw'
            return None
        choices = legal_moves(self.game, self.game['turn'])
        if not choices:
            self.game['win_reason'] = 'no_legal_moves_draw'
            return None
        return {'protocol_version': 2, 'move_number': self.game['move_number'],
                'side': self.game['turn'], 'board_size': 9,
                'pieces': json.loads(json.dumps(self.game['pieces'])),
                'legal_moves': choices}

    def forfeit(self):
        side = self.game['turn']
        self.turns.append({'move_number': self.game['move_number'], 'side': side,
                           'error': 'invalid_move_or_timeout'})
        self.game['winner'] = 'north' if side == 'south' else 'south'
        self.game['win_reason'] = 'invalid_move_or_timeout'

    def advance(self, move):
        observation = self.observation()
        if observation is None:
            raise ValueError('Match has ended')
        selected = next((candidate for candidate in observation['legal_moves']
                         if candidate['piece_id'] == move.get('piece_id') and
                         candidate['to'] == move.get('to')), None)
        if selected is None:
            self.forfeit()
            return
        self.turns.append({'move_number': self.game['move_number'],
                           'side': self.game['turn'], **selected})
        self.game = apply_move(self.game, selected)
        if not self.game['winner']:
            if not legal_moves(self.game, self.game['turn']):
                self.game['win_reason'] = 'no_legal_moves_draw'
            else:
                key = position_key(self.game)
                self.repetitions[key] = self.repetitions.get(key, 0) + 1
                if self.repetitions[key] >= 3:
                    self.game['win_reason'] = 'threefold_repetition_draw'
        self.observation()

    def detail(self):
        return {'protocol_version': 2, 'winner_side': self.game['winner'],
                'win_reason': self.game['win_reason'], 'turns': self.turns,
                'final_pieces': self.game['pieces']}
