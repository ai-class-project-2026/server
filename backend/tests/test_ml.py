"""Training/eval glue: synthetic records must feed both the trainer and the scorer."""

import random
import sys

sys.path[:0] = ["ml/schedule/synth", "ml/schedule", "eval/schedule"]
import scenarios
from generate import Turn, check, to_record
from score import score
from train_lora import to_chat


def test_scenario_to_training_row():
    rng = random.Random(1)
    for _ in range(50):
        sc = scenarios.make(rng)
        turns = [
            Turn(speaker=sc["participants"][i % len(sc["participants"])], text="ㅇㅋ")
            for i in range(8)
        ]
        assert check(sc, turns)
        row = to_chat(to_record(sc, turns))
        assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]


def test_check_drops_wrong_dates_and_strangers():
    sc = {
        "participants": ["a", "b"],
        "ref_date": "2026-09-30",
        "events": [{"date": "2026-10-02"}],
    }
    ok = [Turn(speaker="a", text="모레 ㄱ")] + [Turn(speaker="b", text="ㅇㅋ")] * 5
    assert check(sc, ok)
    assert not check(sc, [Turn(speaker="a", text="내일 ㄱ")] + ok[1:])  # 내일 ≠ gold
    assert not check(sc, [Turn(speaker="z", text="모레")] + ok[1:])


def test_score():
    ev = lambda d, s, st: {"date": d, "start": s, "status": st}
    golds = [
        {"type": "confirm", "events": [ev("2026-10-15", "19:00", "confirmed")]},
        {"type": "proposal_only", "events": [ev("2026-10-16", "18:00", "proposed")]},
        {"type": "cancel", "events": [ev("2026-10-17", "17:00", "cancelled")]},
    ]
    preds = [
        {"events": [ev("2026-10-15", "19:00:00", "confirmed")]},
        {"events": [ev("2026-10-16", "18:00", "confirmed")]},  # false registration
        {"events": [ev("2026-10-17", "17:00", "cancelled")]},
    ]
    m = score(golds, preds)
    assert m["false_registration"] == 0.5 and m["cancel_recall"] == 1.0
    assert m["confirmed_f1"] == round(2 * 0.5 * 1 / 1.5, 4)
    assert m["f1_by_type"]["confirm"] == 1.0
