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


_WD = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_REL = {"오늘": 0, "내일": 1, "낼": 1, "모레": 2, "글피": 3, "내일모레": 2, "낼모레": 2}
_WEEKS = {"다다음": 2, "다담": 2, "다음": 1, "담": 1}
_AM = {"오전", "아침", "새벽"}
_PM = {"오후", "저녁", "밤", "낮"}
_TIME_TEXTS = ("수업 끝나고", "수업끝나고", "점심 시간", "점심시간", "방과 후", "방과후")

# 조사·"이나/나"는 날짜 범위에 넣지 않는다. "금요일에"의 span은 "금요일".
_PARTICLE = r"(?:으로|에서|까지|부터|이나|은|는|이|가|을|를|도|만|에|의|로|나|랑)"
_TAIL = rf"(?:(?=[^\d가-힣]|$)|(?={_PARTICLE}))"
_WD_WORD = r"[월화수목금토일](?:요일|욜)?"

_WEEK = re.compile(
    rf"(?<![가-힣])(?P<prefix>다다음|다담|다음|이번|담)\s*주\s*(?P<wd>{_WD_WORD}){_TAIL}"
)
_WEEKEND = re.compile(
    rf"(?<![가-힣])(?P<prefix>다다음|다담|다음|이번|담)?\s*주\s*말{_TAIL}"
)
_MD = re.compile(rf"(?<!\d)(?P<month>\d{{1,2}})\s*/\s*(?P<day>\d{{1,2}}){_TAIL}")
_MD_KO = re.compile(
    rf"(?<!\d)(?P<month>\d{{1,2}})\s*월\s*(?P<day>\d{{1,2}})\s*일{_TAIL}"
)
_REL_WORD = re.compile(rf"(?<![가-힣])(?P<word>내일모레|낼모레|글피|모레|내일|오늘|낼){_TAIL}")
_BARE_WD = re.compile(rf"(?<![\d가-힣])(?P<wd>{_WD_WORD}){_TAIL}")
_SHIFT_TAIL = re.compile(
    rf"의\s*그\s*(?P<which>전주|다음주)\s*(?P<wd>[월화수목금토일])(?:요일|욜)?{_TAIL}"
)
_BARE_SHIFT = re.compile(
    rf"(?<![가-힣])그\s*(?P<which>전주|다음주)\s*(?P<wd>[월화수목금토일])(?:요일|욜)?{_TAIL}"
)
_CLOCK = re.compile(
    r"(?:(?P<hint>오전|오후|아침|저녁|밤|새벽|낮)\s*)?"
    r"(?<!\d)(?P<hour>\d{1,2})\s*시(?!간)"
    r"(?:\s*(?P<minute>\d{1,2})\s*분|\s*반)?"
)


@dataclass
class _Hit:
    start: int
    end: int
    date: dt.date | None = None
    time: dt.time | None = None
    time_text: str | None = None


def _week_monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _nearest(ref: dt.date, wd: int) -> dt.date:
    """기준일 이후 가장 가까운 요일. 기준일 당일이 그 요일이면 당일."""
    return ref + dt.timedelta(days=(wd - ref.weekday()) % 7)


def _nth_week(ref: dt.date, weeks: int, wd: int) -> dt.date:
    """월요일에 시작하는 주. weeks=1이 다음주."""
    return _week_monday(ref) + dt.timedelta(days=7 * weeks + wd)


def _weekend(ref: dt.date, weeks: int) -> dt.date:
    """주말은 그 주 토요일. 이번 주 토요일이 이미 지났으면 일요일."""
    monday = _week_monday(ref) + dt.timedelta(days=7 * weeks)
    saturday = monday + dt.timedelta(days=5)
    if weeks == 0 and ref > saturday:
        return monday + dt.timedelta(days=6)
    return saturday


def _abs_date(ref: dt.date, month: int, day: int) -> dt.date | None:
    """올해 날짜. 기준일보다 이전이면 내년. 없는 날(2/30)은 버린다."""
    if not 1 <= month <= 12:
        return None
    for year in (ref.year, ref.year + 1):
        try:
            found = dt.date(year, month, day)
        except ValueError:
            continue
        if found >= ref:
            return found
    return None


def _shift(anchor: dt.date, which: str, wd: int) -> dt.date:
    """anchor가 속한 주의 전주/다음주 요일."""
    monday = _week_monday(anchor)
    monday += dt.timedelta(days=-7 if which == "전주" else 7)
    return monday + dt.timedelta(days=wd)


def _wd_index(token: str) -> int:
    return _WD[token[0]]


def _bare_ok(text: str, start: int, end: int) -> bool:
    """한 글자 요일은 '할 수 있어', '할 일' 같은 말과 겹치지 않게."""
    token = text[start:end]
    if len(token) != 1:
        return True
    prev = text[:start].rstrip()
    nxt = text[end:].lstrip()
    if token == "수" and (
        prev.endswith(("할", "있을", "없을", "있는", "없는"))
        or nxt.startswith(("있", "없", "많", "업"))
    ):
        return False
    if token == "일" and prev.endswith("할"):
        return False
    if token == "월" and prev.endswith(("몇", "개", "매")):
        return False
    return True


def _resolve(hits: list[_Hit]) -> list[_Hit]:
    """겹치면 더 긴 표현을 남긴다. '다음주 화'가 '화'를 삼키지 않게."""
    ordered = sorted(hits, key=lambda h: (h.start, -(h.end - h.start)))
    chosen: list[_Hit] = []
    for hit in ordered:
        if hit.end <= hit.start:
            continue
        if any(hit.start < kept.end and kept.start < hit.end for kept in chosen):
            continue
        chosen.append(hit)
    chosen.sort(key=lambda h: h.start)
    return chosen


def _date_atoms(text: str, ref: dt.date) -> list[_Hit]:
    hits: list[_Hit] = []
    for match in _WEEK.finditer(text):
        wd = _wd_index(match.group("wd"))
        prefix = match.group("prefix")
        if prefix == "이번":
            found = _nearest(ref, wd)
        else:
            found = _nth_week(ref, _WEEKS[prefix], wd)
        hits.append(_Hit(match.start(), match.end(), date=found))
    for match in _WEEKEND.finditer(text):
        weeks = _WEEKS.get(match.group("prefix"), 0)
        hits.append(_Hit(match.start(), match.end(), date=_weekend(ref, weeks)))
    for pattern in (_MD_KO, _MD):
        for match in pattern.finditer(text):
            found = _abs_date(ref, int(match.group("month")), int(match.group("day")))
            if found:
                hits.append(_Hit(match.start(), match.end(), date=found))
    for match in _REL_WORD.finditer(text):
        found = ref + dt.timedelta(days=_REL[match.group("word")])
        hits.append(_Hit(match.start(), match.end(), date=found))
    for match in _BARE_WD.finditer(text):
        if not _bare_ok(text, match.start(), match.end()):
            continue
        found = _nearest(ref, _wd_index(match.group("wd")))
        hits.append(_Hit(match.start(), match.end(), date=found))
    return _resolve(hits)


def _shifts(text: str, ref: dt.date) -> list[_Hit]:
    """'10/15의 그 전주 목요일', '그 전주 목요일'."""
    atoms = _date_atoms(text, ref)
    hits: list[_Hit] = []
    for match in _SHIFT_TAIL.finditer(text):
        before = [
            atom
            for atom in atoms
            if atom.date
            and atom.end <= match.start()
            and not text[atom.end : match.start()].strip()
        ]
        if not before:
            continue
        anchor = max(before, key=lambda atom: atom.end)
        found = _shift(anchor.date, match.group("which"), _wd_index(match.group("wd")))
        hits.append(_Hit(anchor.start, match.end(), date=found))
    for match in _BARE_SHIFT.finditer(text):
        found = _shift(ref, match.group("which"), _wd_index(match.group("wd")))
        hits.append(_Hit(match.start(), match.end(), date=found))
    return hits


def _clock(hour: int, minute: int, hint: str | None) -> dt.time | None:
    """시각. 애매하면 None을 반환하고 호출부가 원문을 time_text로 남긴다."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hint in _AM:
        if hour == 12:
            hour = 0
        elif hour > 12:
            return None
        return dt.time(hour, minute)
    if hint in _PM:
        if hour < 12:
            hour += 12
        return dt.time(hour, minute)
    if 1 <= hour <= 7:
        return dt.time(hour + 12, minute)
    if 8 <= hour <= 11:
        return None
    return dt.time(hour, minute)


def _clock_hits(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for phrase in _TIME_TEXTS:
        start = 0
        while True:
            found = text.find(phrase, start)
            if found < 0:
                break
            hits.append(_Hit(found, found + len(phrase), time_text=phrase))
            start = found + len(phrase)
    for match in _CLOCK.finditer(text):
        raw = match.group(0).lstrip()
        start = match.end() - len(raw)
        hour = int(match.group("hour"))
        hint = match.group("hint")
        minute = int(match.group("minute")) if match.group("minute") else 0
        if match.group(0).rstrip().endswith("반"):
            minute = 30
        resolved = _clock(hour, minute, hint)
        # 8~11시는 오전/오후가 없으면 숫자로 바꾸지 않고 원문을 남긴다.
        ambiguous = resolved is None and hint is None and 8 <= hour <= 11
        if resolved is None and not ambiguous:
            continue
        hits.append(
            _Hit(
                start,
                match.end(),
                time=resolved,
                time_text=None if resolved else raw,
            )
        )
    return hits


def parse(text: str, ref: dt.date) -> list[Mention]:
    """Find date/time expressions in one message, relative to `ref` (message day)."""
    hits = _resolve([*_shifts(text, ref), *_date_atoms(text, ref), *_clock_hits(text)])
    return [
        Mention(hit.start, hit.end, date=hit.date, time=hit.time, time_text=hit.time_text)
        for hit in hits
    ]
