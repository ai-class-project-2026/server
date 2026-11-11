"""Korean relative date normalizer (plan 8.5). Owned by part 4.

Shared by training (ml/) and serving (backend/app/schedule): both import
`from dateparse_ko import parse`. Keep the signature stable.

    >>> parse("내일 7시 ㄱㄱ", dt.date(2026, 9, 30))
    [Mention(start=0, end=2, date=datetime.date(2026, 10, 1), time=None, time_text=None),
     Mention(start=3, end=5, date=None, time=datetime.time(19, 0), time_text=None)]
"""

import datetime as dt
import re
from dataclasses import dataclass


@dataclass
class Mention:
    start: int  # span in the input text
    end: int
    date: dt.date | None = None
    time: dt.time | None = None
    time_text: str | None = None  # "점심시간", "방과후": kept as words


_RELATIVE = {"오늘": 0, "내일": 1, "낼": 1, "모레": 2, "글피": 3}


def parse(text: str, ref: dt.date) -> list[Mention]:
    """Find date/time expressions in one message, relative to `ref` (message day)."""
    out = []
    for m in re.finditer("|".join(_RELATIVE), text):
        out.append(
            Mention(m.start(), m.end(), date=ref + dt.timedelta(_RELATIVE[m[0]]))
        )
    # TODO(part 4): 요일, 다음주/다다음주 X요일, 이번 주말, M/D, M월 D일,
    # "~의 그 전주", N시 (1~7시는 오후), N시 반, 점심시간/방과후 → time_text.
    # Write the test first in backend/tests/test_dateparse.py, then make it pass.
    return out
