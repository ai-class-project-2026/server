"""Step ② + ④ of plan 9.2: dictionary hits minus whitelist ("시발점").

단어 목록은 두 가지예요 (기획서 9.8):
  certain    어떤 문장에서도 욕인 말 (badwords.txt, badwords_soundalike.txt, badwords_phrase.txt)
  ambiguous  멀쩡한 뜻도 있는 말 (badwords_ambiguous.txt, badwords_soundalike_ambiguous.txt)
             → 모델이 있으면 모델이 판단하고, 모델이 없으면 욕으로 봐요
강조하려고 붙인 "개"("개웃기네")는 badwords_intensifier.txt에 따로 있어요: "개" 한 글자만 가려요.
"""

from collections import defaultdict
from pathlib import Path

_here = Path(__file__).parent
_load = lambda name: [  # one word per line; "#" to end of line is a comment
    w
    for line in (_here / name).read_text(encoding="utf-8").splitlines()
    if (w := line.split("#")[0].strip())
]
# badwords_soundalike*.txt는 ml/moderation/gen_soundalike.py가 만들어요
# 소리만 바꿔 쓴 문장형 욕 ("너임마 청년"): 띄어 썼어도 글이 거기서 끝나면 욕이에요
PHRASES = _load("badwords_phrase.txt")
BAD = list(dict.fromkeys(_load("badwords.txt") + _load("badwords_soundalike.txt") + PHRASES))
_amb = _load("badwords_ambiguous.txt") + _load("badwords_soundalike_ambiguous.txt")
AMBIGUOUS = [w for w in dict.fromkeys(_amb) if w not in BAD]  # 두 곳에 있으면 certain이 이겨요
WHITE = _load("whitelist.txt")
# 강조하려고 붙인 "개" ("개웃기네"): 뒤에 오는 말까지 맞아야 걸려요
INTENSIFIER = ["개" + w for w in _load("badwords_intensifier.txt")]


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
_INTENSIFIER = _by_first_char(INTENSIFIER)


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


def whitelisted(norm: str) -> list[tuple[int, int]]:
    """화이트리스트가 풀어 준 사전 낱말의 위치 ("씨발아 실험"의 "씨발", "시발점"의 "시발")."""
    allowed = list(_scan(norm, _WHITE))
    return [
        (s, e)
        for groups in (_BAD, _AMBIGUOUS)
        for s, e, _ in _scan(norm, groups)
        if any(a <= s and e <= b for a, b, _ in allowed)
    ]


def find_intensifier(norm: str) -> list[tuple[int, int, str]]:
    """강조하려고 붙인 "개"와 뒤에 오는 말: (시작, 끝, 단어). 가릴 곳은 맨 앞의 "개" 한 글자예요."""
    return _find(norm, _INTENSIFIER)


def find_words(norm: str) -> list[tuple[int, int, str, str]]:
    """걸린 곳 전부: (시작, 끝, 단어, 종류). 후보 읽기가 단어까지 알아야 해서 따로 있어요."""
    return [(s, e, w, "certain") for s, e, w in _find(norm, _BAD)] + [
        (s, e, w, "ambiguous") for s, e, w in _find(norm, _AMBIGUOUS)
    ]

