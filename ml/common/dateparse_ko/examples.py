"""Part 4: "입력 → 정답 날짜" 예시 모음 (plan 8.5). 100개 이상으로 늘려요.

    uv run python ml/common/dateparse_ko/examples.py

기준일은 전부 2026-09-30(수). 틀린 줄만 ✗로 표시돼요.
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from dateparse_ko import parse

REF = dt.date(2026, 9, 30)  # 수요일

EXAMPLES = [
    ("오늘 보자", dt.date(2026, 9, 30)),
    ("내일 ㄱㄱ", dt.date(2026, 10, 1)),
    ("모레 어때", dt.date(2026, 10, 2)),
    ("글피는 안돼", dt.date(2026, 10, 3)),
    ("다음주 화", dt.date(2026, 10, 6)),
    ("금요일 ㄱ", dt.date(2026, 10, 2)),
    ("이번 주말", dt.date(2026, 10, 3)),
    ("10/15 회의", dt.date(2026, 10, 15)),
]

if __name__ == "__main__":
    ok = 0
    for text, want in EXAMPLES:
        got = [m.date for m in parse(text, REF) if m.date]
        ok += got == [want]
        print(f"{'✓' if got == [want] else '✗'} {text!r}: 정답 {want}, 결과 {got}")
    print(f"\n{ok}/{len(EXAMPLES)} 정답")
