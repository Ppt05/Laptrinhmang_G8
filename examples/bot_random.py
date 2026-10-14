"""Bot mẫu hợp lệ OTTv2: chọn ngẫu nhiên một nước hợp lệ."""
import json
import random
import sys


for line in sys.stdin:
    state = json.loads(line)
    moves = state["legal_moves"]
    if not moves:
        break
    move = random.choice(moves)
    print(json.dumps({"piece_id": move["piece_id"], "to": move["to"]}), flush=True)
