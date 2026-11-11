from app.moderation import moderate


def test_dictionary_catches_bypasses_and_maps_spans():
    for text in ["시발 뭐하냐", "씨@발", "시 발", "ㅅㅂ", "ＴＬＱＫＦ"]:
        assert moderate(text).is_profane, text
    r = moderate("아 씨@발 진짜")
    assert r.spans == [[2, 5]]  # covers "씨@발" in the original text


def test_whitelist_and_clean():
    assert not moderate("시발점부터 다시").is_profane
    assert not moderate("내일 7시 회의").is_profane
