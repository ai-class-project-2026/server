"""Step ③ of plan 9.2: KcELECTRA exported to ONNX (ml/moderation/export_onnx.py).

Returns P(profane) in [0, 1], or None when MODERATION_ONNX_PATH is empty.
Needs `uv sync --group onnx` (onnxruntime + tokenizers) only when a model is set.
"""

from functools import cache, lru_cache

import numpy as np

from app.config import settings


@cache
def _load():
    import onnxruntime as ort
    from tokenizers import Tokenizer

    path = settings.moderation_onnx_path
    tok = Tokenizer.from_file(f"{path}/tokenizer.json")
    tok.enable_truncation(128)
    return tok, ort.InferenceSession(
        f"{path}/model.onnx", providers=["CPUExecutionProvider"]
    )


def second_segment(norm: str, context: list[str]) -> str:
    """Normalized text, then the last 3 messages newest first.

    Truncation cuts from the end, so the oldest message is dropped first and the
    message being judged is kept. Training and serving must both use this.
    """
    return " [SEP] ".join([norm, *reversed(list(context)[-3:])])


@lru_cache(maxsize=256)
def _encode(text: str, segment: str):
    """score()와 visible()이 같은 토큰을 써요. 같은 글을 두 번 자르지 않게 기억해 둬요."""
    return _load()[0].encode(text, segment)


def visible(text: str, norm: str, context: list[str]) -> tuple[int, int]:
    """모델이 실제로 읽는 앞부분의 길이: (원문 글자 수, 정규화한 글자 수).
    128토큰을 넘으면 뒤가 잘려서 모델은 그 뒤를 못 봐요. 원문(첫째 칸)과 정규화한 글(둘째 칸의
    맨 앞)이 각각 어디까지 남았는지 score()와 똑같이 잘라서 세요."""
    enc = _encode(text, second_segment(norm, context))
    ends = [0, 0]
    for seq, (_, end) in zip(enc.sequence_ids, enc.offsets):
        if seq is not None:
            ends[seq] = max(ends[seq], end)
    return ends[0], min(ends[1], len(norm))


def score(text: str, norm: str, context: list[str]) -> float | None:
    if not settings.moderation_onnx_path:
        return None
    _, sess = _load()
    enc = _encode(text, second_segment(norm, context))
    feed = {
        "input_ids": [enc.ids],
        "attention_mask": [enc.attention_mask],
        "token_type_ids": [enc.type_ids],
    }
    names = {i.name for i in sess.get_inputs()}
    logits = sess.run(
        None, {k: np.array(v, dtype=np.int64) for k, v in feed.items() if k in names}
    )[0][0]
    e = np.exp(logits - logits.max())
    return float(e[1] / e.sum())
