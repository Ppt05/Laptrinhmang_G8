"""Bot mẫu hợp lệ OTTv2: chọn ngẫu nhiên một nước hợp lệ."""
import random


def choose_move(state):
    return random.choice(state["legal_moves"])
