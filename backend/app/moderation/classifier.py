"""Step ③ of plan 9.2: KcELECTRA exported to ONNX (ml/moderation/export_onnx.py).

Returns P(profane) in [0, 1], or None when MODERATION_ONNX_PATH is empty.
Needs `uv sync --group onnx` (onnxruntime + tokenizers) only when a model is set.
"""

from functools import cache

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


def score(text: str, norm: str, context: list[str]) -> float | None:
    if not settings.moderation_onnx_path:
        return None
    tok, sess = _load()
    enc = tok.encode(text, second_segment(norm, context))
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
