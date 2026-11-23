"""Similar-sound swaps (plan 9.3 유사 발음): 시발 → 쉬발, 씨벌, 쓰빨, 시팔 ...

People dodge a filter by swapping letters that sound almost the same. The
tables below list those swaps, per part of a syllable. Two users:
  - augmentation: similar_sound(text, rng) swaps one syllable of a sentence
  - ml/moderation/gen_soundalike.py: word_variants("시발") lists every variant
    for the dictionary filter
"""

import itertools
import random

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = "\0ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"  # \0 = no final

# first consonant: plain / tense / aspirated sound alike (시발 → 씨발, 시팔)
ONSET_GROUPS = ["ㅅㅆ", "ㅂㅃㅍ", "ㅈㅉㅊ", "ㄱㄲㅋ", "ㄷㄸㅌ"]
# vowel → vowels that sound close to it (시 → 쉬, 싀, 스 / 발 → 벌, 뱔)
VOWEL_SWAPS = {
    "ㅣ": "ㅟㅢㅡ", "ㅟ": "ㅣㅢ", "ㅢ": "ㅣㅟㅡ", "ㅡ": "ㅢㅜ",
    "ㅏ": "ㅑㅓ", "ㅑ": "ㅏ", "ㅓ": "ㅕㅏ", "ㅕ": "ㅓㅑ",
    "ㅐ": "ㅔㅒㅖㅙㅞㅚ", "ㅔ": "ㅐㅖㅒㅞㅙㅚ", "ㅒ": "ㅐㅔ", "ㅖ": "ㅔㅐ",
    "ㅙ": "ㅞㅚㅐ", "ㅞ": "ㅙㅚㅔ", "ㅚ": "ㅙㅞ",
    "ㅗ": "ㅛㅓ", "ㅛ": "ㅗ", "ㅜ": "ㅠ", "ㅠ": "ㅜ",  # ㅗ → ㅓ: 놈 → 넘
}  # fmt: skip
# final consonant: finals that are pronounced the same (좆 → 좃, 졷 / 닥 → 닼)
CODA_GROUPS = ["ㄱㄲㅋ", "ㅂㅍ", "ㅅㅆㄷㅈㅊㅌ"]
# only on the last syllable, where it reads like a joke spelling (병신 → 병싱)
LAST_CODA = {"ㄴ": "ㅇ"}

# 글자를 바꿔서 이 단어가 되면 안 돼요 (창녀 → 장녀: 멀쩡한 말인데 라벨이 1로 남아요).
# gen_soundalike.py도 이 목록으로 사전에서 빼므로 둘이 같은 목록을 써요.
# 테스트에서 새로 찾으면 여기에 더해요.
NOT_BAD = {
    "장녀", "장년", "정녀", "세기", "새기", "세끼", "쐐기", "변신", "평신", "거저",
    "까져", "게놈", "에미", "병진", "병선", "촌나", "닦쳐", "색스", "턱쳐",
    "따따이", "또라히", "캐년", "시뻘", "풍신", "상년", "엠청", "거져", "병승",
    # "딱 쳐다봤다" → 딱쳐다봤다 (a 1-letter word loses its space in normalize)
    "딱쳐", "딲쳐", "탁쳐", "떡쳐", "전너", "전냐",
    # "좋더라이거" written without spaces
    "더라이", "더라의", "더라으", "더라위", "더랴이", "더러이",
    # 하게서리 / 시계소리 / 달달이 / 판도라의: 멀쩡한 말 속에 들어 있어요
    "게서리", "계소리", "달달이", "도라의",
    # 서비스발표처럼 "스 발"이 붙은 말, "가고 싶년 ..." 같은 오타와 띄어쓰기 없는 글
    "스발", "싶년",
    # "올 게 년말에"(한 글자 뒤 띄어쓰기가 지워져요), "김씨뻘 친척"
    "게년", "씨뻘",
}  # fmt: skip


def is_syllable(c: str) -> bool:
    return "가" <= c <= "힣"


def _split(c: str) -> tuple[str, str, str]:
    n = ord(c) - 0xAC00
    return CHO[n // 588], JUNG[n % 588 // 28], JONG[n % 28]


def _join(cho: str, jung: str, jong: str) -> str:
    return chr(0xAC00 + CHO.index(cho) * 588 + JUNG.index(jung) * 28 + JONG.index(jong))


def _alts(c: str, groups: list[str]) -> list[str]:
    return next(([x for x in g if x != c] for g in groups if c in g), [])


def syllable_variants(syl: str, last: bool = False) -> list[str]:
    """시 → [스, 쉬, 싀, 씨, 쓰, 쒸, 씌]. Every combination of the swaps above."""
    cho, jung, jong = _split(syl)
    chos = [cho, *_alts(cho, ONSET_GROUPS)]
    jungs = [jung, *VOWEL_SWAPS.get(jung, "")]
    jongs = [jong, *_alts(jong, CODA_GROUPS)]
    if last and jong in LAST_CODA:
        jongs.append(LAST_CODA[jong])
    return sorted({_join(c, v, j) for c in chos for v in jungs for j in jongs} - {syl})


def word_variants(word: str, max_changes: int = 2) -> set[str]:
    """시발 → {쉬발, 시벌, 쉬벌, 씨빨, ...}: change up to max_changes syllables."""
    last = len(word) - 1
    options = [syllable_variants(c, i == last) if is_syllable(c) else [] for i, c in enumerate(word)]
    out = set()
    for k in range(1, max_changes + 1):
        for spots in itertools.combinations(range(len(word)), k):
            pools = [options[i] if i in spots else [c] for i, c in enumerate(word)]
            out.update("".join(p) for p in itertools.product(*pools))
    return out


_LONGEST = max(map(len, NOT_BAD))
_BY_FIRST: dict[str, list[str]] = {}
for _w in NOT_BAD:
    _BY_FIRST.setdefault(_w[0], []).append(_w)


def makes_normal_word(old: str, new: str, i: int) -> bool:
    """old[i]를 바꿔서 new가 되었을 때, 없던 NOT_BAD 단어가 생겼으면 True."""
    lo = max(0, i - _LONGEST + 1)
    before, after = old[lo : i + _LONGEST], new[lo : i + _LONGEST]
    # 글자 구간에 나온 글자로 시작하는 단어만 확인해요 (매번 모든 단어를 보지 않도록)
    return any(w in after and w not in before for c in set(after) for w in _BY_FIRST.get(c, ()))


def similar_sound(text: str, rng: random.Random) -> str:
    """증강 규칙: 한글 한 글자를 비슷한 소리로 바꿔요.
    멀쩡한 단어가 되는 바꾸기(창녀 → 장녀)는 쓰지 않아요. 라벨이 틀어져요."""
    options = [
        (i, v)
        for i, c in enumerate(text)
        if is_syllable(c)
        for v in syllable_variants(c)
        if not makes_normal_word(text, text[:i] + v + text[i + 1 :], i)
    ]
    if not options:  # e.g. only 뭐, 응: nothing sounds close
        return text
    i, v = rng.choice(options)
    return text[:i] + v + text[i + 1 :]
