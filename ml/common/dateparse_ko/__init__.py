"""Korean relative date normalizer (plan 8.5). Owned by part 4.

Shared by training (ml/) and serving (backend/app/schedule): both import
`from dateparse_ko import parse`. Keep the signature stable.

    >>> parse("내일 7시 ㄱㄱ", dt.date(2026, 9, 30))
    [Mention(start=0, end=2, date=datetime.date(2026, 10, 1), time=None, time_text=None),
     Mention(start=3, end=5, date=None, time=datetime.time(19, 0), time_text=None)]
"""

import bisect
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
    # True: 한 글자 요일("목", "화"...)이라 요일인지 확실하지 않은 후보. 대화를 보고 정한다.
    tentative: bool = False


_WD = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_REL = {"오늘": 0, "내일": 1, "낼": 1, "모레": 2, "글피": 3, "내일모레": 2, "낼모레": 2}
_WEEKS = {"다다음": 2, "다담": 2, "다음": 1, "담": 1}
_AM = {"오전", "아침", "새벽"}
_PM = {"오후", "저녁", "밤", "낮"}
_TIME_TEXTS = ("수업 끝나고", "수업끝나고", "점심 시간", "점심시간", "방과 후", "방과후")

# 조사·"이나/나"는 날짜 범위에 넣지 않는다. "금요일에"의 span은 "금요일".
_PARTICLE = r"(?:으로|에서|까지|부터|이나|은|는|이|가|을|를|도|만|에|엔|쯤|경|의|로|나|랑)"
# "내일오후 7시"처럼 시간 말이 바로 붙어도 날짜로 본다.
_TIME_WORD = r"(?:오전|오후|아침|저녁|새벽|밤|낮)"
# "목입니다", "수라고", "화예요"처럼 이다/인용 어미가 바로 붙은 꼴
_COPULA = r"(?:입니다|입니까|이에요|예요|이예요|이라고|라고|이라는|라는|이라며|이래|이야|이었|였|이라서|이니까|인데|이죠|이지|이면|이고)"
_TAIL = rf"(?:(?=[^\d가-힣]|$)|(?={_PARTICLE})|(?={_TIME_WORD})|(?={_COPULA}))"
_WD_WORD = r"[월화수목금토일](?:요일|욜)?"

# "그 다음주 화요일"의 "그"는 앞 날짜를 가리킨다. 기준일로 계산하지 않고 아래 _SHIFT/_BARE_SHIFT가 맡는다.
_WEEK = re.compile(
    rf"(?<![가-힣])(?<!그 )(?P<prefix>다다음|다담|다음|이번|담)\s*주\s*(?P<wd>{_WD_WORD}){_TAIL}"
)
_WEEKEND = re.compile(
    rf"(?<![가-힣])(?:(?P<prefix>다다음|다담|다음|이번|담)\s*(?:주\s*)?)?주\s*말{_TAIL}"  # "다음 주말", "다음주 주말"
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
# "시" 바로 뒤에 올 수 있는 것: 분/반, 공백, 글자가 아닌 것, 조사, 조사가 붙어 이어지는 말("7시에보자", "7시이후")
_SI_END = (
    rf"(?=\s*[0-9]{{1,2}}\s*분|\s*반|\s|$|[^가-힣]|{_PARTICLE}(?![가-힣])"
    r"|(?:에는|으로|로|에|엔|은|는|부터|까지|쯤|경|이후|이전|정각|무렵|전|후|께))"
)
_CLOCK = re.compile(
    r"(?:(?P<hint>오전|오후|아침|저녁|밤|새벽|낮)\s*)?"
    rf"(?<!\d)(?P<hour>\d{{1,2}})\s*시(?!간){_SI_END}"
    r"(?:\s*(?P<minute>\d{1,2})\s*분|\s*반)?"
)


@dataclass
class _Hit:
    start: int
    end: int
    date: dt.date | None = None
    time: dt.time | None = None
    time_text: str | None = None
    tentative: bool = False


def _week_monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _nearest(ref: dt.date, wd: int) -> dt.date:
    """'이번주 X요일': 기준일 이후 가장 가까운 요일. 기준일 당일이 그 요일이면 당일."""
    return ref + dt.timedelta(days=(wd - ref.weekday()) % 7)


def _upcoming(ref: dt.date, wd: int) -> dt.date:
    """'이번주' 없이 요일만 말하면 다음에 올 그 요일. 오늘이 그 요일이면 7일 뒤
    (수요일에 "수요일에 보자"는 보통 다음 주 수요일이다. 오늘이면 "오늘"이라고 한다)."""
    return ref + dt.timedelta(days=(wd - ref.weekday() - 1) % 7 + 1)


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


# 한 글자 요일("목", "화", "일"...)은 흔한 낱말(목, 화, 일, 수, 금)과 같다. 파서는 이것이 요일인지 정하지 않는다.
# 날짜 후보로만 알리고(tentative=True) 정말 요일인지는 대화를 보고 LLM이 정한다 (prompt.py의 "?" 표시).
#  - 바로 뒤에 시각이 오면("목 7시") 요일로 본다 (tentative=False).
#  - 낱말 속 글자("목도리", "수학", "금요")나 문법 구조("볼 수 있어", "할 일")는 후보에서 뺀다.
_DAY = r"[월화수목금토일](?:요일|욜)?"
_CLOCK_AFTER = re.compile(rf"\s*(?:에\s*)?(?:(?:오전|오후|아침|저녁|밤|새벽|낮)\s*)?\d{{1,2}}\s*시(?!간){_SI_END}")
# 조사가 바로 붙고 띄어쓰기 없이 말이 이어지는 꼴("목에보자", "월은안돼")만 후보로 본다. "목도리"의 "도"는 여기에 없다
_ATTACHED = re.compile(r"(?:에는|으로|로|에|엔|은|는)(?=[가-힣])")
_STANDALONE = re.compile(rf"(?!경){_PARTICLE}(?![가-힣])")  # 한 글자 뒤의 "경"은 낱말의 일부다(월경)


def _has_rieul_final(word: str) -> bool:
    return bool(word) and "가" <= word[-1] <= "힣" and (ord(word[-1]) - 0xAC00) % 28 == 8


def _bare_kind(text: str, start: int, end: int) -> str | None:
    """None: 요일 후보가 아님 / "explicit": 요일로 본다 / "tentative": 후보로만 알린다."""
    token = text[start:end]
    if len(token) != 1:
        return "explicit"
    after = text[end:]
    if (
        after[:1] and "가" <= after[0] <= "힣"
        and not _STANDALONE.match(after) and not _ATTACHED.match(after) and not re.match(_COPULA, after)
    ):
        return None  # 낱말 속 글자: 목도리, 수학, 금요, 화장실, 월경
    # 의존명사 "~ㄹ 수 있/없/밖에", "~ㄹ 수도 있다"는 문법 구조다. 앞말이 아니라 뒤따르는 말로 가린다
    # ("다들 수 가능", "리허설 수에"는 진짜 요일)
    if token == "수" and re.match(r"\s*(?:도|가|는|야)?\s*(?:있|없|밖에)", after):
        return None
    words = text[:start].split()
    prev = words[-1] if words else ""
    # "할 일", "볼 일": 한 글자 동사 + ㄹ 받침 바로 뒤의 일 ("리허설 일"처럼 두 글자 이상은 요일일 수 있다)
    if token == "일" and len(prev) == 1 and _has_rieul_final(prev):
        return None
    return "explicit" if _CLOCK_AFTER.match(after) else "tentative"


def _resolve(hits: list[_Hit]) -> list[_Hit]:
    """겹치면 더 긴 표현을 남긴다. '다음주 화'가 '화'를 삼키지 않게.
    시작 위치순으로 한 번만 훑는다: 남긴 것들은 서로 안 겹치니 마지막 끝 위치만 보면 된다."""
    ordered = sorted(hits, key=lambda h: (h.start, -(h.end - h.start)))
    chosen: list[_Hit] = []
    last_end = 0
    for hit in ordered:
        if hit.end <= hit.start or hit.start < last_end:
            continue
        chosen.append(hit)
        last_end = hit.end
    return chosen


# "10/15(목)", "10/15 목요일", "목요일 10/15", "오늘이 수요일이니까": 요일이 바로 옆 날짜를 다시 말한 것
_APPOSED = re.compile(r"이?\s*[(（]?\s*")


def _drop_apposed(text: str, hits: list[_Hit], bare: list[_Hit]) -> list[_Hit]:
    """바로 옆 날짜의 요일을 다시 말한 것뿐인 요일은 뺀다. 안 그러면 기준일로 따로 계산해서
    "10/15 목요일"이 10/15와 10/1 두 날짜가 된다. 날짜의 실제 요일과 같을 때만 뺀다
    (요일이 다르면 어느 쪽이 맞는지 모르니 둘 다 알린다)."""
    bare_ids = {id(h) for h in bare}
    out = []
    for i, hit in enumerate(hits):
        if id(hit) in bare_ids:
            prev = hits[i - 1] if i else None
            after = hits[i + 1] if i + 1 < len(hits) else None
            near = [prev] if prev and _APPOSED.fullmatch(text[prev.end : hit.start]) else []
            if after and not text[hit.end : after.start].strip():  # 뒤쪽은 띄어쓰기만: "목요일 10/15"
                near.append(after)
            if any(
                h.date and id(h) not in bare_ids and h.date != hit.date and h.date.weekday() == hit.date.weekday()
                for h in near
            ):
                continue
        out.append(hit)
    return out


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
            # 없는 날짜(2월 30일)도 자리는 차지한다. 안 그러면 "월", "일"이 요일로 읽힌다.
            hits.append(_Hit(match.start(), match.end(), date=found))
    for match in _REL_WORD.finditer(text):
        found = ref + dt.timedelta(days=_REL[match.group("word")])
        # "낼"은 "내일"의 줄임말이지만 "화 낼 일"처럼 "내다"의 활용형도 되므로 후보로만 알린다
        hits.append(_Hit(match.start(), match.end(), date=found, tentative=match.group("word") == "낼"))
    bare: list[_Hit] = []
    for match in _BARE_WD.finditer(text):
        kind = _bare_kind(text, match.start(), match.end())
        if kind is None:
            continue
        # "그 전주 목요일"의 "목요일"만 따로 날짜로 읽지 않는다.
        if re.search(r"그\s*(?:전주|다음주)\s*$", text[: match.start()]):
            continue
        found = _upcoming(ref, _wd_index(match.group("wd")))
        bare.append(_Hit(match.start(), match.end(), date=found, tentative=kind == "tentative"))
    return _drop_apposed(text, _resolve([*hits, *bare]), bare)


def _shifts(text: str, ref: dt.date, atoms: list[_Hit]) -> list[_Hit]:
    """'10/15의 그 전주 목요일', '그 전주 목요일'. 앞 결과를 다시 앞 날짜로 써서 왼쪽부터 이어 계산한다
    ('10/15의 그 전주 목요일의 그 다음주 화요일' = 10/8의 다음주 화요일 = 10/13)."""
    # 없는 날짜(2월 30일)도 후보에 넣는다. 가장 가까운 날짜 모양이 없는 날짜면 그 뒤 표현은 계산하지 않는다
    # (더 앞의 멀쩡한 날짜를 대신 쓰면 엉뚱한 날짜가 나온다)
    anchors = [_Hit(a.start, a.end, date=a.date, tentative=a.tentative) for a in atoms]
    ends = [a.end for a in anchors]
    moved: set[int] = set()
    for match in _SHIFT_TAIL.finditer(text):
        i = bisect.bisect_right(ends, match.start()) - 1
        if i < 0:
            continue
        gap = text[anchors[i].end : match.start()]
        # 앞 날짜와 "의" 사이에 "회의" 같은 짧은 말은 껴도 된다. 문장 부호가 있거나 너무 길면 다른 얘기다.
        if len(gap) > 20 or re.search(r"[.!?\n]", gap):
            continue
        anchor = anchors[i]
        if anchor.date is None:
            continue
        anchor.date = _shift(anchor.date, match.group("which"), _wd_index(match.group("wd")))
        anchor.end = ends[i] = match.end()
        # 이번 표현 안에 들어 있던 다른 날짜("그 다음주 월요일"의 "다음주 월요일")는 앞 날짜 후보에서 뺀다.
        while i + 1 < len(anchors) and anchors[i + 1].start < match.end():
            del anchors[i + 1], ends[i + 1]
        moved.add(i)
    hits = [anchors[i] for i in sorted(moved)]
    for match in _BARE_SHIFT.finditer(text):
        # "회의의 그 전주 목요일"처럼 "의"로 앞 날짜를 받는데 날짜가 없으면 정하지 않는다.
        if text[: match.start()].rstrip().endswith("의"):
            continue
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
        if hint == "밤":
            # 밤 12시·0시는 자정, 밤 1~5시는 새벽 시각 그대로, 밤 6~11시는 저녁(+12). 13시 이상은 말이 안 된다.
            if hour > 12:
                return None
            if hour <= 5 or hour == 12:
                return dt.time(hour % 12, minute)
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
        if minute > 59:  # 8시 99분 같은 건 오전/오후가 애매한 게 아니라 시각이 아니다
            continue
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
    atoms = _date_atoms(text, ref)
    hits = _resolve([*_shifts(text, ref, atoms), *atoms, *_clock_hits(text)])
    return [
        Mention(hit.start, hit.end, date=hit.date, time=hit.time, time_text=hit.time_text, tentative=hit.tentative)
        for hit in hits
        if hit.date or hit.time or hit.time_text  # 없는 날짜의 자리 표시는 내보내지 않는다
    ]
