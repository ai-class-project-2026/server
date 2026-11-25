"""개선 전후 그래프 (기획서 8.8, 15장 4번). score.py가 쌓은 CSV → HTML 한 장.

    uv run python eval/schedule/chart.py --testset test.jsonl --labels qwen-base claude-zeroshot qwen-lora-v4
    uv run python eval/schedule/chart.py --demo        # 그래프 코드 자체 검사

--labels는 보여 줄 순서대로 (최대 3개: 학습 전, 상용 LLM, 우리 모델). 같은 label이 여러 줄이면 마지막 줄을 쓴다.
결과: eval/reports/schedule_chart.html. 브라우저로 열고, 값은 막대 오른쪽과 맨 아래 표에 있다.
없는 값은 0으로 그리지 않고 "값 없음"이라고 적는다.
testset 이름에 "_ai"가 있으면 AI 역할극 자료라는 경고를 맨 위에 넣는다.
"""

import argparse
import csv
import html
import sys
from pathlib import Path

# (칸 이름, 제목, 목표, 목표 방향, 축 최댓값, 배수, 단위). 목표는 기획서 4.2
METRICS = [
    ("confirmed_f1", "확정 일정 F1", 0.90, "이상", 1, 1, ""),
    ("false_registration", "오등록률", 5, "이하", 100, 100, "%"),
    ("cancel_recall", "취소 감지율", 85, "이상", 100, 100, "%"),
    ("latency_ms_mean", "평균 응답 시간", 3, "이하", None, 0.001, "초"),
]
TYPES = {
    "confirm": "단순 확정", "proposal_only": "제안만", "revise": "번복", "cancel": "취소",
    "partial_absent": "부분 참석", "relative_date": "상대 날짜", "not_schedule": "일정 아님",
}

CSS = """
body{margin:0;background:var(--s)} .v{--s:#fcfcfb;--t:#0b0b0b;--t2:#52514e;--g:#e4e3df;--c0:#2a78d6;--c1:#eb6834;--c2:#1baf7a;
background:var(--s);color:var(--t);font:16px/1.5 'Noto Sans KR','Malgun Gothic',sans-serif;max-width:1040px;margin:auto;padding:32px 24px}
@media (prefers-color-scheme:dark){body{background:#1a1a19}.v{--s:#1a1a19;--t:#f0efec;--t2:#c3c2b7;--g:#383835;--c0:#3987e5;--c1:#d95926;--c2:#199e70}}
h1{font-size:26px;margin:0 0 4px} h2{font-size:18px;margin:32px 0 8px} p{margin:0;color:var(--t2)}
.warn{border:2px solid var(--t);padding:10px 14px;margin:14px 0;color:var(--t);font-weight:700}
.lg{display:flex;gap:20px;flex-wrap:wrap;margin:14px 0;color:var(--t2)} .lg i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:7px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:28px 40px} svg{width:100%;height:auto;display:block}
.sc{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums} th,td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--g)}
th:first-child,td:first-child{text-align:left} th{color:var(--t2);font-weight:600}
"""


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def show(v, unit):
    return "값 없음" if v is None else (f"{v:.2f}" if not unit else f"{v:.1f}{unit}")


def bars(title: str, rows: list[tuple[str, float | None]], top: float, unit="", goal=None, direction="") -> str:
    """가로 막대. rows 순서 = 색 순서. goal이 있으면 세로선으로 긋는다."""
    x0, width, h, gap = 4, 280, 18, 10
    height = len(rows) * (h + gap) + 30
    out = [f'<svg viewBox="0 0 380 {height}" role="img" aria-label="{html.escape(title)}">']
    out.append(f'<line x1="{x0}" x2="{x0}" y1="0" y2="{height - 26}" stroke="var(--g)"/>')
    for i, (name, v) in enumerate(rows):
        y = i * (h + gap) + 4
        w = 0 if v is None else max(0, min(v / top, 1)) * width
        if w:
            r = min(4, w)  # 바깥쪽 끝만 둥글게
            out.append(
                f'<path d="M{x0} {y}h{w - r:.1f}a{r:.1f} {r:.1f} 0 0 1 {r:.1f} {r:.1f}v{h - 2 * r:.1f}a{r:.1f} {r:.1f} 0 0 1 -{r:.1f} {r:.1f}h-{w - r:.1f}z"'
                f' fill="var(--c{i})"><title>{html.escape(name)}: {show(v, unit)}</title></path>'
            )
        out.append(f'<text x="{x0 + width + 14}" y="{y + 14}" font-size="14" fill="var(--t)">{show(v, unit)}</text>')
    if goal is not None:
        x = x0 + goal / top * width
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height - 26}" stroke="var(--t)" stroke-width="1.5"/>')
        anchor = "end" if x > 190 else "start"
        out.append(f'<text x="{x:.1f}" y="{height - 8}" font-size="13" text-anchor="{anchor}" fill="var(--t2)">목표 {goal:g}{unit} {direction}</text>')
    return "".join(out) + "</svg>"


def page(rows: list[dict], testset: str, labels: list[str]) -> str:
    """rows = schedule_by_type.csv의 줄들 (type이 ALL인 줄이 전체 점수)."""
    last = {(r["label"], r["type"]): r for r in rows if r["testset"] == testset}
    missing = [x for x in labels if (x, "ALL") not in last]
    if missing:
        sys.exit(f"{testset}에 이 label의 점수가 없어요: {', '.join(missing)}")
    n = {x: last[x, "ALL"]["n"] for x in labels}
    if len(set(n.values())) > 1:
        sys.exit(f"label마다 대화 수가 달라서 비교할 수 없어요: {n}")
    out = [f"<style>{CSS}</style><div class='v'><h1>일정 AI 개선 전후</h1>"]
    out.append(f"<p>{html.escape(testset)} · 대화 {next(iter(n.values()))}개</p>")
    if "_ai" in testset:
        out.append("<p class='warn'>AI 역할극으로 잰 값. 사람 테스트셋이 아니라서 최종 점수로 쓰지 않는다.</p>")
    if len(labels) > 1:
        out.append("<div class='lg'>" + "".join(f"<span><i style='background:var(--c{i})'></i>{html.escape(x)}</span>" for i, x in enumerate(labels)) + "</div>")
    out.append("<div class='grid'>")
    for key, title, goal, direction, top, scale, unit in METRICS:
        vals = [(x, None if (v := num(last[x, "ALL"][key])) is None else v * scale) for x in labels]
        limit = top or max([goal, *(v for _, v in vals if v is not None)]) * 1.1
        out.append(f"<div><h2>{title}</h2>{bars(title, vals, limit, unit, goal, direction)}</div>")
    out.append("</div><h2>유형별 확정 일정 F1</h2><div class='grid'>")
    types = list(dict.fromkeys(t for (x, t) in last if x in labels and t != "ALL"))
    types.sort(key=lambda t: (list(TYPES).index(t) if t in TYPES else 99, t))
    for t in types:
        vals = [(x, num(last.get((x, t), {}).get("confirmed_f1"))) for x in labels]
        name = TYPES.get(t, t)
        out.append(f"<div><h2>{html.escape(name)}</h2>{bars(name, vals, 1)}</div>")
    out.append("</div><h2>표로 보기</h2><div class='sc'><table><tr><th>모델</th><th>유형</th><th>대화</th><th>F1</th><th>오등록률</th><th>취소 감지율</th><th>날짜 정확도</th><th>응답(초)</th></tr>")
    for x in labels:
        for t in ["ALL", *types]:
            if r := last.get((x, t)):
                pct = lambda k: show(None if (v := num(r[k])) is None else v * 100, "%")  # noqa: E731
                ms = num(r["latency_ms_mean"])
                cells = [x, "전체" if t == "ALL" else TYPES.get(t, t), r["n"], show(num(r["confirmed_f1"]), ""), pct("false_registration"),
                         pct("cancel_recall"), pct("date_accuracy"), "값 없음" if ms is None else f"{ms / 1000:.1f}"]
                out.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    out.append("</table></div></div>")
    return "<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>일정 AI 개선 전후</title>" + "".join(out) + "</html>"


def _demo():
    row = lambda x, t, f1, lat="": {  # noqa: E731
        "label": x, "testset": "t_ai.jsonl", "type": t, "n": "3", "confirmed_f1": f1, "false_registration": "0.1",
        "cancel_recall": "", "date_accuracy": "1.0", "latency_ms_mean": lat}
    rows = [row("a", "ALL", "0.1"), row("a", "ALL", "0.5", "5200"), row("a", "cancel", ""), row("b", "ALL", "0.9", "2000")]
    h = page(rows, "t_ai.jsonl", ["a", "b"])
    assert "0.50" in h and "0.10" not in h  # 같은 label이면 마지막 줄
    assert "값 없음" in h and "AI 역할극" in h and "5.2초" in h and "목표 0.9 이상" in h
    assert "AI 역할극" not in page([r | {"testset": "test.jsonl"} for r in rows], "test.jsonl", ["a"])
    print("demo 통과")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--testset", default="test.jsonl")
    ap.add_argument("--labels", nargs="+", help="보여 줄 label, 최대 3개")
    ap.add_argument("--report", default="eval/reports/schedule_by_type.csv")
    ap.add_argument("--out", default="eval/reports/schedule_chart.html")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        return _demo()
    if not args.labels or len(args.labels) > 3:
        ap.error("--labels를 1~3개 적어 주세요")
    with open(args.report, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(rows, args.testset, args.labels), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
