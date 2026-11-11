"""Part 3: one test per rule, with concrete input → output examples."""

import random
import sys

sys.path.insert(0, "ml/moderation")
from augment import insert_digit


def test_insert_digit():
    out = insert_digit("시발", random.Random(0))
    assert out[0] == "시" and out[-1] == "발" and len(out) == 3
    assert insert_digit("ab", random.Random(0)) == "ab"  # no hangul pair → unchanged
