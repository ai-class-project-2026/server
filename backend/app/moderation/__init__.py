"""Profanity pipeline (plan 9.2). Part 3 owns normalize/dict_filter/classifier;
this file only wires them together and must keep the same return shape."""

from dataclasses import dataclass

from app.config import settings
from app.moderation import classifier, dict_filter, normalize

# filter_level 1 (lenient) .. 3 (strict) → classifier score needed to flag
THRESHOLDS = {1: 0.9, 2: 0.7, 3: 0.5}


@dataclass
class ModerationResult:
    is_profane: bool
    score: float
    spans: list[list[int]]  # [start, end) in the ORIGINAL text
    restored: str
    model_version: str


def _span(text: str, index_map: list[int], s: int, e: int) -> list[int]:
    """Normalized [s, e) → original [start, end). A normalized char can come from
    several original chars ("ㅅㅣ" → "시"), so the end runs up to where the next
    normalized char starts, minus trailing spaces."""
    end = index_map[e] if e < len(index_map) else len(text)
    while end > index_map[s] + 1 and text[end - 1].isspace():
        end -= 1
    return [index_map[s], end]


def moderate(text: str, context: list[str] = (), level: int = 2) -> ModerationResult:
    norm, index_map = normalize.normalize(text)
    spans = [_span(text, index_map, s, e) for s, e in dict_filter.find(norm)]
    if spans:  # dictionary hit = certain
        return ModerationResult(True, 1.0, spans, norm, "dict")
    score = classifier.score(text, norm, context)
    if score is None:  # no ONNX model configured
        return ModerationResult(False, 0.0, [], norm, "dict")
    return ModerationResult(
        score >= THRESHOLDS[level], score, [], norm, settings.moderation_onnx_path
    )
