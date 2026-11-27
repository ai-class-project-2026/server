"""Profanity pipeline (plan 9.2). Part 3 owns normalize/dict_filter/classifier;
this file only wires them together and must keep the same return shape."""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.moderation import candidate, classifier, normalize

log = logging.getLogger(__name__)

# filter_level 1 (lenient) .. 3 (strict) → classifier score needed to flag
THRESHOLDS = {1: 0.9, 2: 0.7, 3: 0.5}


@dataclass
class ModerationResult:
    is_profane: bool
    score: float
    spans: list[list[int]]  # [start, end) in the ORIGINAL text
    restored: str
    model_version: str


def _spans(pairs: list[tuple[int, int]]) -> list[list[int]]:
    """겹치거나 맞닿은 구간은 하나로 합쳐서, 정렬된 겹치지 않는 구간만 돌려줘요."""
    out: list[list[int]] = []
    for start, end in sorted(pairs):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def _model_score(text: str, norm: str, context: list[str]) -> float | None:
    """모델 점수. 모델이 없거나, 에러가 나거나, 0~1 밖(NaN, 무한대 포함)이면 None.
    None이면 사전만으로 판단해요. 에러 내용에는 사용자 글이 들어갈 수 있어서
    로그에는 에러 종류(클래스 이름)만 남겨요."""
    try:
        score = classifier.score(text, norm, context)
        if score is None or isinstance(score, bool):
            return None
        score = float(score)
    except Exception as e:
        log.warning("moderation classifier failed: %s", type(e).__name__)
        return None
    if not (math.isfinite(score) and 0.0 <= score <= 1.0):
        log.warning("moderation classifier gave an unusable score")
        return None
    return score


def _seen_end(text: str, norm: str, context: list[str]) -> int:
    """원문에서 모델이 읽는 곳의 끝 위치: 이 앞은 모델이 봐요. 원문 칸과 정규화한 칸 중
    더 멀리 남은 쪽을 원문 위치로 바꿔요. 셀 수 없으면 0 (모델이 아무것도 못 본 것으로 쳐요)."""
    try:
        raw_end, norm_end = classifier.visible(text, norm, context)
        _, _, end_map = normalize.normalize_with_ends(text)
        return max(raw_end, end_map[norm_end - 1] if norm_end > 0 else 0)
    except Exception as e:
        log.warning("moderation visible-length check failed: %s", type(e).__name__)
        return 0


def _without_names(text: str, names: list[str]) -> tuple[str, ...]:
    """멤버 이름을 같은 길이의 공백으로 지운 글 (위치가 그대로라 가릴 곳이 맞아요). 이름이 없으면 ().
    "강상우보지"는 한 낱말이라 혼자 쓴 말로 안 보여서 모델과 사전이 둘 다 놓쳐요. 세 글자 이름은
    성을 빼고도 불러서 뒤 두 글자("상우")도 지워요. 긴 이름부터 지워요."""
    keys = set()
    for n in names:
        n = n.strip()
        if len(n) >= 2:
            keys.add(n)
            if len(n) == 3 and all(normalize.is_syllable(c) for c in n):
                keys.add(n[1:])
    found = sorted((n for n in keys if n in text), key=len, reverse=True)
    for n in found:
        text = text.replace(n, " " * len(n))
    return (text,) if found else ()


def moderate(text: str, context: list[str] = (), level: int = 2, names: list[str] = ()) -> ModerationResult:
    # 앞 메시지는 판정에 쓰지 않아요. 문맥 학습 데이터가 합성이라 모델이 "이런 분위기면 정상"이라는
    # 지름길을 배웠어요: "3시쯤?", "ㅇㅋ" 뒤의 "병신아"를 통과시키고, 욕 뒤의 "ㅋㅋ"를 가렸어요.
    # API 모양은 그대로 두고 값만 버려요. 사람 대화로 다시 학습하기 전에는 되살리지 않아요.
    context = []
    # 기본 읽기: 복원 글자와 certain 단어는 여기서만 나와요.
    norm = normalize.normalize_with_ends(text)[0]
    certain, ambiguous = [], []
    # 멤버 이름을 지운 글도 사전으로 한 번 더 읽어서 걸린 곳을 더하기만 해요: 원문에서 걸린 곳은
    # 이름 때문에 빠지지 않아요 ("시발"이라는 멤버가 있어도 "시발"은 가려요). 모델은 원문만 읽어요.
    for reading in (text, *_without_names(text, names)):
        for s, e, _, kind in candidate.default_hits(reading):
            (certain if kind == "certain" else ambiguous).append((s, e))
        # 후보 읽기: 공백/숫자/기호로 가린 철자를 사전에서 찾기만 해요. 여기서 걸린 곳은
        # 모두 애매한 단어로 다뤄요. 이미 certain으로 가려진 곳 안은 건너뛰어요.
        for s, e, _ in candidate.hits(reading):
            if not any(cs <= s and e <= ce for cs, ce in certain):
                ambiguous.append((s, e))
    restored = norm
    if certain and not ambiguous:  # certain 단어만: 모델 없이 바로 욕이에요
        return ModerationResult(True, 1.0, _spans(certain), restored, "dict")
    # ambiguous 단어("새끼", "꺼져")는 멀쩡한 말일 수도 있어요: 모델이 판단하고,
    # 가리는 위치는 사전이 찾은 곳이에요. certain 단어는 늘 가려요.
    score = _model_score(text, norm, context)
    # 띄어쓰기를 지워서 욕처럼 보이게 된 말을 화이트리스트가 풀어 줬으면("수박 씨 발아 실험" →
    # "씨발아 실험"), 붙여 읽은 글에는 "씨발"이 그대로 보여서 모델이 욕이라고 하고 글 전체가
    # 가려져요. 이런 글은 원문 그대로도 한 번 더 읽혀서, 두 번 다 욕이라고 할 때만 욕으로 봐요.
    if score is not None and candidate.joined_whitelisted(text):
        raw_score = _model_score(text, text, context)
        if raw_score is not None:
            score = min(score, raw_score)
    if score is not None and ambiguous:
        # 긴 글은 뒤가 잘려서 모델이 못 봐요. 모델이 못 본 곳의 ambiguous 단어는 모델이
        # 판단할 수 없으니, 모델이 없을 때처럼 certain으로 다뤄서 가려요.
        seen = _seen_end(text, norm, context)
        certain += [h for h in ambiguous if h[1] > seen]
        ambiguous = [h for h in ambiguous if h[1] <= seen]
    if certain:
        hits = certain + (ambiguous if score is None or score >= THRESHOLDS[level] else [])
        return ModerationResult(True, 1.0, _spans(hits), restored, "dict")
    if score is None:  # 모델이 없거나 못 쓰면 ambiguous 단어도 욕으로 봐요
        spans = _spans(ambiguous)
        return ModerationResult(bool(spans), 1.0 if spans else 0.0, spans, restored, "dict")
    flagged = score >= THRESHOLDS[level]
    spans = _spans(ambiguous) if flagged else []
    # 응답에는 서버 안의 전체 경로 대신 모델 폴더 이름만 내보내요
    return ModerationResult(flagged, score, spans, restored, Path(settings.moderation_onnx_path).name)
