"""Part 4: grow this to 100+ cases, one per rule in plan 8.5."""

import datetime as dt

import pytest

from dateparse_ko import parse

WED = dt.date(2026, 9, 30)  # 수요일


@pytest.mark.parametrize(
    "text, expected",
    [
        ("오늘 보자", dt.date(2026, 9, 30)),
        ("내일 ㄱㄱ", dt.date(2026, 10, 1)),
        ("모레 어때", dt.date(2026, 10, 2)),
        ("글피는 안돼", dt.date(2026, 10, 3)),
        pytest.param(
            "다음주 화", dt.date(2026, 10, 6), marks=pytest.mark.xfail(reason="part 4")
        ),
        pytest.param(
            "금요일 ㄱ", dt.date(2026, 10, 2), marks=pytest.mark.xfail(reason="part 4")
        ),
        pytest.param(
            "이번 주말", dt.date(2026, 10, 3), marks=pytest.mark.xfail(reason="part 4")
        ),
        pytest.param(
            "10/15 회의",
            dt.date(2026, 10, 15),
            marks=pytest.mark.xfail(reason="part 4"),
        ),
    ],
)
def test_date(text, expected):
    assert [m.date for m in parse(text, WED) if m.date] == [expected]
