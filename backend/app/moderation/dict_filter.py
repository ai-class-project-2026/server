"""Step ② + ④ of plan 9.2: dictionary hits minus whitelist ("시발점")."""

from pathlib import Path

_here = Path(__file__).parent
_load = lambda name: [
    w for w in (_here / name).read_text(encoding="utf-8").split() if not w.startswith("#")
]
BAD = _load("badwords.txt")
WHITE = _load("whitelist.txt")


def _find_all(text: str, word: str):
    i = text.find(word)
    while i != -1:
        yield i, i + len(word)
        i = text.find(word, i + 1)


def find(norm: str) -> list[tuple[int, int]]:
    """Spans in the normalized text. ponytail: substring scan per word, fine for a
    few hundred words; switch to pyahocorasick when the list grows."""
    allowed = [s for w in WHITE for s in _find_all(norm, w)]
    return [
        (s, e)
        for w in BAD
        for s, e in _find_all(norm, w)
        if not any(a <= s and e <= b for a, b in allowed)
    ]
