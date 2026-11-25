"""후보 읽기로 사전을 찾아요 (기획서 9.2, 9.8).

기본 읽기(normalize.normalize_with_ends)는 조심스러워요: 없는 글자를 지어내지 않고,
복원 글자와 certain 욕설은 여기서만 나와요.

후보 읽기(normalize.normalize_candidate)는 공격적이에요: 공백으로 떨어진 철자를 붙이고,
자음 뒤의 숫자/영문/!/|를 모음으로 읽어요. 어느 읽기가 맞는지 모르니까 읽는 방법을 바꿔
가며 여러 번 읽고, 사전에 걸린 곳을 원문 위치로 돌려요. 후보 읽기의 글은 누구에게도
돌려주지 않고, 여기서 걸린 곳은 거의 "애매한" 것으로 다뤄요: 모델이 있으면 모델이
판단하고, 없으면 가려요.

모델이 작아서, 모양만 봐도 욕인 경우는 규칙으로 certain에 올려요 (모델에 맡기지 않아요).
  - 애매한 단어인데 욕으로만 쓰는 모양: "새끼야", "이 새끼", 문장 끝의 "꺼져"
    (_sure_ambiguous)
  - 후보 읽기에서 certain 단어가 "ㅅ1발"처럼 자음 바로 뒤 숫자/영문으로 걸린 것
    (_lookalike). 멀쩡한 글에는 자음 낱자 바로 뒤에 숫자가 붙을 일이 거의 없어요.
"""

from functools import cache

from app.moderation import dict_filter, normalize

# (vowels, greedy): 숫자/영문을 모음으로 읽을지, 낱자 뒤 자음을 받침으로 읽을지
_READINGS = [(False, False), (True, False), (False, True), (True, True)]

# 명령으로 쓰는 애매한 단어("불이 꺼져요"와 "꺼져"). 이 단어가 글의 끝에 오면 욕이에요.
# 화이트리스트("폰이 꺼져")에 걸린 것은 여기까지 오지 않아요.
_COMMANDS = {"꺼져", "꺼저", "꺼쳐", "꺼지라", "닥쳐", "닥처", "닥저", "닥져", "닼쳐"}
_COMMAND_TAILS = {"", "라", "버려"}  # 꺼져 / 꺼져라 / 꺼져 버려 (뒤의 기호, ㅋㅋ, 이모지는 상관없어요)
# "새끼"를 욕으로만 쓰는 모양: 부르는 말(새끼야, 새끼들아)과 가리키는 말(이 새끼, 저 새끼).
# "그 새끼", "새끼가"는 동물 얘기에도 흔해서 넣지 않아요.
_SAEKKI_AFTER = ("야", "들아")
_SAEKKI_BEFORE = ("이", "저")  # 한 글자 단어라 정규화하면 붙어요: "이 새끼" → 이새끼
# 근처에 이 말이 있으면 동물 새끼일 수 있어서 올리지 않아요 ("고양이 새끼야?", "이 새끼 고양이").
# 모델이 판단해요. 한 글자 동물(새, 소, 말)은 "새로", "말이야"에도 들어 있어서 넣지 않아요.
_ANIMALS = ("고양이", "강아지", "냥이", "멍멍이", "댕댕이", "토끼", "오리", "병아리", "사자",
            "호랑이", "펭귄", "거북", "물고기", "햄스터", "다람쥐", "여우", "늑대", "판다",
            "코끼리", "기린", "사슴", "염소", "돼지", "개구리", "망아지", "송아지")  # fmt: skip


def _jamo(c: str) -> bool:
    return "ㄱ" <= c <= "ㅣ"


def _sure_ambiguous(text: str, norm: str, s: int, e: int, end: int, word: str) -> bool:
    """애매한 단어가 욕으로만 쓰는 모양인가? s, e는 정규화한 글의 위치, end는 원문의 끝 위치."""
    if word in _COMMANDS:
        tail = "".join(c for c in text[end:] if "가" <= c <= "힣")
        return tail in _COMMAND_TAILS
    if word != "새끼":
        return False
    near = norm[max(0, s - 5) : s] + " " + norm[e : e + 5]  # 앞뒤 다섯 글자 ("새끼"는 빼고)
    if any(a in near for a in _ANIMALS):
        return False
    after = norm[e:].startswith(_SAEKKI_AFTER)
    # "이새끼"는 "이"가 따로 떨어진 단어일 때만이에요 ("아이새끼"의 "이"는 아니에요)
    before = s >= 1 and norm[s - 1] in _SAEKKI_BEFORE and (s == 1 or norm[s - 2] == " ")
    return after or before


def _lookalike(text: str, start: int, end: int) -> bool:
    """원문 [start, end)가 띄어쓰기 없이, 자음 낱자 바로 뒤에 모음 대신 숫자/영문을 쓴 모양인가 ("ㅅ1발")."""
    span = "".join(t[0] for t in normalize._fold(text[start:end]))
    if any(c.isspace() for c in span):
        return False
    return any("ㄱ" <= a <= "ㅎ" and b in normalize._CANDIDATE_VOWEL for a, b in zip(span, span[1:]))


def _splits_initial_group(text: str, s: int, e: int) -> bool:
    """[s, e)가 "ㄹㅂ ㅅㄱ"처럼 서로 다른 초성 묶음의 일부만 잘라서 붙인 것인가?
    공백을 사이에 두고, 걸린 곳이 자모 묶음의 한가운데에서 시작하거나 끝나면 True."""
    for p in range(s, e):
        if not text[p].isspace() or (p > s and text[p - 1].isspace()):
            continue
        left = p
        while left > 0 and not text[left - 1].isspace():
            left -= 1
        if left < s and _jamo(text[s]) and _jamo(text[s - 1]):
            return True
        right = p
        while right < len(text) and text[right].isspace():
            right += 1
        end = right
        while end < len(text) and not text[end].isspace():
            end += 1
        if right < e < end and _jamo(text[e - 1]) and _jamo(text[e]):
            return True
    return False


@cache
def default_hits(text: str) -> tuple[tuple[int, int, str, str], ...]:
    """기본 읽기에서 걸린 (원문 시작, 원문 끝, 단어, 종류). 서빙(moderate)과 학습 데이터
    만들기(build_train)가 같은 판정을 쓰도록 여기 한 곳에 둬요.

    certain은 욕이 원문에서 이어져 있을 때만이에요 (사이의 기호는 괜찮아요: "씨@발").
    "수박 씨 발아율"처럼 띄어쓰기를 지워서 생긴 "씨발"은 certain이 아니라 ambiguous예요.
    그래야 모델이 멀쩡한 문장을 풀어 줄 수 있어요. 낱말에 공백이 들어 있는 사전 항목
    ("꼬치 빨")은 공백이 있어도 이어진 거예요."""
    norm, index_map, end_map = normalize.normalize_with_ends(text)
    out = []
    for s, e, word, kind in dict_filter.find_words(norm):
        start, end = index_map[s], end_map[e - 1]
        if kind == "certain" and " " not in word and any(c.isspace() for c in text[start:end]):
            kind = "ambiguous"
        elif kind == "ambiguous" and _sure_ambiguous(text, norm, s, e, end, word):
            kind = "certain"
        out.append((start, end, word, kind))
    return tuple(out)


@cache
def hits(text: str) -> tuple[tuple[int, int, str, str], ...]:
    """후보 읽기에서 걸린 (원문 시작, 원문 끝, 단어, 종류). 자음 자모가 없으면 읽을 필요가 없어요.
    종류는 거의 "ambiguous"이고, certain 단어를 "ㅅ1발" 모양으로 쓴 것만 "certain"이에요."""
    # 전각 "１", "｜"나 반각 자모는 읽기 전에 접혀서 글자가 돼요. 건너뛸지는 접힌 글로 정해요.
    folded = "".join(t[0] for t in normalize._fold(text))
    if not any("ㄱ" <= c <= "ㅎ" for c in folded):
        return ()
    base_norm, base_map, _ = normalize.normalize_with_ends(text)
    seen = {(base_norm, tuple(base_map))}
    found = set()
    # 읽는 방법이 결과에 영향을 줄 글자가 없으면 그 읽기는 앞선 읽기와 똑같아서 건너뛰어요
    has_lookalike = any(c in normalize._CANDIDATE_VOWEL for c in folded)  # "1", "l", "!", "h" ...
    has_ieung = any("아" <= c <= "잏" for c in folded)  # ㅇ으로 시작하는 글자 (아 ~ 잏)
    for vowels, greedy in _READINGS:
        if (vowels and not has_lookalike) or (greedy and not has_ieung):
            continue
        norm, index_map, end_map = normalize.normalize_candidate(text, vowels, greedy)
        if (norm, tuple(index_map)) in seen:  # 기본 읽기나 앞선 읽기와 같으면 같은 결과예요
            continue
        seen.add((norm, tuple(index_map)))
        for s, e, word, kind in dict_filter.find_words(norm):
            start, end = index_map[s], end_map[e - 1]
            if not _splits_initial_group(text, start, end):
                sure = kind == "certain" and _lookalike(text, start, end)
                found.add((start, end, word, "certain" if sure else "ambiguous"))
    # 같은 곳이 여러 읽기에서 걸리면 certain 하나만 남겨요
    best = {}
    for start, end, word, kind in found:
        if best.get((start, end, word)) != "certain":
            best[start, end, word] = kind
    return tuple(sorted((s, e, w, k) for (s, e, w), k in best.items()))
