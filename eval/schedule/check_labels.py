"""라벨 파일 검사, 그리고 두 라벨러 비교.

    # 1) 내가 단 정답에 실수가 없는지
    uv run python eval/schedule/check_labels.py data/schedule/real_test/labels_a.jsonl
    # 2) 두 사람이 다르게 단 대화만 보기 (이것만 토론하면 된다)
    uv run python eval/schedule/check_labels.py labels_a.jsonl labels_b.jsonl

검사하는 것: 날짜 형식, 요일이 날짜와 맞는지, status 값, start가 HH:MM인지,
start와 time_text를 같이 적지 않았는지, 참석/불참이 참여자 안에 있는지,
확정 일정이 기준일보다 과거가 아닌지(8.3 제외 대상), type 값.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

WD = "월화수목금토일"
STATUS = {"proposed", "rejected", "confirmed", "cancelled"}
TYPES = {"confirm", "proposal_only", "revise", "cancel", "partial_absent", "relative_date", "not_schedule"}
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _read(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def problems(rec: dict) -> list[str]:
    out = []
    if rec.get("type") not in TYPES:
        out.append(f"type이 이상함: {rec.get('type')!r}")
    ref = dt.datetime.fromisoformat(rec["ref_datetime"]).date()
    people = set(rec["participants"])
    for i, e in enumerate(rec["events"], 1):
        tag = f"일정{i}"
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, TypeError, ValueError):
            out.append(f"{tag}: date는 YYYY-MM-DD ({e.get('date')!r})")
            continue
        if e.get("weekday") and e["weekday"] != WD[d.weekday()]:
            out.append(f"{tag}: {d}는 {WD[d.weekday()]}요일인데 weekday={e['weekday']!r}")
        if e.get("status") not in STATUS:
            out.append(f"{tag}: status는 {sorted(STATUS)} 중 하나 ({e.get('status')!r})")
        start = e.get("start")
        if start is not None and not HHMM.match(start):
            out.append(f"{tag}: start는 '19:00'처럼 ({start!r})")
        if start is not None and e.get("time_text"):
            out.append(f"{tag}: start와 time_text는 둘 중 하나만 (숫자로 바꿀 수 없을 때만 time_text)")
        if not e.get("title"):
            out.append(f"{tag}: title이 비어 있음")
        for field in ("attendees", "absent"):
            extra = set(e.get(field) or []) - people
            if extra:
                out.append(f"{tag}: {field}에 참여자가 아닌 이름 {sorted(extra)}")
        both = set(e.get("attendees") or []) & set(e.get("absent") or [])
        if both:
            out.append(f"{tag}: 참석과 불참에 동시에 있음 {sorted(both)}")
        if e.get("status") == "confirmed" and d < ref:
            out.append(f"{tag}: 기준일({ref})보다 과거인 확정 일정 (8.3 제외 대상)")
    if rec.get("type") == "not_schedule" and any(e.get("status") == "confirmed" for e in rec["events"]):
        out.append("type이 not_schedule인데 확정 일정이 있음 (type이나 정답 중 하나가 틀림)")
    return out


def _summary(e: dict) -> str:
    when = e.get("start") or e.get("time_text") or "-"
    absent = f" 불참={e['absent']}" if e.get("absent") else ""
    return f"{e.get('status')} {e.get('date')} {when}{absent}"


def _key(e: dict) -> tuple:
    return (e.get("status"), e.get("date"), e.get("start") or e.get("time_text"), tuple(sorted(e.get("absent") or [])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    args = ap.parse_args()

    a = _read(args.a)
    bad = 0
    for n, rec in enumerate(a, 1):
        for p in problems(rec):
            bad += 1
            print(f"{args.a}:{n} {rec.get('card_id', '')} {p}")
    print(f"{len(a)}줄 검사, 문제 {bad}개")

    if not args.b:
        sys.exit(1 if bad else 0)
    b = {r.get("card_id"): r for r in _read(args.b)}
    diff = 0
    for rec in a:
        cid = rec.get("card_id")
        other = b.get(cid)
        if other is None:
            print(f"{cid}: {args.b}에 없음")
            diff += 1
            continue
        ka = sorted(map(_key, rec["events"]), key=str)
        kb = sorted(map(_key, other["events"]), key=str)
        if ka != kb or rec.get("type") != other.get("type"):
            diff += 1
            print(f"\n{cid} ({rec.get('type')} / {other.get('type')})")
            print("  A:", "; ".join(map(_summary, rec["events"])) or "일정 없음")
            print("  B:", "; ".join(map(_summary, other["events"])) or "일정 없음")
    print(f"\n{len(a)}개 중 {diff}개가 다름 → 이것만 같이 보고 정하기")


if __name__ == "__main__":
    main()
