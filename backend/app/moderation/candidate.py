"""후보 읽기로 사전을 찾아요 (기획서 9.2, 9.8).

기본 읽기(normalize.normalize_with_ends)는 조심스러워요: 없는 글자를 지어내지 않고,
복원 글자와 certain 욕설은 여기서만 나와요.

후보 읽기(normalize.normalize_candidate)는 공격적이에요: 공백으로 떨어진 철자를 붙이고,
자음 뒤의 숫자/영문/!/|를 모음으로 읽어요. 어느 읽기가 맞는지 모르니까 읽는 방법을 바꿔
가며 여러 번 읽고, 사전에 걸린 곳을 원문 위치로 돌려요. 후보 읽기의 글은 누구에게도
돌려주지 않고, 여기서 걸린 곳은 모두 "애매한" 것으로 다뤄요: 모델이 있으면 모델이
판단하고, 없으면 가려요.
"""

from functools import cache

from app.moderation import dict_filter, normalize

# (vowels, greedy): 숫자/영문을 모음으로 읽을지, 낱자 뒤 자음을 받침으로 읽을지
_READINGS = [(False, False), (True, False), (False, True), (True, True)]


def _jamo(c: str) -> bool:
    return "ㄱ" <= c <= "ㅣ"


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
        out.append((start, end, word, kind))
    return tuple(out)


@cache
def hits(text: str) -> tuple[tuple[int, int, str], ...]:
    """후보 읽기에서 걸린 (원문 시작, 원문 끝, 단어). 자음 자모가 없으면 읽을 필요가 없어요."""
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
        for s, e, word, _kind in dict_filter.find_words(norm):
            start, end = index_map[s], end_map[e - 1]
            if not _splits_initial_group(text, start, end):
                found.add((start, end, word))
    return tuple(sorted(found))
