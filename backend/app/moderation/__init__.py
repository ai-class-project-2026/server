"""Profanity pipeline (plan 9.2). Part 3 owns normalize/dict_filter/classifier;
this file only wires them together and must keep the same return shape."""

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.moderation import candidate, classifier, dict_filter, normalize

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


def moderate(text: str, context: list[str] = (), level: int = 2) -> ModerationResult:
    # 기본 읽기: 복원 글자와 certain 단어는 여기서만 나와요.
    norm = normalize.normalize_with_ends(text)[0]
    certain, ambiguous = [], []
    for s, e, _, kind in candidate.default_hits(text):
        (certain if kind == "certain" else ambiguous).append((s, e))
    # 후보 읽기: 공백/숫자/기호로 가린 철자를 사전에서 찾기만 해요. 여기서 걸린 곳은
    # 모두 애매한 단어로 다뤄요. 이미 certain으로 가려진 곳 안은 건너뛰어요.
    for s, e, _ in candidate.hits(text):
        if not any(cs <= s and e <= ce for cs, ce in certain):
            ambiguous.append((s, e))
    restored = norm
    if certain and not ambiguous:  # certain 단어만: 모델 없이 바로 욕이에요
        return ModerationResult(True, 1.0, _spans(certain), restored, "dict")
    # ambiguous 단어("새끼", "꺼져")는 멀쩡한 말일 수도 있어요: 모델이 판단하고,
    # 가리는 위치는 사전이 찾은 곳이에요. certain 단어는 늘 가려요.
    score = _model_score(text, norm, context)
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
