"""Bypass-style augmentation rules (plan 9.3). Owned by part 3.

Each rule: (text, rng) -> text. Label stays the same, so one labeled sentence
becomes many. Apply to clean sentences too (plan 9.3 last line).
"""

import random

from .similar_sound import similar_sound


def insert_digit(text: str, rng: random.Random) -> str:
    """시발 → 시1발 (a digit between two hangul syllables)."""
    spots = [
        i
        for i in range(1, len(text))
        if "가" <= text[i - 1] <= "힣" and "가" <= text[i] <= "힣"
    ]
    if not spots:
        return text
    i = rng.choice(spots)
    return text[:i] + rng.choice("1!l") + text[i:]


# TODO(part 3): insert_symbol (씨@발), split_jamo (ㅅㅣ발), move_final (시ㅂ알), chosung (ㅅㅂ),
# split_space (시 발), keyboard (tlqkf), stretch (시이이발), combo.
RULES = [insert_digit, similar_sound]


def augment(text: str, rng: random.Random, n: int = 3) -> list[str]:
    return [rng.choice(RULES)(text, rng) for _ in range(n)]
