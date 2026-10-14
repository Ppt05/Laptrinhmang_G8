"""Bot mẫu hợp lệ OTTv2: ưu tiên ăn quân, sau đó tiến về ô đích."""
import json
import sys


def distance_to_goal(side, square):
    x = ord(square[0]) - ord("a")
    y = int(square[1:]) - 1
    goal = (0, 0) if side == "north" else (8, 8)
    return abs(x - goal[0]) + abs(y - goal[1])


for line in sys.stdin:
    state = json.loads(line)
    moves = state["legal_moves"]
    if not moves:
        break
    side = state["side"]
    move = max(moves, key=lambda m: (
        m["capture"] is not None,
        distance_to_goal(side, m["from"]) - distance_to_goal(side, m["to"]),
    ))
    print(json.dumps({"piece_id": move["piece_id"], "to": move["to"]}), flush=True)
