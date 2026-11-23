"""Step ① of plan 9.2: undo bypass spellings before the dictionary/model look.

Contract: return (normalized, index_map) where index_map[i] is the index in the
original text of normalized[i] (the FIRST original char it came from when several
were merged, e.g. "ㅅㅣ" → "시"). moderate() uses it to mask the right characters.

Pipeline (each step works on [char, original_index] tokens):
  1. fold       NFKC (fullwidth "ＴＬＱＫＦ"), conjoining jamo → compatibility jamo
  2. lookalike  "ㅅ1발" "ㅅl발" "ㅅ|발" → ㅅㅣ발, "^ㅣ발" → ㅅㅣ발
  3. keyboard   "tlqkf" → 시발 (only when the latin run types valid hangul)
  4. symbols    drop "@", "*", "." ... ("씨@발" → 씨발)
  5. spaces     drop a space after a 1-letter word ("시 발" → 시발) but keep
                "수박씨 발라" apart so it doesn't become 씨발
  6. noise      drop 1-2 digits/letters between syllables ("시1발" → 시발)
  7. compose    jamo → syllables ("ㅅㅣ발" → 시발, "시ㅂ알" → 시발, "시바ㄹ" → 시발)
  8. squeeze    "시이이발" → 시발, "바아알" → 발, 3+ same chars → 2
"""

import unicodedata

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
        toks += [[_CONJOIN.get(c, c), i] for c in folded]
    return toks


def _lookalike(toks):
    for i, (c, _) in enumerate(toks):
        nxt, prev = _ch(toks, i + 1), _ch(toks, i - 1)
        if c in LOOKALIKE_VOWEL and prev in CHO_S and not _is_letter(nxt):
            toks[i][0] = LOOKALIKE_VOWEL[c]
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
        s = "".join(c for c, _ in run)
        if s.isupper():  # caps lock, not shift
            s = s.lower()
        composed = None
        if len(s) >= 2:
            jamo = [[KEYMAP.get(c) or KEYMAP[c.lower()], src] for c, (_, src) in zip(s, run)]
            composed = _compose(jamo, ime=True)
            if not all(is_syllable(c) for c, _ in composed):
                composed = None  # "hello" → ㅗㄷㅣㅣㅐ isn't hangul, keep it
        out += composed or [[c.lower(), src] for c, (_, src) in zip(s, run)]
        i = j
    return out


def _drop_symbols(toks):
    return [t for t in toks if t[0].isalnum() or t[0].isspace()]


def _spaces(toks):
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
            if not (len(words[k - 1]) == 1 or lone_jamo):
                out.append([" ", seps[k - 1][1]])
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


def _takes_final(toks, i, ime=False):
    """toks[i] is a consonant that closes the previous syllable instead of
    starting the next one."""
    c, nxt = _ch(toks, i), _ch(toks, i + 1)
    if c not in JONG_S or nxt in JUNG_S:
        return False
    if ime:  # keyboard run: plain 2-beolsik IME rule
        return True
    return not (
        (_is_cons(nxt) and _ch(toks, i + 2) not in JUNG_S)  # "가자ㄱㄱ": chosung
        or (is_syllable(nxt) and split_syllable(nxt)[0] == "ㅇ")  # "시ㅂ알"
    )


def _compose(toks, ime=False):
    out, i = [], 0
    while i < len(toks):
        c, src = toks[i]
        nxt = _ch(toks, i + 1)
        if c in CHO_S and nxt in JUNG_S:  # ㅅ ㅣ (ㅂ) → 시 / 십
            jung, i = nxt, i + 2
            if (jung, _ch(toks, i)) in _VOWEL_PAIRS:
                jung, i = _VOWEL_PAIRS[jung, _ch(toks, i)], i + 1
            jong = ""
            if _takes_final(toks, i, ime):
                jong, i = _ch(toks, i), i + 1
                if (jong, _ch(toks, i)) in _FINAL_PAIRS and _takes_final(toks, i, ime):
                    jong, i = _FINAL_PAIRS[jong, _ch(toks, i)], i + 1
            out.append([join_syllable(c, jung, jong), src])
            continue
        if c in CHO_S and is_syllable(nxt) and split_syllable(nxt)[0] == "ㅇ":
            _, jung, jong = split_syllable(nxt)  # ㅂ알 → 발
            out.append([join_syllable(c, jung, jong), src])
            i += 2
            continue
        if is_syllable(c) and not split_syllable(c)[2] and _takes_final(toks, i + 1):
            out.append([join_syllable(*split_syllable(c)[:2], nxt), src])  # 바ㄹ → 발
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
                continue  # 시 + 이 → 시
        if len(out) >= 2 and c == out[-1][0] == out[-2][0]:
            continue  # ㅋㅋㅋㅋ → ㅋㅋ
        out.append(t)
    return out


def normalize(text: str) -> tuple[str, list[int]]:
    toks = _fold(text)
    toks = _lookalike(toks)
    toks = _keyboard(toks)
    toks = _drop_symbols(toks)
    toks = _spaces(toks)
    toks = _drop_noise(toks)
    toks = _compose(toks)
    toks = _squeeze(toks)
    return "".join(c for c, _ in toks), [i for _, i in toks]
