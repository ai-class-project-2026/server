"""Step ① of plan 9.2: undo bypass spellings before the dictionary/model look.

Contract: return (normalized, index_map) where index_map[i] is the index in the
original text of normalized[i] (the FIRST original char it came from when several
were merged, e.g. "ㅅㅣ" → "시"). moderate() uses it to mask the right characters.
normalize_with_ends() also gives end_map[i] = where normalized[i] ENDS in the
original text (exclusive), so a mask stops at the last matched char and never
covers symbols or digits that were dropped after it ("시발1가" → 시발 is [0, 2]).
Tokens inside are [char, start, end].

읽기가 두 가지예요 (기획서 9.8):
  기본 읽기  normalize_with_ends(): 조심스러워요. 없는 글자를 지어내지 않고
             ("ㅋ 1등"은 그대로), 복원 글자와 certain 욕설은 이 읽기에서만 나와요.
  후보 읽기  normalize_candidate(): 공격적이에요. 공백으로 떨어진 철자를 붙이고,
             자음 뒤의 숫자/영문/!/|를 모음으로 읽어요. 사전을 찾을 때만 쓰고 그 글은
             돌려주지 않아요 (candidate.py가 여러 방식으로 읽어요).

Pipeline of the default reading (each step works on [char, start, end] tokens):
  1. fold       NFKC (fullwidth "ＴＬＱＫＦ"), conjoining jamo → compatibility jamo
  2. lookalike  "^ㅣ발" → ㅅㅣ발. The vowel look-alikes ("ㅅ1발", "ㅅ|발", "새ㄲ1")
                are read only by the candidate reading
  3. keyboard   "tlqkf" → 시발 (only when the latin run types valid hangul)
  4. symbols    drop "@", "*", "." ... ("씨@발" → 씨발)
  5. spaces     drop a space after a 1-letter word ("시 발" → 시발) but keep
                "수박씨 발라" apart so it doesn't become 씨발
  6. noise      drop 1-2 digits/letters between syllables ("시1발" → 시발)
  7. compose    jamo → syllables ("ㅅㅣ발" → 시발, "시ㅂ알" → 시발, "시바ㄹ" → 시발)
  8. squeeze    "시이이발" → 시발, "바아알" → 발, 3+ same chars → 2
"""

import unicodedata
from functools import lru_cache

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = "\0ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"  # 0 = no final
_VOWEL_PAIRS = {
    ("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ", ("ㅜ", "ㅓ"): "ㅝ",
    ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ", ("ㅡ", "ㅣ"): "ㅢ",
}  # fmt: skip
_FINAL_PAIRS = {
    ("ㄱ", "ㅅ"): "ㄳ", ("ㄴ", "ㅈ"): "ㄵ", ("ㄴ", "ㅎ"): "ㄶ", ("ㄹ", "ㄱ"): "ㄺ",
    ("ㄹ", "ㅁ"): "ㄻ", ("ㄹ", "ㅂ"): "ㄼ", ("ㄹ", "ㅅ"): "ㄽ", ("ㄹ", "ㅌ"): "ㄾ",
    ("ㄹ", "ㅍ"): "ㄿ", ("ㄹ", "ㅎ"): "ㅀ", ("ㅂ", "ㅅ"): "ㅄ",
}  # fmt: skip
# vowel you hear when you hold V: 쉬이 → ㅣ, 봐아 → ㅏ (for squeeze)
_HOLD = {"ㅘ": "ㅏ", "ㅝ": "ㅓ", "ㅟ": "ㅣ", "ㅢ": "ㅣ", "ㅙ": "ㅐ", "ㅞ": "ㅔ",
         "ㅑ": "ㅏ", "ㅕ": "ㅓ", "ㅛ": "ㅗ", "ㅠ": "ㅜ", "ㅒ": "ㅐ", "ㅖ": "ㅔ"}  # fmt: skip

# 2-beolsik keyboard: what the key types when the IME is off
KEYMAP = dict(zip("qwertyuiopasdfghjklzxcvbnm", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ"))
KEYMAP |= dict(zip("QWERTOP", "ㅃㅉㄸㄲㅆㅒㅖ"))

LOOKALIKE_VOWEL = {"1": "ㅣ", "l": "ㅣ", "I": "ㅣ", "i": "ㅣ", "!": "ㅣ", "|": "ㅣ", "H": "ㅐ"}

# 후보 읽기는 소문자 "h"도 ㅐ로 읽어요 ("ㅅ H ㄲ l"은 영문 처리에서 "h"가 돼요)
_CANDIDATE_VOWEL = LOOKALIKE_VOWEL | {"h": "ㅐ"}

_CONJOIN = (
    {chr(0x1100 + i): c for i, c in enumerate(CHO)}
    | {chr(0x1161 + i): c for i, c in enumerate(JUNG)}
    | {chr(0x11A8 + i): c for i, c in enumerate(JONG[1:])}
)


def is_syllable(c: str) -> bool:
    return "가" <= c <= "힣"


def split_syllable(c: str) -> tuple[str, str, str]:
    """시 → ("ㅅ", "ㅣ", ""), 발 → ("ㅂ", "ㅏ", "ㄹ")."""
    n = ord(c) - 0xAC00
    jong = JONG[n % 28]
    return CHO[n // 588], JUNG[n % 588 // 28], "" if jong == "\0" else jong


def join_syllable(cho: str, jung: str, jong: str = "") -> str:
    return chr(
        0xAC00 + CHO.index(cho) * 588 + JUNG.index(jung) * 28 + JONG.index(jong or "\0")
    )


CHO_S, JUNG_S, JONG_S = set(CHO), set(JUNG), set(JONG[1:])


def _ch(toks, i):
    return toks[i][0] if 0 <= i < len(toks) else ""


def _is_cons(c: str) -> bool:
    return "ㄱ" <= c <= "ㅎ"


def _is_letter(c: str) -> bool:
    return c.isascii() and c.isalpha()


def _fold(text: str) -> list[list]:
    toks = []
    for i, ch in enumerate(text):
        # NFKC would turn compatibility jamo ("ㅅㅂ") into conjoining jamo
        folded = ch if "㄰" <= ch <= "㆏" else unicodedata.normalize("NFKC", ch)
        toks += [[_CONJOIN.get(c, c), i, i + 1] for c in folded]
    return toks


def _lookalike(toks, vowels=False):
    """"^ㅣ발" → ㅅㅣ발 (기본 읽기). vowels=True(후보 읽기)면 자음 바로 뒤의 "1", "l", "H",
    "!", "|"도 모음으로 읽어요: "새ㄲ1", "ㅅHㄲl야". 기본 읽기는 이걸 하지 않아요.
    "ㅋ1등"을 "키등"으로 바꾸면 멀쩡한 글을 지어내는 거라서요."""
    for i, (c, *_) in enumerate(toks):
        nxt, prev = _ch(toks, i + 1), _ch(toks, i - 1)
        if vowels and c in _CANDIDATE_VOWEL and prev in CHO_S:
            toks[i][0] = _CANDIDATE_VOWEL[c]
        elif c == "^" and (nxt in JUNG_S or nxt in LOOKALIKE_VOWEL):
            toks[i][0] = "ㅅ"
            if nxt in LOOKALIKE_VOWEL:
                toks[i + 1][0] = LOOKALIKE_VOWEL[nxt]
    return toks


def _keyboard(toks):
    out, i = [], 0
    while i < len(toks):
        if not _is_letter(toks[i][0]):
            out.append(toks[i])
            i += 1
            continue
        j = i
        while j < len(toks) and _is_letter(toks[j][0]):
            j += 1
        run = toks[i:j]
        s = "".join(c for c, *_ in run)
        if s.isupper():  # caps lock, not shift
            s = s.lower()
        composed = None
        if len(s) >= 2:
            jamo = [
                [KEYMAP.get(c) or KEYMAP[c.lower()], src, end]
                for c, (_, src, end) in zip(s, run)
            ]
            composed = _compose(jamo, ime=True)
            if not all(is_syllable(c) for c, *_ in composed):
                composed = None  # "hello" → ㅗㄷㅣㅣㅐ isn't hangul, keep it
        out += composed or [[c.lower(), src, end] for c, (_, src, end) in zip(s, run)]
        i = j
    return out


def _drop_symbols(toks, keep=""):
    return [t for t in toks if t[0].isalnum() or t[0].isspace() or t[0] in keep]


def _spaces(toks, join=False):
    words, seps, cur, sep = [], [], [], None  # split on whitespace runs
    for t in toks:
        if t[0].isspace():
            sep = sep or t
            continue
        if sep and cur:
            words.append(cur)
            seps.append(sep)
            cur = []
        sep = None
        cur.append(t)
    if cur:
        words.append(cur)
    out = []
    for k, w in enumerate(words):
        if k:
            lone_jamo = len(w) == 1 and "ㄱ" <= w[0][0] <= "ㅣ"  # "시바 ㄹ"
            if join:  # 후보 읽기: "ㄹ@27"처럼 자모 뒤에 숫자/영문 잡음만 붙은 말도 자모 하나로 봐요
                lone_jamo = "ㄱ" <= w[0][0] <= "ㅣ" and all(t[0].isascii() and t[0].isalnum() for t in w[1:])
            # join: 자음 자모로 끝나는 말 뒤 공백도 지워요 ("éㅆ 1발"). 단어 길이가 아니라
            # "끝이 자음인가"를 봐요. 후보 읽기에서만 쓰고, 사전에 걸릴 때만 써요.
            ends_jamo = join and "ㄱ" <= words[k - 1][-1][0] <= "ㅎ"
            # 후보 읽기: "ㅆㅣ"처럼 자모 여러 개가 글자 하나가 되는 말도 한 글자 말로 봐요
            one_letter = len(words[k - 1]) == 1 or (join and len(_compose(words[k - 1], boundary=True)) == 1)
            if not (one_letter or lone_jamo or ends_jamo):
                out.append([" ", *seps[k - 1][1:]])
        out += w
    return out


def _drop_noise(toks):
    out, i = [], 0
    while i < len(toks):
        j = i
        while j < len(toks) and toks[j][0].isascii() and toks[j][0].isalnum():
            j += 1
        if 0 < j - i <= 2 and is_syllable(_ch(toks, i - 1)) and is_syllable(_ch(toks, j)):
            i = j  # "시1발", "병x신"
            continue
        out += toks[i : max(j, i + 1)]
        i = max(j, i + 1)
    return out


def _takes_final(toks, i, ime=False, greedy=False):
    """toks[i] is a consonant that closes the previous syllable instead of
    starting the next one."""
    c, nxt = _ch(toks, i), _ch(toks, i + 1)
    if c not in JONG_S or nxt in JUNG_S:
        return False
    if ime:  # keyboard run: plain 2-beolsik IME rule
        return True
    return not (
        (_is_cons(nxt) and _ch(toks, i + 2) not in JUNG_S)  # "가자ㄱㄱ": chosung
        # "시ㅂ알". greedy(후보 읽기)는 "ㅂㅏㄹ이"의 ㄹ을 "이" 앞이어도 받침으로 읽어요
        or (not greedy and is_syllable(nxt) and split_syllable(nxt)[0] == "ㅇ")
    )


def _compose(toks, ime=False, boundary=False, greedy=False):
    out, i = [], 0
    while i < len(toks):
        c, src, _ = toks[i]
        nxt = _ch(toks, i + 1)
        if c in CHO_S and nxt in JUNG_S:  # ㅅ ㅣ (ㅂ) → 시 / 십
            jung, i = nxt, i + 2
            if (jung, _ch(toks, i)) in _VOWEL_PAIRS:
                jung, i = _VOWEL_PAIRS[jung, _ch(toks, i)], i + 1
            jong = ""
            if _takes_final(toks, i, ime, greedy):
                jong, i = _ch(toks, i), i + 1
                if (jong, _ch(toks, i)) in _FINAL_PAIRS and _takes_final(toks, i, ime, greedy):
                    jong, i = _FINAL_PAIRS[jong, _ch(toks, i)], i + 1
            out.append([join_syllable(c, jung, jong), src, toks[i - 1][2]])
            continue
        # boundary(후보 읽기): "ㅂㅅ아"의 ㅅ은 앞에도 자음이 있는 초성 낱말이라 "아"와 합치지 않아요
        if (
            c in CHO_S
            and is_syllable(nxt)
            and split_syllable(nxt)[0] == "ㅇ"
            and not (boundary and _ch(toks, i - 1) in CHO_S)
        ):
            _, jung, jong = split_syllable(nxt)  # ㅂ알 → 발
            out.append([join_syllable(c, jung, jong), src, toks[i + 1][2]])
            i += 2
            continue
        if is_syllable(c) and not split_syllable(c)[2] and _takes_final(toks, i + 1, greedy=greedy):
            out.append([join_syllable(*split_syllable(c)[:2], nxt), src, toks[i + 1][2]])  # 바ㄹ → 발
            i += 2
            continue
        out.append(toks[i])
        i += 1
    return out


def _squeeze(toks):
    out = []
    for t in toks:
        c = t[0]
        if out and is_syllable(c) and is_syllable(out[-1][0]):
            cho, jung, jong = split_syllable(c)
            pcho, pjung, pjong = split_syllable(out[-1][0])
            if cho == "ㅇ" and not pjong and jung in (pjung, _HOLD.get(pjung)):
                if jong:  # 바 + 알 → 발
                    out[-1][0] = join_syllable(pcho, pjung, jong)
                out[-1][2] = t[2]  # 지운 글자까지 이 글자의 일부예요
                continue  # 시 + 이 → 시
        if len(out) >= 2 and c == out[-1][0] == out[-2][0]:
            out[-1][2] = t[2]
            continue  # ㅋㅋㅋㅋ → ㅋㅋ
        out.append(t)
    return out


def normalize_with_ends(text: str) -> tuple[str, list[int], list[int]]:
    """기본 읽기(조심스러운 읽기). 복원 글자와 certain 단어는 이 읽기에서만 나와요.
    normalize()와 같고, 정규화 글자마다 원문에서 끝나는 위치(end_map)도 줘요.
    없는 글자를 지어내지 않아요 ("ㅋ 1등"은 그대로)."""
    norm, index_map, end_map = _default_reading(text)
    return norm, list(index_map), list(end_map)  # 호출한 쪽이 고쳐도 캐시는 안전해요


@lru_cache(maxsize=16384)  # 같은 글을 여러 번 읽는 일이 많아요 (사전 검사, 학습 데이터 만들기)
def _default_reading(text: str) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    toks = _fold(text)
    toks = _lookalike(toks)
    toks = _keyboard(toks)
    toks = _drop_symbols(toks)
    toks = _spaces(toks)
    toks = _drop_noise(toks)
    toks = _compose(toks)
    toks = _squeeze(toks)
    return "".join(t[0] for t in toks), tuple(t[1] for t in toks), tuple(t[2] for t in toks)


def normalize_candidate(text: str, vowels: bool, greedy: bool) -> tuple[str, list[int], list[int]]:
    """후보 읽기: 사전을 찾을 때만 써요. 이 글은 누구에게도 돌려주지 않아요.
    기본 읽기보다 공격적으로 읽어요.
      - 공백으로 떨어진 철자를 붙여요 ("ㅅ 1발", "ㅆ ! 발", "씨바 ㄹ@27")
      - vowels: 자음 뒤의 숫자/영문/!/|를 모음으로 읽어요 ("새ㄲ1", "ㅅHㄲl야", "ㅅH..끼")
      - 초성 낱말과 조사/서수의 경계를 지켜요 ("ㅂㅅ아", "ㅆㅣㅂㅏㄹ1교시")
      - greedy: 낱자 뒤에 "이" 같은 조사가 와도 마지막 자음을 받침으로 읽어요 ("ㅆㅣㅂㅏㄹ이")
    어느 읽기가 맞는지는 모르니까 여러 읽기를 모두 찾아보고, 걸린 곳을 원문 위치로 돌려요."""
    toks = _fold(text)
    toks = _lookalike(toks, vowels)
    toks = _keyboard(toks)
    toks = _drop_symbols(toks, "!|" if vowels else "")  # 공백을 붙인 뒤에 모음으로 읽어요
    toks = _spaces(toks, join=True)
    if vowels:
        toks = _drop_symbols(_lookalike(toks, vowels))
    toks = _drop_noise(toks)
    toks = _compose(toks, boundary=True, greedy=greedy)
    toks = _squeeze(toks)
    return "".join(t[0] for t in toks), [t[1] for t in toks], [t[2] for t in toks]


def normalize(text: str) -> tuple[str, list[int]]:
    norm, index_map, _ = normalize_with_ends(text)
    return norm, index_map
