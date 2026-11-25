"""Score schedule predictions against gold (plan 8.8).

    python eval/schedule/score.py data/schedule/real_test/test.jsonl preds.jsonl --label v1
    python eval/schedule/score.py --demo        # 채점 코드 자체 검사

gold line: a record with "events" (and optional "type"); pred line: {"events": [...]}
정답에 "weakness"가 있으면(hard.jsonl) type 대신 "약점: ..."으로 묶어서 약점마다 점수를 낸다.
(run.py가 남기는 "latency_ms", "usage", "cost_usd"가 있으면 같이 집계한다).
Prints the metrics and appends a row to eval/reports/schedule.csv (dashboard input)
and one row per type to eval/reports/schedule_by_type.csv.

일정 짝짓기 규칙 (한 대화 안에서, 정답 하나에 예측 하나만 짝이 된다):
  1) 날짜+시각+상태가 모두 같다  2) 날짜+상태가 같다 (시각만 틀림)
  3) 날짜+시각이 같다 (상태만 틀림)  4) 날짜만 같다
시각은 start가 있으면 start, start가 null이면 time_text(공백 뺀 원문)로 비교한다.
"""

import argparse
import csv
import difflib
import json
import math
from collections import defaultdict
from pathlib import Path


def _time_key(e: dict) -> str:
    """start가 있으면 HH:MM, 없으면 time_text도 시각의 일부로 본다."""
    if e.get("start"):
        return str(e["start"])[:5]
    return "~" + "".join((e.get("time_text") or "").split())


def _f1(tp, n_pred, n_gold):
    if not n_pred and not n_gold:
        return None  # 정답도 예측도 없으면 이 지표는 해당 없음 (1.0점을 주지 않는다)
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gold if n_gold else 1.0
    return 2 * p * r / (p + r) if p + r else 0.0


def _pair(left: list, right: list, same) -> list[tuple]:
    """같은 조건이면 하나씩만 짝지어 준다. 짝이 된 것은 left/right에서 빠진다."""
    pairs = []
    for i in list(left):
        for j in right:
            if same(i, j):
                pairs.append((i, j))
                left.remove(i)
                right.remove(j)
                break
    return pairs


def _match(pred: list[dict], gold: list[dict]) -> list[tuple[int, dict]]:
    """일정 짝짓기. 모듈 위쪽 설명의 1)~4) 순서로 짝을 찾는다."""
    left, right = list(range(len(pred))), list(range(len(gold)))
    steps = [
        lambda p, g: (p["date"], _time_key(p), p["status"]) == (g["date"], _time_key(g), g["status"]),
        lambda p, g: (p["date"], p["status"]) == (g["date"], g["status"]),
        lambda p, g: (p["date"], _time_key(p)) == (g["date"], _time_key(g)),
        lambda p, g: p["date"] == g["date"],
    ]
    out = []
    for same in steps:
        out += _pair(left, right, lambda i, j: same(pred[i], gold[j]))
    return [(i, gold[j]) for i, j in out]  # (예측 번호, 짝이 된 정답)


def _same_event(p: dict, g: dict) -> float:
    """날짜를 빼고 얼마나 같은 일정처럼 보이는지. 날짜 정확도용 짝짓기에 쓴다."""
    title = difflib.SequenceMatcher(None, p.get("title") or "", g.get("title") or "").ratio()
    loc = bool(p.get("location")) and p.get("location") == g.get("location")
    return (
        2 * title
        + (1 if _time_key(p) == _time_key(g) else 0)
        + (0.5 if p["status"] == g["status"] else 0)
        + (0.5 if loc else 0)
    )


def _date_hits(pred: list[dict], gold: list[dict]) -> int:
    """날짜가 맞은 예측 수. 날짜 말고 다른 내용으로 짝을 짓고, 그 짝의 날짜를 비교한다.
    짝이 없는 예측(없는 일정을 만든 경우)은 틀린 것으로 센다."""
    # 배열 순서가 점수를 바꾸면 안 된다. 그래서 (1) 날짜까지 전부 같은 일정부터 짝을 짓고,
    # (2) 나머지는 비슷한 순서로, 점수가 같으면 날짜가 같은 짝을 먼저, 그다음 내용으로 정한다.
    used_p, used_g, hits = set(), set(), 0
    ident = lambda e: (e["date"], e.get("title"), _time_key(e), e["status"], e.get("location"))  # noqa: E731
    for i, p in enumerate(pred):
        for j, g in enumerate(gold):
            if j not in used_g and ident(p) == ident(g):
                used_p.add(i)
                used_g.add(j)
                hits += 1
                break
    cands = sorted(
        (
            (-_same_event(p, g), p["date"] != g["date"], ident(p), ident(g), i, j)
            for i, p in enumerate(pred)
            for j, g in enumerate(gold)
            if i not in used_p and j not in used_g
        ),
        key=lambda c: (c[0], c[1], tuple(map(str, c[2])), tuple(map(str, c[3]))),
    )
    for s, _, _, _, i, j in cands:
        if -s < 1.0 or i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        hits += pred[i]["date"] == gold[j]["date"]
    return hits


def _percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[max(0, math.ceil(q * len(xs)) - 1)]


def _new_counter():
    return defaultdict(int) | {"latency": [], "in": [], "out": [], "cost": []}


def _metrics(c) -> dict:
    def ratio(a, b):
        return round(a / b, 4) if b else None

    f1 = _f1(c["tp"], c["pred"], c["gold"])
    m = {
        "n": c["n"],
        "confirmed_f1": None if f1 is None else round(f1, 4),
        "false_registration": ratio(c["false_reg"], c["pred"]) if c["pred"] else 0.0,
        "cancel_recall": ratio(c["cancel_hit"], c["cancel_gold"]),
        "date_accuracy": ratio(c["date_ok"], c["events_pred"]),
    }
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    m["latency_ms_mean"] = mean(c["latency"])
    m["latency_ms_p95"] = _percentile(c["latency"], 0.95) if c["latency"] else None
    m["input_tokens_mean"] = mean(c["in"])
    m["output_tokens_mean"] = mean(c["out"])
    m["cost_usd_per_1000"] = round(sum(c["cost"]) / len(c["cost"]) * 1000, 4) if c["cost"] else None
    return m


def score(golds: list[dict], preds: list[dict]) -> dict:
    assert len(golds) == len(preds), "one prediction line per gold line"
    tot = _new_counter()
    by_type = defaultdict(_new_counter)
    for g, p in zip(golds, preds):
        pairs = _match(p["events"], g["events"])
        pc = [i for i, e in enumerate(p["events"]) if e["status"] == "confirmed"]
        gc = [e for e in g["events"] if e["status"] == "confirmed"]
        gx = [e for e in g["events"] if e["status"] == "cancelled"]
        partner = dict(pairs)
        full = [
            (p["events"][i], b)
            for i, b in pairs
            if (_time_key(p["events"][i]), p["events"][i]["date"], p["events"][i]["status"])
            == (_time_key(b), b["date"], b["status"])
        ]
        group = f"약점: {g['weakness']}" if g.get("weakness") else g.get("type", "all")
        for t in (tot, by_type[group]):
            t["n"] += 1
            t["tp"] += sum(a["status"] == "confirmed" for a, _ in full)
            t["pred"] += len(pc)
            t["gold"] += len(gc)
            # 오등록: 확정으로 냈는데 짝이 없거나 짝의 정답이 확정이 아닌 것.
            # 날짜는 맞고 시각만 틀린 확정은 F1에서만 틀리고 오등록은 아니다.
            t["false_reg"] += sum(
                i not in partner or partner[i]["status"] != "confirmed" for i in pc
            )
            t["cancel_gold"] += len(gx)
            t["cancel_hit"] += sum(b["status"] == "cancelled" for _, b in full)
            t["events_pred"] += len(p["events"])
            t["date_ok"] += _date_hits(p["events"], g["events"])
            if p.get("latency_ms") is not None:
                t["latency"].append(p["latency_ms"])
            usage = p.get("usage") or {}
            if usage.get("input_tokens") is not None:
                t["in"].append(usage["input_tokens"])
                t["out"].append(usage.get("output_tokens") or 0)
            if p.get("cost_usd") is not None:
                t["cost"].append(p["cost_usd"])
    result = _metrics(tot)
    result["by_type"] = {k: _metrics(v) for k, v in sorted(by_type.items())}
    result["f1_by_type"] = {k: v["confirmed_f1"] for k, v in result["by_type"].items()}
    return result


def _read(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append(report: Path, header: list[str], rows: list[list]):
    report.parent.mkdir(parents=True, exist_ok=True)
    new = not report.exists()
    with report.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def _demo():
    """채점 규칙이 말한 대로 움직이는지 확인한다. 틀리면 AssertionError."""

    def ev(date, start, status, title="회의", text=None):
        return {"title": title, "date": date, "start": start, "time_text": text, "status": status}

    def run(gold, pred, typ="confirm", extra=None):
        return score([{"type": typ, "events": gold}], [{"events": pred, **(extra or {})}])

    ok = ev("2026-10-12", "19:00", "confirmed")
    # 1) start가 null이면 time_text까지 같아야 맞다
    a = ev("2026-10-12", None, "confirmed", text="점심시간")
    b = ev("2026-10-12", None, "confirmed", text="방과후")
    assert run([a], [a])["confirmed_f1"] == 1.0
    assert run([a], [b])["confirmed_f1"] == 0.0
    # 2) 시각만 틀린 확정은 오등록이 아니다. 확정이 아닌 걸 확정으로 내면 오등록이다
    m = run([ok], [ev("2026-10-12", "20:00", "confirmed")])
    assert m["confirmed_f1"] == 0.0 and m["false_registration"] == 0.0
    assert run([ev("2026-10-12", "19:00", "proposed")], [ok])["false_registration"] == 1.0
    assert run([], [ok])["false_registration"] == 1.0
    # 3) 같은 일정을 두 번 내면 벌점
    m = run([ok], [ok, ok])
    assert m["false_registration"] == 0.5 and round(m["confirmed_f1"], 4) == 0.6667
    # 4) 날짜가 서로 바뀌면 틀린 것
    g1, g2 = ev("2026-10-12", "19:00", "confirmed", "발표 연습"), ev("2026-10-14", "19:00", "confirmed", "자료 조사")
    p1, p2 = dict(g1, date="2026-10-14"), dict(g2, date="2026-10-12")
    assert run([g1, g2], [g1, g2])["date_accuracy"] == 1.0
    assert run([g1, g2], [p1, p2])["date_accuracy"] == 0.0
    # 4-2) 같은 일정이 날짜만 다를 때 예측 배열 순서가 점수를 바꾸면 안 된다
    twin = [ok, dict(ok, date="2026-10-14")]
    assert run(twin, twin)["date_accuracy"] == run(twin, twin[::-1])["date_accuracy"] == 1.0
    # 5) 취소를 놓치면 그 유형의 F1이 1.0이 아니다
    cx = ev("2026-10-12", "19:00", "cancelled")
    m = run([cx], [], typ="cancel")
    assert m["by_type"]["cancel"]["confirmed_f1"] is None
    assert m["by_type"]["cancel"]["cancel_recall"] == 0.0
    assert run([cx], [cx], typ="cancel")["by_type"]["cancel"]["cancel_recall"] == 1.0
    # 5-2) weakness가 있으면 type 대신 약점으로 묶는다
    golds = [{"type": "confirm", "weakness": "애매한 답", "events": [ok]}, {"type": "confirm", "events": [ok]}]
    assert set(score(golds, [{"events": []}] * 2)["by_type"]) == {"약점: 애매한 답", "confirm"}
    # 6) 지연시간, 토큰, 비용 집계
    extra = {"latency_ms": 100, "usage": {"input_tokens": 10, "output_tokens": 5}, "cost_usd": 0.002}
    m = score([{"events": [ok]}] * 2, [{"events": [ok], **extra}, {"events": [ok], **extra, "latency_ms": 300}])
    assert m["latency_ms_mean"] == 200 and m["latency_ms_p95"] == 300
    assert m["input_tokens_mean"] == 10 and m["cost_usd_per_1000"] == 2.0
    print("demo 통과")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", nargs="?")
    ap.add_argument("pred", nargs="?")
    ap.add_argument("--demo", action="store_true", help="채점 코드 자체 검사")
    ap.add_argument(
        "--label",
        help="model/version name for the report, e.g. qwen3b-lora-v1",
    )
    ap.add_argument("--report", default="eval/reports/schedule.csv")
    ap.add_argument("--by-type-report", default="eval/reports/schedule_by_type.csv")
    args = ap.parse_args()
    if args.demo:
        return _demo()
    if not (args.gold and args.pred and args.label):
        ap.error("gold, pred, --label are required (or use --demo)")

    m = score(_read(args.gold), _read(args.pred))
    print(json.dumps(m, ensure_ascii=False, indent=2))
    testset = Path(args.gold).name
    # schedule.csv의 열과 순서는 web 대시보드가 쉼표로 잘라 읽으니 바꾸지 않는다.
    _append(
        Path(args.report),
        ["label", "testset", "n", "confirmed_f1", "false_registration", "cancel_recall", "date_accuracy"],
        [[args.label, testset, m["n"], m["confirmed_f1"], m["false_registration"], m["cancel_recall"], m["date_accuracy"]]],
    )
    cols = [
        "n", "confirmed_f1", "false_registration", "cancel_recall", "date_accuracy",
        "latency_ms_mean", "latency_ms_p95", "input_tokens_mean", "output_tokens_mean", "cost_usd_per_1000",
    ]
    rows = [[args.label, testset, "ALL", *[m[c] for c in cols]]]
    rows += [[args.label, testset, k, *[v[c] for c in cols]] for k, v in m["by_type"].items()]
    _append(Path(args.by_type_report), ["label", "testset", "type", *cols], rows)


if __name__ == "__main__":
    main()
