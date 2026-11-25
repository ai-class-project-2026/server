"""Step ② + ④ of plan 9.2: dictionary hits minus whitelist ("시발점").

단어 목록은 두 가지예요 (기획서 9.8):
  certain    어떤 문장에서도 욕인 말 (badwords.txt, badwords_soundalike.txt)
  ambiguous  멀쩡한 뜻도 있는 말 (badwords_ambiguous.txt, badwords_soundalike_ambiguous.txt)
             → 모델이 있으면 모델이 판단하고, 모델이 없으면 욕으로 봐요
"""

from collections import Counter, defaultdict
from pathlib import Path

_here = Path(__file__).parent
_load = lambda name: [  # one word per line; "#" to end of line is a comment
    w
    for line in (_here / name).read_text(encoding="utf-8").splitlines()
    if (w := line.split("#")[0].strip())
]
# badwords_soundalike*.txt는 ml/moderation/gen_soundalike.py가 만들어요
BAD = list(dict.fromkeys(_load("badwords.txt") + _load("badwords_soundalike.txt")))
_amb = _load("badwords_ambiguous.txt") + _load("badwords_soundalike_ambiguous.txt")
AMBIGUOUS = [w for w in dict.fromkeys(_amb) if w not in BAD]  # 두 곳에 있으면 certain이 이겨요
WHITE = _load("whitelist.txt")


def _by_first_char(words: list[str]) -> dict[str, list[str]]:
    groups = defaultdict(list)
    for w in words:
        groups[w[0]].append(w)
    return groups


# Only words whose first letter is in the message can match, so a message checks
# a few dozen of the ~3,000 words instead of all of them.
_BAD = _by_first_char(BAD)
_AMBIGUOUS = _by_first_char(AMBIGUOUS)
_WHITE = _by_first_char(WHITE)


def _find_all(text: str, word: str):
    i = text.find(word)
    while i != -1:
        yield i, i + len(word)
        i = text.find(word, i + 1)


def _scan(norm: str, groups: dict[str, list[str]]):
    for c in set(norm):
        for w in groups.get(c, ()):
            for s, e in _find_all(norm, w):
                yield s, e, w


def _find(norm: str, groups: dict[str, list[str]]) -> list[tuple[int, int, str]]:
    allowed = list(_scan(norm, _WHITE))
    return sorted(
        (s, e, w)
        for s, e, w in _scan(norm, groups)
        if not any(a <= s and e <= b for a, b, _ in allowed)
    )


def find(norm: str) -> list[tuple[int, int]]:
    """정규화된 문장에서 certain 욕설의 위치. 화이트리스트 안에 있는 것은 빼요."""
    return [(s, e) for s, e, _ in _find(norm, _BAD)]


def find_ambiguous(norm: str) -> list[tuple[int, int]]:
    """정규화된 문장에서 ambiguous 단어(멀쩡한 뜻도 있는 말)의 위치. 화이트리스트는 빼요."""
    return [(s, e) for s, e, _ in _find(norm, _AMBIGUOUS)]


def find_words(norm: str) -> list[tuple[int, int, str, str]]:
    """걸린 곳 전부: (시작, 끝, 단어, 종류). 후보 읽기가 단어까지 알아야 해서 따로 있어요."""
    return [(s, e, w, "certain") for s, e, w in _find(norm, _BAD)] + [
        (s, e, w, "ambiguous") for s, e, w in _find(norm, _AMBIGUOUS)
    ]


def matches(norm: str) -> Counter:
    """걸린 단어를 (단어, 종류)별로 센 것. 종류는 "certain" 또는 "ambiguous".
    변형이 원문에 없던 욕을 새로 만들었는지 단어 하나하나 비교할 때 써요."""
    hits = Counter((w, "certain") for _, _, w in _find(norm, _BAD))
    hits.update((w, "ambiguous") for _, _, w in _find(norm, _AMBIGUOUS))
    return hits
