"""화이트리스트 확인: PYTHONPATH=backend:ml/common python -m app.moderation
MODERATION_ONNX_PATH를 주면 모델까지 넣고 확인해요 (모델이 붙여 읽은 글에 속지 않는지)."""

from app.config import settings
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
# 강조하려고 붙인 "개"는 "개"만 가려요. 낱말 끝의 "개"(찌개, 3개)와 다른 낱말(개구리, 개편)은 그대로 둬요
for bad, spans in (
    ("개웃기네", [[0, 1]]),
    ("진짜 개좋아", [[3, 4]]),
    ("완전개맛있어", [[2, 3]]),
    ("전개 개빠르고 개꿀잼", [[3, 4], [8, 9]]),
    ("ㅋㅋ개웃겨", [[2, 3]]),
    ("개웃긴데 개짜증남", [[0, 1], [5, 6]]),
):
    for level in (1, 2, 3):
        r = moderate(bad, level=level)
        assert r.is_profane and r.spans == spans, (bad, level, r)
for ok in ("김치찌개맛있어", "3개많아", "소개잘해줘", "개구리 귀엽다", "조직 개편해야 함", "개별로 연락할게", "개싸움 났대", "전개빨라서 좋은데", "나라망신 개망신"):
    r = moderate(ok, level=3)
    assert not r.is_profane, (ok, r)
# "니 엄마 창년"을 소리만 바꿔 쓴 말: "너임마/니임마/니엄마"를 붙여 쓰고 바로 "청년"이 오면 가려요
for bad in (
    "니임마 청년", "니임마청년", "너임마청년", "너임마 청년", "니엄마 청년", "야 니임마청년이냐 ㅋㅋ",
    "니 임마 청년", "너 임마청년 ㅋㅋ", "야 니 엄마 청년!!",
):
    for level in (1, 2, 3):
        r = moderate(bad, level=level)
        assert r.is_profane and r.spans, (bad, level, r)
# "너 임마"처럼 띄어 쓴 것은 멀쩡한 말일 수 있어서 모델이 판단해요 (모델이 없으면 가려요)
if settings.moderation_onnx_path:
    for ok in ("너 임마 청년이 그러면 되냐", "니 임마 청년이 그라믄 되나", "니 엄마 청년 때 사진 봤어", "임마 청년이면 열심히 해야지"):
        r = moderate(ok, level=3)
        assert not r.is_profane, (ok, r)
print("ok")
