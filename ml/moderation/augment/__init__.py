"""Bypass-style augmentation rules (plan 9.3). Owned by part 3.

Each rule: (text, rng) -> text. Label stays the same, so one labeled sentence
becomes many. Apply to clean sentences too (plan 9.3 last line).
"""

import random
import re

from .similar_sound import _join, _split, is_syllable, similar_sound


def insert_digit(text: str, rng: random.Random) -> str:
    """시발 → 시1발 (a digit between two hangul syllables)."""
    spots = _gaps(text)
    if not spots:
        return text
    i = rng.choice(spots)
    return text[:i] + rng.choice("1!l") + text[i:]


# 아래 규칙은 모두 한 단어 또는 한 곳만 바꿔요. 그래서 문장 대부분은 그대로 읽혀요.
# 바꿀 곳이 없으면(빈 문장, 이모지만, 한글 없음) 받은 문장을 그대로 돌려줘요.
# 예시는 일부러 아무 뜻 없는 말로 썼어요.
_RUN = re.compile("[가-힣]+")  # 한글 음절이 이어진 덩어리


def _is_jamo(c: str) -> bool:
    return "ㄱ" <= c <= "ㅣ"


def _breaks_final(nxt: str) -> bool:
    """받침을 떼어 놓으면 다음 글자와 섞이는 경우: 뒤가 자모이거나 "아"처럼 ㅇ으로 시작해요."""
    return bool(nxt) and (_is_jamo(nxt) or (is_syllable(nxt) and _split(nxt)[0] == "ㅇ"))


def _gaps(text: str) -> list[int]:
    """한글 두 글자 사이 위치: 사과 → [1]."""
    return [i for i in range(1, len(text)) if is_syllable(text[i - 1]) and is_syllable(text[i])]


def _pick_span(text: str, rng: random.Random, lo: int, hi: int) -> tuple[int, int] | None:
    """한 단어 안에서 한글 lo~hi글자 구간. 없으면 None."""
    runs = [m for m in _RUN.finditer(text) if len(m.group()) >= lo]
    if not runs:
        return None
    m = rng.choice(runs)
    n = rng.randint(lo, min(hi, len(m.group())))
    start = m.start() + rng.randint(0, len(m.group()) - n)
    return start, start + n


def insert_symbol(text: str, rng: random.Random) -> str:
    """사과나무 → 사@과나무 (글자 사이에 기호. normalize()가 지워요).
    "^", "!", "|"는 normalize()가 글자로 읽어서 쓰지 않아요."""
    gaps = _gaps(text)
    if not gaps:
        return text
    i = rng.choice(gaps)
    return text[:i] + rng.choice("@.*~_-#/") + text[i:]


def split_jamo(text: str, rng: random.Random) -> str:
    """사과나무 → ㅅㅏ과나무 (한 글자를 자모로 풀어요)."""
    spots = []
    for i, c in enumerate(text):
        if not is_syllable(c):
            continue
        # 받침 있는 글자 뒤에 "아"가 오면 "ㅂㅏㄹ아"가 "바라"로 읽혀서 건너뛰어요
        if _split(c)[2] == "\0" or not _breaks_final(text[i + 1 : i + 2]):
            spots.append(i)
    if not spots:
        return text
    i = rng.choice(spots)
    cho, jung, jong = _split(text[i])
    return text[:i] + cho + jung + ("" if jong == "\0" else jong) + text[i + 1 :]


def move_final(text: str, rng: random.Random) -> str:
    """한 글자를 둘로 떼어요. 두 가지 모양 중 하나를 골라요.
    받침 떼기: 달 → 다ㄹ    첫소리 떼기: 달 → ㄷ알 (normalize()가 되돌려요)"""
    spots = []
    for i, c in enumerate(text):
        if not is_syllable(c):
            continue
        cho, jung, jong = _split(c)
        if jong != "\0" and not _breaks_final(text[i + 1 : i + 2]):
            spots.append((i, "final"))
        if cho != "ㅇ" and not _is_jamo(text[i - 1 : i] if i else ""):
            spots.append((i, "first"))
    if not spots:
        return text
    i, mode = rng.choice(spots)
    cho, jung, jong = _split(text[i])
    if mode == "final":  # 달 → 다ㄹ
        new = _join(cho, jung, "\0") + jong
    else:  # 달 → ㄷ알
        new = cho + _join("ㅇ", jung, jong)
    return text[:i] + new + text[i + 1 :]


def chosung(text: str, rng: random.Random) -> str:
    """사과나무 → ㅅㄱ나무 (한 단어의 2~4글자를 초성만 남겨요)."""
    span = _pick_span(text, rng, 2, 4)
    if not span:
        return text
    s, e = span
    return text[:s] + "".join(_split(c)[0] for c in text[s:e]) + text[e:]


def split_space(text: str, rng: random.Random) -> str:
    """사과나무 → 사 과나무 (단어 안에 띄어쓰기)."""
    gaps = _gaps(text)
    if not gaps:
        return text
    i = rng.choice(gaps)
    return text[:i] + " " + text[i:]


# 영어 자판으로 친 한글 (두벌식): 안녕 → dkssud
_KEY = dict(zip("ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ", "qwertyuiopasdfghjklzxcvbnm"))
_KEY |= dict(zip("ㅃㅉㄸㄲㅆㅒㅖ", "QWERTOP"))
_PAIR = {  # 키를 두 번 누르는 글자
    "ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
    "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ",
    "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ",
}  # fmt: skip


def _typed(c: str) -> str:
    cho, jung, jong = _split(c)
    keys = (cho, jung, "" if jong == "\0" else jong)
    return "".join(_KEY[j] for x in keys for j in _PAIR.get(x, x))


def _is_latin(c: str) -> bool:
    return c.isascii() and c.isalpha()


def keyboard(text: str, rng: random.Random) -> str:
    """안녕하세요 → dkssud하세요 (한 단어의 1~4글자를 영어 자판으로)."""
    for _ in range(3):  # 영어 글자에 붙어 있는 덩어리는 건너뛰고 다시 골라요
        span = _pick_span(text, rng, 1, 4)
        if not span:
            return text
        s, e = span
        if not _is_latin(text[s - 1 : s]) and not _is_latin(text[e : e + 1]):
            return text[:s] + "".join(map(_typed, text[s:e])) + text[e:]
    return text


# 길게 끌 때 들리는 모음: 쉬 → ㅣ, 봐 → ㅏ (normalize.py의 표와 같아요)
_HOLD = {"ㅘ": "ㅏ", "ㅝ": "ㅓ", "ㅟ": "ㅣ", "ㅢ": "ㅣ", "ㅙ": "ㅐ", "ㅞ": "ㅔ",
         "ㅑ": "ㅏ", "ㅕ": "ㅓ", "ㅛ": "ㅗ", "ㅠ": "ㅜ", "ㅒ": "ㅐ", "ㅖ": "ㅔ"}  # fmt: skip


def stretch(text: str, rng: random.Random) -> str:
    """사과나무 → 사아아과나무 (한 글자의 모음을 늘여요)."""
    spots = [i for i, c in enumerate(text) if is_syllable(c)]
    if not spots:
        return text
    i = rng.choice(spots)
    cho, jung, jong = _split(text[i])
    hold = _HOLD.get(jung, jung)
    n = rng.randint(2, 4)  # 늘어난 글자 수. 받침은 맨 끝 글자가 가져요
    new = _join(cho, jung, "\0") + _join("ㅇ", hold, "\0") * (n - 1) + _join("ㅇ", hold, jong)
    return text[:i] + new + text[i + 1 :]


def combo(text: str, rng: random.Random) -> str:
    """사과나무 → 사@아과나무처럼 서로 다른 규칙 두 개를 차례로 써요."""
    rules = [
        insert_digit, insert_symbol, split_jamo, move_final, chosung,
        split_space, similar_sound, keyboard, stretch,
    ]  # fmt: skip
    rng.shuffle(rules)
    done = 0
    for rule in rules:
        new = rule(text, rng)
        if new != text:
            text, done = new, done + 1
            if done == 2:
                break
    return text


RULES = [
    insert_digit, insert_symbol, split_jamo, move_final, chosung,
    split_space, similar_sound, keyboard, stretch, combo,
]  # fmt: skip


def augment(text: str, rng: random.Random, n: int = 3) -> list[str]:
    return [rng.choice(RULES)(text, rng) for _ in range(n)]
