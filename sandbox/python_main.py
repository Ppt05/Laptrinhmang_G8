"""Appended by the judge after the submitted choose_move function."""
def _ottv2_run():
    import json
    import sys

    for line in sys.stdin:
        state = json.loads(line)
        move = choose_move(state)
        if not isinstance(move, dict) or not all(isinstance(move.get(key), str) for key in ("piece_id", "to")):
            raise TypeError("choose_move(state) must return a move dictionary with piece_id and to strings")
        print(json.dumps({"piece_id": move["piece_id"], "to": move["to"]}), flush=True)


_ottv2_run()
