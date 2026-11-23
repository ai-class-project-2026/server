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


def similar_sound(text: str, rng: random.Random) -> str:
    """Augmentation rule: swap one hangul syllable for a similar-sounding one."""
    spots = [i for i, c in enumerate(text) if is_syllable(c) and syllable_variants(c)]
    if not spots:  # e.g. only 뭐, 응: nothing sounds close
        return text
    i = rng.choice(spots)
    return text[:i] + rng.choice(syllable_variants(text[i])) + text[i + 1 :]
