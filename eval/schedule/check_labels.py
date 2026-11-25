"""라벨 파일 검사, 그리고 두 라벨러 비교.

    # 1) 내가 단 정답에 실수가 없는지
    uv run python eval/schedule/check_labels.py data/schedule/real_test/labels_a.jsonl
    # 2) 두 사람이 다르게 단 대화만 보기 (이것만 토론하면 된다)
    uv run python eval/schedule/check_labels.py labels_a.jsonl labels_b.jsonl

검사하는 것: 날짜 형식, 요일이 날짜와 맞는지, status 값, start가 HH:MM인지,
start와 time_text를 같이 적지 않았는지, 참석/불참이 참여자 안에 있는지,
확정 일정이 기준일보다 과거가 아닌지(8.3 제외 대상), type 값,
확정 일정의 참석+불참이 참여자 전체와 딱 맞는지, type과 status가 서로 맞는지,
같은 card_id가 두 번 나오지 않는지. 끝에 type별(약점 카드는 weakness별) 개수를 보여 준다.
두 라벨러 비교는 일정의 모든 칸을 보고, 참석/불참 이름은 순서를 무시한다.
"""

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

WD = "월화수목금토일"
STATUS = {"proposed", "rejected", "confirmed", "cancelled"}
TYPES = {"confirm", "proposal_only", "revise", "cancel", "partial_absent", "relative_date", "not_schedule"}
# 한국 시각(KST). 서버 프롬프트(prompt.py)와 같은 규칙: 시간대가 있으면 KST로 바꾸고, 없으면 이미 KST로 본다.
KST = dt.timezone(dt.timedelta(hours=9))
HHMM = re.compile(r"[01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]")  # fullmatch로만 쓴다 ($는 끝의 줄바꿈을 통과시킨다)
YMD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # 20261015, 2026-W42-4 같은 ISO 변형은 받지 않는다


def _read(path: str) -> list:
    """줄 하나가 깨져도 나머지를 계속 검사하도록, 깨진 줄은 {"_bad_json": 줄번호}로 돌려준다."""
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            out.append(rec if isinstance(rec, dict) else {"_bad_json": n})
    return out


def _is_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def problems(rec: dict) -> list[str]:
    out = []
    if not isinstance(rec, dict):
        return ["줄이 JSON 객체가 아님"]
    if "_bad_json" in rec:
        return [f"{rec['_bad_json']}번째 줄이 JSON 객체가 아님"]
    kind = rec.get("type")
    if not isinstance(kind, str) or kind not in TYPES:
        out.append(f"type이 이상함: {kind!r}")
    if rec.get("weakness") is not None and not isinstance(rec["weakness"], str):
        out.append(f"weakness는 문자열이어야 함: {rec['weakness']!r}")
    ref = None
    try:
        when = dt.datetime.fromisoformat(rec["ref_datetime"])
        ref = (when.astimezone(KST) if when.tzinfo else when).date()  # 날짜는 KST로 바꾼 뒤에 구한다
    except (KeyError, TypeError, ValueError):
        out.append(f"ref_datetime이 ISO 시각이 아님: {rec.get('ref_datetime')!r}")
    if _is_str_list(rec.get("participants")):
        people = set(rec["participants"])
    else:
        out.append("participants는 이름(문자열) 목록이어야 함")
        people = set()
    events = rec.get("events")
    if not isinstance(events, list):
        return out + ["events는 목록이어야 함"]
    for i, e in enumerate(events, 1):
        tag = f"일정{i}"
        if not isinstance(e, dict):
            out.append(f"{tag}: 객체가 아님 ({e!r})")
            continue
        if not isinstance(e.get("title"), str) or not e["title"].strip():
            out.append(f"{tag}: title은 비어 있지 않은 문자열이어야 함 ({e.get('title')!r})")
        for field in ("weekday", "time_text", "location", "note"):
            if e.get(field) is not None and not isinstance(e[field], str):
                out.append(f"{tag}: {field}는 문자열 또는 null ({e[field]!r})")
        status = e.get("status")
        if not isinstance(status, str) or status not in STATUS:
            out.append(f"{tag}: status는 {sorted(STATUS)} 중 하나 ({status!r})")
        start = e.get("start")
        if start is not None and not (isinstance(start, str) and HHMM.fullmatch(start)):
            out.append(f"{tag}: start는 '19:00'처럼 ({start!r})")
        if start is not None and e.get("time_text"):
            out.append(f"{tag}: start와 time_text는 둘 중 하나만 (숫자로 바꿀 수 없을 때만 time_text)")
        for field in ("attendees", "absent"):
            if not _is_str_list(e.get(field, [])):
                out.append(f"{tag}: {field}는 이름(문자열) 목록이어야 함 ({e.get(field)!r})")
        attendees = set(e.get("attendees") or []) if _is_str_list(e.get("attendees", [])) else set()
        absent = set(e.get("absent") or []) if _is_str_list(e.get("absent", [])) else set()
        for field, names in (("attendees", attendees), ("absent", absent)):
            if names - people:
                out.append(f"{tag}: {field}에 참여자가 아닌 이름 {sorted(names - people)}")
        if attendees & absent:
            out.append(f"{tag}: 참석과 불참에 동시에 있음 {sorted(attendees & absent)}")
        d = None
        if isinstance(e.get("date"), str) and YMD.fullmatch(e["date"]):
            try:
                d = dt.date.fromisoformat(e["date"])
            except ValueError:
                pass
        if d is None:
            out.append(f"{tag}: date는 YYYY-MM-DD ({e.get('date')!r})")
            continue
        if e.get("weekday") and isinstance(e["weekday"], str) and e["weekday"] != WD[d.weekday()]:
            out.append(f"{tag}: {d}는 {WD[d.weekday()]}요일인데 weekday={e['weekday']!r}")
        if status == "confirmed":
            # 확정이면 모든 참여자가 참석 아니면 불참이어야 한다 (무응답자는 attendees)
            seen = attendees | absent
            if seen != people:
                out.append(f"{tag}: 확정인데 참석+불참이 참여자와 다름 (빠진 사람 {sorted(people - seen)})")
            if ref and d < ref:
                out.append(f"{tag}: 기준일({ref})보다 과거인 확정 일정 (8.3 제외 대상)")
    status = [e.get("status") for e in events if isinstance(e, dict)]
    if kind == "not_schedule" and "confirmed" in status:
        out.append("type이 not_schedule인데 확정 일정이 있음 (type이나 정답 중 하나가 틀림)")
    if kind == "confirm" and "confirmed" not in status:
        out.append("type이 confirm인데 확정 일정이 없음")
    if kind == "proposal_only" and "confirmed" in status:
        out.append("type이 proposal_only인데 확정 일정이 있음")
    if kind == "cancel" and "cancelled" not in status:
        out.append("type이 cancel인데 취소된 일정이 없음")
    if kind == "partial_absent" and not any(
        isinstance(e, dict) and e.get("status") == "confirmed" and e.get("absent") for e in events
    ):
        out.append("type이 partial_absent인데 불참자가 있는 확정 일정이 없음")
    if kind == "revise" and not events:
        out.append("type이 revise인데 일정이 하나도 없음")
    return out


def _safe_problems(rec) -> list[str]:
    try:
        return problems(rec)
    except Exception as e:  # noqa: BLE001  예상 못 한 모양이라도 그 줄만 문제로 알리고 계속한다
        return [f"검사 중 오류({type(e).__name__}): 줄 모양을 확인하기"]


def _events(rec: dict) -> list[dict]:
    """비교용: problems()가 문제로 알린 일정(일정N: ...)은 뺀다. 모양이 틀린 값으로 비교하다 죽지 않게."""
    events = rec.get("events")
    if not isinstance(events, list):
        return []
    bad = {int(m[1]) for p in _safe_problems(rec) if (m := re.match(r"일정([0-9]+):", p))}
    return [e for i, e in enumerate(events, 1) if isinstance(e, dict) and i not in bad]


def _summary(e: dict) -> str:
    when = e.get("start") or e.get("time_text") or "-"
    absent = f" 불참={e['absent']}" if e.get("absent") else ""
    return f"{e.get('status')} {e.get('date')} {when}{absent}"


def _key(e: dict) -> tuple:
    """일정의 모든 칸. 이름 목록은 순서를 무시하고, 빈 값(None, "", [])은 같게 본다."""
    fields = ("title", "date", "weekday", "start", "time_text", "location", "status", "note")
    names = ("attendees", "absent")
    return tuple(e.get(f) or None for f in fields) + tuple(tuple(sorted(e.get(f) or [])) for f in names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    args = ap.parse_args()

    def check(path: str) -> tuple[list, int]:
        recs, count = _read(path), 0
        for n, rec in enumerate(recs, 1):
            for p in _safe_problems(rec):
                count += 1
                print(f"{path}:{n} {rec.get('card_id', '')} {p}")
        ids = Counter(r["card_id"] for r in recs if isinstance(r.get("card_id"), str))
        for cid in [c for c, k in ids.items() if k > 1]:
            count += 1
            print(f"{path}: card_id {cid} 줄이 {ids[cid]}개 있음")
        for field in ("type", "weakness"):
            kinds = Counter(str(r[field]) for r in recs if r.get(field))
            if kinds:
                print(f"{path}: {field}별 " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        print(f"{path}: {len(recs)}줄 검사, 문제 {count}개")
        return recs, count

    a, bad = check(args.a)
    if not args.b:
        sys.exit(1 if bad else 0)
    b_recs, bad_b = check(args.b)  # B도 같은 검사를 한다
    bad += bad_b
    b = {r.get("card_id"): r for r in b_recs}
    diff = 0
    for rec in a:
        cid = rec.get("card_id")
        other = b.get(cid)
        if other is None:
            print(f"{cid}: {args.b}에 없음")
            diff += 1
            continue
        ka = sorted(map(_key, _events(rec)), key=str)
        kb = sorted(map(_key, _events(other)), key=str)
        if ka != kb or rec.get("type") != other.get("type"):
            diff += 1
            print(f"\n{cid} ({rec.get('type')} / {other.get('type')})")
            print("  A:", "; ".join(map(_summary, _events(rec))) or "일정 없음")
            print("  B:", "; ".join(map(_summary, _events(other))) or "일정 없음")
    print(f"\n{len(a)}개 중 {diff}개가 다름 → 이것만 같이 보고 정하기")
    sys.exit(1 if bad else 0)  # 문제가 있는 라벨 파일이면 종료 코드로도 알린다


if __name__ == "__main__":
    main()
