"""Step ③ of plan 9.2: KcELECTRA exported to ONNX. TODO(part 3 + part 1).

Contract: return probability of profanity in [0, 1], or None when no model is
configured (settings.moderation_onnx_path empty). Must stay under ~100ms on CPU.
Load the onnxruntime session once at module level, not per call.
"""

from app.config import settings


def score(text: str, norm: str, context: list[str]) -> float | None:
    if not settings.moderation_onnx_path:
        return None
    raise NotImplementedError("load ONNX model and run it here")
