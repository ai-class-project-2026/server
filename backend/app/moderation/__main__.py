"""화이트리스트 확인: PYTHONPATH=backend:ml/common python -m app.moderation
MODERATION_ONNX_PATH를 주면 모델까지 넣고 확인해요 (모델이 붙여 읽은 글에 속지 않는지)."""

from app.moderation import moderate

# "씨 발아"(씨가 싹트다)는 띄어쓰기를 지우면 "씨발아"가 되지만 욕이 아니에요
for ok in (
    "수박 씨 발아 실험은 내가 정리할게",
    "수박 씨 발아율",
    "씨 발아 조건 조사해 올게",
    "강낭콩 씨 발아 관찰 일지 써야 함 ㅋㅋ",
    "수박씨 발아 실험",
):
    for level in (1, 2, 3):
        r = moderate(ok, level=level)
        assert not r.is_profane, (ok, level, r)
# 같은 글자라도 욕으로 쓴 말은 그대로 가려요
for bad, spans in (("야 이 씨 발아", [[4, 7]]), ("씨 발 아 짜증나", [[0, 3]]), ("아 씨발아 진짜", [[2, 4]])):
    r = moderate(bad, level=3)
    assert r.is_profane and r.spans == spans, (bad, r)
print("ok")
