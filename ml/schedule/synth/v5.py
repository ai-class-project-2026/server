"""v5 data pipeline: like offline.py, but every turn is tagged with the beat it acts out, so
one written chat also gives prefix rows (the chat cut where a pause is plausible) whose gold
is replayed from the script (scenarios_v5.state), never guessed. Full DESIGN.md, WRITER.md,
JUDGE.md: ~/aip-tools/v5sched/.

    python ml/schedule/synth/v5.py dump --n 2000 --seed 51 --dir RUN --batches 12 --brief BRIEFS
        → RUN/private/scenarios.jsonl            (gold, beats; writers never read private/)
          RUN/writers/wNN/{WRITER.md,input.jsonl}  {id, prompt}
    writers answer in RUN/writers/wNN/output.jsonl: {id, turns: [{speaker, text, beat}]}
    python ml/schedule/synth/v5.py check --dir RUN      (optional, read-only: wNN/rejected.jsonl
        lists {id, reason} per pack so a writer can fix those lines before judge-prep)
    python ml/schedule/synth/v5.py judge-prep --dir RUN --batches 20 --brief BRIEFS
        → RUN/private/candidates.jsonl           {id, chat, part, record}  part = full | p1..p3
          RUN/judges/jNN/{JUDGE.md,input.jsonl}    {id, prompt}: the serving prompt only
    judges answer in RUN/judges/jNN/output.jsonl: {id, events: [...]}
    python ml/schedule/synth/v5.py finish --dir RUN --out data/schedule/synth/train_v5.jsonl

A chat is kept when its full row agrees with its judge (agrees()); its prefix rows are kept
when, in addition, none of its judged prefixes disagrees. The full row and the prefixes of
one chat go to different judge batches, and judge ids say nothing about chats.
"""

import argparse
import collections
import datetime as dt
import json
import random
import re
import secrets
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path[:0] = [str(Path(__file__).parent)]
import generate  # noqa: E402  (sets up the backend/ and ml/common import paths)
import scenarios_v5 as S  # noqa: E402
from app.schemas import ScheduleEvent  # noqa: E402
from dateparse_ko import parse  # noqa: E402
from offline import _ids, _refuse_overwrite, keeps_moved_time, read_jsonl, write_jsonl  # noqa: E402

PROMPT = """너는 {setting} 대화를 쓰는 작가야. 실제 애들이 친 것처럼 써.
- 기준일: {ref}({weekday}). 대화는 이날 하루 안에서 일어남
- 참여자: {people}
- 아래 비트를 번호 순서대로 연기해. 비트 하나는 그 사람의 메시지 1~3개(연달아 보내도 됨). 순서를 바꾸거나 건너뛰지 마:
{flow}
- 턴마다 그 턴이 연기하는 비트 번호를 "beat"에 적어. {filler}
- 작은따옴표 안의 말은 그 비트의 턴 안에 글자 그대로 써. 비트에 없는 날짜, 시간, 장소, 동의, 반대, 불참, 결론을 지어내지 마
- 뒤 비트에서 처음 나오는 날짜나 시간을 앞에서 미리 말하지 마. 말 없는 비트(아무 말도 안 함)에는 그 사람 턴을 쓰지 마
- {confirm_rule}
- 말투: {style}. 동의할 때는 사람마다 다르게 (예: {agree})
- '근데'는 쓰지 마. 같은 문장을 반복하지 마
- 전체 {turns}턴 안팎 (최소 {lo}, 최대 {hi}턴)"""

INSTRUCTION = """너는 {setting}에서 일정 봇에게 요청을 보내는 학생이야.
- 기준일: {ref}({weekday}). 참여자: {people}
- 보내는 사람: {who}
- {say}
- 작은따옴표 안의 말은 글자 그대로 써. 메시지는 반드시 '[요청] '(대괄호, 요청, 띄어쓰기)으로 시작해
- 턴은 딱 1개, "beat"는 1. 말투는 자연스럽게, 오타 조금은 괜찮음"""

TIME_RE = re.compile(r"(?<!\d)\d{1,2}\s*(?:시(?!간)(?:\s*반|\s*\d{1,2}\s*분)?|:\d{2})")
DAYPART_RE = re.compile(r"오전|오후|아침|저녁|밤|낮|새벽")
PLAN_RE = re.compile(r"ㅇㅋ|ㅇㅇ|ㄱㄱ|콜|오케이|(?i:ok)|좋아|좋음|가능|찬성|됨|돼|못\s*가|그때|보자|확정|ㄴㄴ|취소|미루|바꾸|옮기")


class Turn(BaseModel):
    speaker: str
    text: str
    beat: int


class Dialogue(BaseModel):
    turns: list[Turn]


def _sq(x: str) -> str:
    return x.replace(" ", "")


def _phrase(x: str) -> str:
    """Regex for a phrase, spaces optional; "1시" must not match inside "11시"."""
    x = _sq(x)
    return (r"(?<!\d)" if x[:1].isdigit() else "") + r"\s*".join(map(re.escape, x))


def to_prompt(sc: dict) -> str:
    ref = dt.date.fromisoformat(sc["ref_date"])
    base = {"setting": sc["setting"], "ref": ref, "weekday": S.WEEKDAYS[ref.weekday()],
            "people": ", ".join(sc["participants"])}  # fmt: skip
    if sc["type"] == "instruction":
        b = sc["beats"][0]
        return INSTRUCTION.format(**base, who=b["who"], say=b["say"])
    flow = "\n".join(
        f"  {k}. {b['who'] + ': ' if b['who'] else '(말 없음) '}{b['say']}" for k, b in enumerate(sc["beats"], 1)
    )
    words = [k for k, b in enumerate(sc["beats"], 1) if b["word"]]
    talk = sc["small_talk"]
    filler = (f"중간에 '{talk}' 잡담을 한두 턴 섞어도 됨. 잡담 턴은 beat 0이고, 날짜·시간·동의·반대 말은 넣지 마"
              if talk else "잡담 없이 비트만 연기해 (beat 0 턴 없음)")  # fmt: skip
    return PROMPT.format(
        **base, flow=flow, filler=filler, style=sc["style"], agree=", ".join(sc["agree_words"]),
        confirm_rule=f"'확정'이라는 단어는 {', '.join(map(str, words))}번 비트에서만 써" if words else "'확정'이라는 단어는 절대 쓰지 마",
        turns=sc["turns"], lo=sc["min_turns"], hi=sc["max_turns"],
    )  # fmt: skip


def _introduced(sc: dict) -> list[tuple[set, set]]:
    """Per beat: (dates, time phrases, start values) that beat says for the first time. A
    prefix cut before that beat must not contain them."""
    seen_dates = {e["date"] for e in sc["existing_events"]} | set(sc["extra_dates"])
    seen_times, seen_starts = set(), {e["start"] for e in sc["existing_events"]}
    out = []
    for b in sc["beats"]:
        act = b["act"]
        sid = act[1] if act[0] in ("propose", "counter", "move") else None
        dates, times, starts = set(), set(), set()
        if sid:
            s = sc["slots"][sid]
            if s["date"] not in seen_dates and s["say_date"]:
                dates.add(s["date"])
            if s["say_time"] and _sq(s["say_time"]) not in seen_times and (s["start"] is None or s["start"] not in seen_starts):
                times.add(s["say_time"])
                starts |= {s["start"]} - {None}
            seen_dates.add(s["date"])
            seen_starts.add(s["start"])
        seen_times |= {_sq(m) for m in b["must"]}
        out.append((dates, times, starts))
    return out


def check(sc: dict, turns: list[Turn]) -> str | None:
    """Why this chat can't carry its per-beat golds, or None."""
    ref = dt.date.fromisoformat(sc["ref_date"])
    beats = sc["beats"]
    if not sc["min_turns"] <= len(turns) <= sc["max_turns"]:
        return "length"
    if {t.speaker for t in turns} - set(sc["participants"]):
        return "unknown speaker"
    if any(not 0 <= t.beat <= len(beats) for t in turns):
        return "beat number out of range"
    if sc["type"] == "instruction":
        if turns[0].beat != 1 or turns[0].speaker != beats[0]["who"]:
            return "wrong speaker for beat"
        if not turns[0].text.startswith("[요청] "):
            return "no [요청] marker"
    elif any(t.text.lstrip().startswith("[요청]") for t in turns):
        return "[요청] marker in a chat"
    tags = [t.beat for t in turns if t.beat]
    if tags != sorted(tags):
        return "beats out of order"
    if any(t.beat == 0 for t in turns) and not sc["small_talk"]:
        return "small talk not asked for"
    first = {}
    for i, t in enumerate(turns):
        first.setdefault(t.beat, i)
    news = _introduced(sc)
    for k, b in enumerate(beats, 1):
        mine = [t for t in turns if t.beat == k]
        if b["who"] is None:
            if mine:
                return "turn on a silent beat"
            continue
        if not mine:
            return "missing beat"
        if any(t.speaker != b["who"] for t in mine):
            return "wrong speaker for beat"
        said = _sq("".join(t.text for t in mine))
        if any(_sq(w) not in said for w in b["must"]):
            return "missing expression"
        dates, times, starts = news[k - 1]
        before = turns[: first[k]]
        if dates - {m.date.isoformat() for t in mine for m in parse(t.text, ref) if m.date}:
            return "date not resolved"
        if any(m.date and m.date.isoformat() in dates for t in before for m in parse(t.text, ref)):
            return "said before its beat"
        early = " ".join(t.text for t in before)
        if any(re.search(_phrase(x) + r"(?!\s*반|\s*\d)", early) for x in times):
            return "said before its beat"
        if any(m.time and m.time.strftime("%H:%M") in starts for t in before for m in parse(t.text, ref)):
            return "said before its beat"
    word_beats = {k for k, b in enumerate(beats, 1) if b["word"]}
    if any("확정" in t.text and t.beat not in word_beats for t in turns):
        return "says 확정"
    for t in turns:
        if t.beat == 0 and (PLAN_RE.search(t.text) or TIME_RE.search(t.text) or any(m.date for m in parse(t.text, ref))):
            return "small talk touches the plan"
    known = {s["date"] for s in sc["slots"].values()} | {e["date"] for e in sc["existing_events"]}
    known |= set(sc["extra_dates"]) | {e["date"] for g in sc["golds"] for e in g}
    if any(m.date and not m.tentative and m.date.isoformat() not in known for t in turns for m in parse(t.text, ref)):
        return "unscripted date"
    musts = {_sq(w) for b in beats for w in b["must"]}
    # a clock time is fine inside a scripted phrase, or restated shorter with the same value
    # ("오후 1시 반" → "1시 반"); a bare 8~11시 only as scripted
    values = {s["start"] for s in sc["slots"].values()} | {e["start"] for e in sc["existing_events"]}
    values |= {t for w in musts for t in S.times_in(w, ref)} | {e["start"] for g in sc["golds"] for e in g}
    values.discard(None)
    for t in turns:
        text = t.text
        inside = set()
        for w in musts:
            for m in re.finditer(_phrase(w), text):
                inside |= set(range(m.start(), m.end()))
        for m in TIME_RE.finditer(text):
            if not set(range(*m.span())) <= inside and not S.times_in(m.group(), ref) & values:
                return "unscripted time"
        if sc["bare_hour"] and any(not set(range(*m.span())) <= inside for m in DAYPART_RE.finditer(text)):
            return "time-of-day word next to a bare hour"
    return None


def to_record(sc: dict, turns: list[Turn], cut: int | None = None, k: int | None = None) -> dict:
    """The full chat (cut=None) or its first `cut` turns with the gold after beat k."""
    gold = sc["events"] if k is None else sc["golds"][k - 1]
    rows = [generate.Turn(speaker=t.speaker, text=t.text) for t in turns[:cut]]
    return generate.to_record(sc | {"events": gold}, rows)


def cuts(sc: dict, turns: list[Turn], rng: random.Random) -> list[tuple[int, int]]:
    """(beat k, turns kept) for prefix rows: right after a beat where a pause is plausible
    (a proposal or counter waiting for answers, one member's reply while others have not
    answered, an objection, a hedge...), never where the gold is ambiguous, never the full
    chat. Prefer cuts whose gold differs from the final one."""
    last = {t.beat: i for i, t in enumerate(turns) if t.beat}
    options = []
    for k, b in enumerate(sc["beats"], 1):
        if b["who"] and b["act"][0] in S.PAUSE_ACTS and not sc["ambiguous"][k - 1] and k in last:
            if last[k] + 1 < len(turns):
                options.append((k, last[k] + 1))
    useful = [o for o in options if sc["golds"][o[0] - 1] != sc["events"]]
    same = [o for o in options if o not in useful]
    pick = rng.sample(useful, min(sc["max_cuts"], len(useful)))
    if same and len(pick) < sc["max_cuts"] and rng.random() < 0.3:
        pick.append(rng.choice(same))
    return sorted(pick)


# ── agreement between a gold and a judge answer ──────────────────────────────


def _rows(events: list[dict], record: dict) -> list[tuple]:
    """What must match, per event. Rejected ideas don't count. A confirmed event equal to its
    calendar entry once empty fields are filled from it (as reconcile._merge does) is a no-op
    and doesn't count either. Empty attendees on a live event = everyone not absent."""
    people = record["participants"]
    calendar = {(e["date"], (e["start"] or "")[:5]): e for e in record["existing_events"]}
    out = []
    for e in events:
        status, key = e["status"], (e["date"], (e.get("start") or "")[:5])
        if status == "rejected":
            continue
        if status == "cancelled":
            out.append((*key, status, (), (), ""))
            continue
        absent, attendees, loc = sorted(e.get("absent") or []), sorted(e.get("attendees") or []), _sq(e.get("location") or "")
        if status == "confirmed" and key in calendar:
            x = calendar[key]
            absent = absent or sorted(x["absent"])
            attendees = attendees or sorted(x["attendees"])
            loc = loc or _sq(x["location"] or "")
            if (absent, attendees, loc) == (sorted(x["absent"]), sorted(x["attendees"]), _sq(x["location"] or "")):
                continue
        attendees = attendees or sorted(p for p in people if p not in absent)
        out.append((*key, status, tuple(absent), tuple(attendees), loc))
    return out


def agrees(record: dict, answer: dict) -> bool:
    """Same events (multiset: two events at one slot stay two), statuses, absentees,
    attendees and place (one place name may contain the other: "도서관" / "도서관 3층")."""
    dropped = {(e["date"], (e["start"] or "")[:5]) for e in record["events"] if e["status"] == "rejected"}
    said = [e for e in answer["events"]
            if not (e["status"] == "cancelled" and (e["date"], (e.get("start") or "")[:5]) in dropped)]  # fmt: skip
    gold, got = _rows(record["events"], record), _rows(said, record)
    if len(gold) != len(got):
        return False
    for g in gold:
        hit = next((a for a in got if a[:5] == g[:5] and (a[5] in g[5] or g[5] in a[5]) and bool(a[5]) == bool(g[5])), None)
        if hit is None:
            return False
        got.remove(hit)
    return not keeps_moved_time(record, {"events": said})


# ── the three steps ──────────────────────────────────────────────────────────


def _copy_brief(brief: str | None, name: str, folder: Path) -> None:
    if brief and (Path(brief) / name).exists():
        shutil.copy(Path(brief) / name, folder / name)


def _quotas(n: int) -> dict:
    total = sum(f[1] for f in S.FAMILIES.values())
    q = {k: round(n * f[1] / total) for k, f in S.FAMILIES.items()}
    q[max(q, key=q.get)] += n - sum(q.values())
    return q


def dump(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    packs = [folder / "writers" / f"w{k:02d}" for k in range(1, args.batches + 1)]
    _refuse_overwrite([private / "scenarios.jsonl", *packs])
    private.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    todo = [S.make(rng, fam) for fam, k in _quotas(args.n).items() for _ in range(k)]
    ids = _ids(len(todo), set())
    write_jsonl(private / "scenarios.jsonl", ({"id": i, "scenario": sc} for i, sc in zip(ids, todo)))
    order = list(zip(ids, todo))
    random.Random(secrets.randbits(64)).shuffle(order)
    size = -(-len(order) // args.batches)
    for k, pack in enumerate(packs):
        pack.mkdir(parents=True)
        write_jsonl(pack / "input.jsonl", ({"id": i, "prompt": to_prompt(sc)} for i, sc in order[k * size : (k + 1) * size]))
        _copy_brief(args.brief, "WRITER.md", pack)
    kinds = collections.Counter(sc["type"] for sc in todo)
    print(f"{len(todo)} scenarios (seed {args.seed}) → {private / 'scenarios.jsonl'}")
    print("families", dict(kinds))
    print("writer packs", ", ".join(f"{p.name} ({len(read_jsonl(p / 'input.jsonl'))})" for p in packs))


def _answers(folder: Path, pattern: str) -> tuple[list[dict], collections.Counter]:
    rows, bad = [], collections.Counter()
    for path in sorted(folder.glob(pattern)):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad["not json"] += 1
                print(f"  {path}:{n} is not JSON", file=sys.stderr)
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                rows.append(row)
            else:
                bad["no id"] += 1
    return rows, bad


def check_packs(args) -> None:
    """Report check() reasons per writer pack without writing anything outside the packs."""
    folder = Path(args.dir)
    todo = {r["id"]: r["scenario"] for r in read_jsonl(folder / "private" / "scenarios.jsonl")}
    total = collections.Counter()
    for pack in sorted((folder / "writers").iterdir()):
        out = pack / "output.jsonl"
        if not out.exists():
            print(f"{pack.name}: no output.jsonl")
            continue
        ids = {r["id"] for r in read_jsonl(pack / "input.jsonl")}
        rows, why = _answers(pack, "output.jsonl")
        bad, seen = [], set()
        for row in rows:
            if row["id"] not in ids or row["id"] in seen:
                why["unknown or repeated id"] += 1
                continue
            seen.add(row["id"])
            try:
                reason = check(todo[row["id"]], Dialogue.model_validate({"turns": row.get("turns")}).turns)
            except ValueError:
                reason = "not the turns shape"
            if reason:
                bad.append({"id": row["id"], "reason": reason})
                why[reason] += 1
        why["no answer"] += len(ids - seen)
        write_jsonl(pack / "rejected.jsonl", bad)
        total.update(why)
        print(f"{pack.name}: {len(seen) - len(bad)}/{len(ids)} pass", dict(+why))
    print("all packs", dict(+total))


def judge_prep(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    packs = [folder / "judges" / f"j{k:02d}" for k in range(1, args.batches + 1)]
    _refuse_overwrite([private / "candidates.jsonl", *packs])
    if args.batches < 4:
        sys.exit("--batches must be at least 4 (a chat's full row and its prefixes go to different judges)")
    todo = {r["id"]: r["scenario"] for r in read_jsonl(private / "scenarios.jsonl")}
    rows, why = _answers(folder, args.answers)
    by_family, done, chats = collections.Counter(), set(), []
    for row in rows:
        sc = todo.get(row["id"])
        if sc is None or row["id"] in done:
            why["unknown id" if sc is None else "second answer for an id (first kept)"] += 1
            continue
        done.add(row["id"])
        try:
            turns = Dialogue.model_validate({"turns": row.get("turns")}).turns
        except ValueError:
            reason = "not the turns shape"
        else:
            reason = check(sc, turns)
        if reason:
            why[reason] += 1
            by_family[(sc["type"], reason)] += 1
            continue
        rng = random.Random(row["id"])
        parts = [("full", to_record(sc, turns))]
        parts += [(f"p{j}", to_record(sc, turns, cut, k)) for j, (k, cut) in enumerate(cuts(sc, turns, rng), 1)]
        chats.append((row["id"], parts))
    taken = set(todo)
    cands, batches = [], [[] for _ in packs]
    for chat, parts in chats:
        base = secrets.randbelow(len(packs))
        for j, ((part, record), jid) in enumerate(zip(parts, _ids(len(parts), taken))):
            cands.append({"id": jid, "chat": chat, "part": part, "record": record})
            batches[(base + j) % len(packs)].append((jid, record))
    write_jsonl(private / "candidates.jsonl", cands)
    for pack, batch in zip(packs, batches):
        random.Random(secrets.randbits(64)).shuffle(batch)
        pack.mkdir(parents=True)
        write_jsonl(pack / "input.jsonl", ({"id": j, "prompt": generate.judge_prompt((None, r))} for j, r in batch))
        _copy_brief(args.brief, "JUDGE.md", pack)
    parts = collections.Counter(c["part"][0] for c in cands)
    print(f"{len(rows)} writer answers for {len(todo)} scenarios; {len(todo) - len(done)} scenarios have no answer")
    print(f"{len(chats)} chats pass check() → {parts['f']} full + {parts['p']} prefix rows in {len(packs)} judge packs "
          f"({', '.join(str(len(b)) for b in batches)})")  # fmt: skip
    print("dropped", dict(why.most_common()))
    for (kind, reason), n in sorted(by_family.items()):
        print(f"  {kind:>13}  {reason}: {n}")


def finish(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    cands = {c["id"]: c for c in read_jsonl(private / "candidates.jsonl")}
    ledger = private / "finished.txt"
    finished = set(ledger.read_text(encoding="utf-8").split()) if ledger.exists() else set()
    rows, why = _answers(folder, args.answers)
    verdict = {}
    for row in rows:
        c = cands.get(row["id"])
        if c is None or row["id"] in verdict:
            why["unknown id" if c is None else "second answer for an id (first kept)"] += 1
            continue
        try:
            events = [ScheduleEvent.model_validate(e).model_dump(mode="json") for e in row["events"]]
        except (KeyError, TypeError, ValueError):
            why["not the events shape"] += 1
            verdict[row["id"]] = False
            continue
        verdict[row["id"]] = agrees(c["record"], {"events": events})
    by_chat = collections.defaultdict(list)
    for c in cands.values():
        by_chat[c["chat"]].append(c)
    out, stat = [], collections.Counter()
    for chat, group in by_chat.items():
        if chat in finished:
            continue
        full = next(c for c in group if c["part"] == "full")
        family = full["record"]["type"]
        if full["id"] not in verdict:
            stat[(family, "full not judged")] += 1
            continue
        if not verdict[full["id"]]:
            stat[(family, "full: judge disagrees")] += 1
            continue
        prefixes = [c for c in group if c["part"] != "full"]
        bad = [c for c in prefixes if verdict.get(c["id"]) is False]
        keep = [full] + ([] if bad else [c for c in prefixes if verdict.get(c["id"])])
        stat[(family, "full kept")] += 1
        stat[(family, "prefix kept")] += len(keep) - 1
        if bad:
            stat[(family, "prefixes dropped: a prefix disagrees")] += len(prefixes)
        out += [c["record"] | {"snap": c["part"], "chat": chat} for c in keep]
        finished.add(chat)
    with open(args.out, "a", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
    ledger.write_text("".join(f"{c}\n" for c in sorted(finished)), encoding="utf-8")
    print(f"{len(out)} rows → {args.out} (appended): "
          f"{sum(r['snap'] == 'full' for r in out)} full + {sum(r['snap'] != 'full' for r in out)} prefix")  # fmt: skip
    if why:
        print("unusable answer lines", dict(why))
    for fam in sorted({k for k, _ in stat}):
        print(f"  {fam:>13}  " + ", ".join(f"{r}: {n}" for (k, r), n in sorted(stat.items()) if k == fam))


# ── self-check ───────────────────────────────────────────────────────────────


def _mechanical(sc: dict) -> list[dict]:
    """A writer that says exactly the beats: one turn per speaking beat."""
    if sc["type"] == "instruction":
        b = sc["beats"][0]
        return [{"speaker": b["who"], "beat": 1, "text": "[요청] " + " ".join(m for m in b["must"] if m != "[요청]") + " 해줘"}]
    out = []
    for k, b in enumerate(sc["beats"], 1):
        if b["who"]:
            act = b["act"][0]
            tail = {"agree": " ㅇㅋ", "object": " 난 안 돼", "declare": " 그럼 그때 보자", "absent": " 난 못 감 너네끼리 해"}.get(act, "")
            out.append({"speaker": b["who"], "beat": k, "text": (" ".join(b["must"]) + tail).strip() or "음"})
    return out


def demo() -> None:
    import contextlib
    import copy
    import io
    import tempfile

    sys.path[:0] = [str(Path(__file__).parents[1])]
    import build_train
    from app.schedule.prompt import build_messages
    from app.schemas import ScheduleRequest

    # 1. every family: golds replay from the acts, mechanical chats pass check(), records are valid
    rng = random.Random(7)
    per = collections.Counter()
    for fam in S.FAMILIES:
        for _ in range(150):
            sc = S.make(rng, fam)
            assert not (e := S.errors(sc)), (fam, e)
            assert "{" not in to_prompt(sc)
            turns = [Turn(**t) for t in _mechanical(sc)]
            assert (why := check(sc, turns)) is None, (fam, sc["variant"], why, [t.text for t in turns])
            for k, cut in cuts(sc, turns, random.Random(1)):
                assert sc["golds"][k - 1] == S.state(sc, k, False)[0]  # derivable from the script
                r = to_record(sc, turns, cut, k)
                assert build_train.valid(r) and agrees(r, {"events": r["events"]}), (fam, r)
                per[fam] += 1
            r = to_record(sc, turns)
            assert build_train.valid(r) and agrees(r, {"events": r["events"]}), (fam, sc["variant"])
            build_messages(ScheduleRequest.model_validate(r))
    assert all(per[f] for f, v in S.FAMILIES.items() if v[3]), per

    # 2. check() rejects chats that would mislabel a prefix
    rng = random.Random(3)
    sc = next(s for s in (S.make(rng, "premature") for _ in range(200)) if s["variant"] == "objection_counter")
    good = [Turn(**t) for t in _mechanical(sc)]

    def broken(change) -> str | None:
        turns = copy.deepcopy(good)
        change(turns)
        return check(sc, turns)

    third = next(i for i, t in enumerate(good) if sc["beats"][t.beat - 1]["act"][0] == "counter")
    new_time = sc["slots"][sc["beats"][third]["act"][1]]["say_time"]
    assert broken(lambda t: t.__setitem__(slice(1, 3), [t[2], t[1]])) == "beats out of order"
    assert broken(lambda t: (t.pop(1), t.append(t[-1].model_copy()))) == "missing beat"
    assert broken(lambda t: setattr(t[1], "speaker", t[0].speaker)) == "wrong speaker for beat"
    assert broken(lambda t: setattr(t[0], "text", t[0].text + f" 아님 {new_time}?")) == "said before its beat"
    assert broken(lambda t: setattr(t[-1], "text", t[-1].text + " 나 밤 11시 반까지 학원")) == "unscripted time"
    assert broken(lambda t: setattr(t[1], "text", t[1].text + " 확정")) == "says 확정"
    assert broken(lambda t: t.insert(1, Turn(speaker=t[3].speaker, text="ㅇㅋ", beat=0))) in ("small talk not asked for", "small talk touches the plan")
    assert broken(lambda t: setattr(t[0], "text", t[0].text.replace(sc["beats"][0]["must"][0], "그날"))) in ("missing expression", "date not resolved")
    ins = next(s for s in (S.make(rng, "instruction") for _ in range(50)) if s["events"])
    it = [Turn(**t) for t in _mechanical(ins)]
    assert check(ins, it) is None and check(ins, [it[0].model_copy(update={"text": it[0].text[5:]})]) == "no [요청] marker"
    assert check(sc, good[:1] + [Turn(speaker=good[1].speaker, text="[요청] 8시로", beat=2)] + good[2:]) == "[요청] marker in a chat"

    # 3. agrees(): the prefix gold "proposed" must not pass a "confirmed" answer, and vice versa
    k = next(k for k, g in enumerate(sc["golds"], 1) if any(e["status"] == "proposed" for e in g))
    r = to_record(sc, good, next(i for i, t in enumerate(good) if t.beat > k), k)
    early = [e | {"status": "confirmed"} if e["status"] == "proposed" else e for e in r["events"]]
    assert agrees(r, {"events": r["events"]}) and not agrees(r, {"events": early})
    gone = [e | {"absent": e["attendees"][:1], "attendees": e["attendees"][1:]} for e in r["events"]]
    assert not agrees(r, {"events": gone})  # an attendee moved to absent
    assert not agrees(r, {"events": r["events"] + r["events"][-1:]})  # one event said twice
    assert agrees(r, {"events": [e | {"attendees": []} for e in r["events"]]})  # empty = everyone

    # 4. dump → judge-prep → finish in a temp folder
    def run(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(list(argv))
        return buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        run("dump", "--n", "60", "--seed", "2", "--dir", tmp, "--batches", "3")
        folder = Path(tmp)
        scs = {r["id"]: r["scenario"] for r in read_jsonl(folder / "private" / "scenarios.jsonl")}
        assert collections.Counter(s["type"] for s in scs.values()) == collections.Counter(
            {k: v for k, v in _quotas(60).items() if v})
        packs = sorted((folder / "writers").iterdir())
        jobs = [r for p in packs for r in read_jsonl(p / "input.jsonl")]
        assert sorted(j["id"] for j in jobs) == sorted(scs)
        ids = list(scs)
        bad = ids[0]
        for p in packs:
            answers = [{"id": j["id"], "turns": _mechanical(scs[j["id"]])} for j in read_jsonl(p / "input.jsonl")]
            for a in answers:
                if a["id"] == bad:
                    a["turns"] = a["turns"][::-1] if len(a["turns"]) > 1 else [{"speaker": "누구", "text": "x", "beat": 1}]
            write_jsonl(p / "output.jsonl", answers)
        report = run("check", "--dir", tmp)
        flagged = [r["id"] for p in packs for r in read_jsonl(p / "rejected.jsonl")]
        assert flagged == [bad], (flagged, report)
        report = run("judge-prep", "--dir", tmp, "--batches", "4")
        cands = read_jsonl(folder / "private" / "candidates.jsonl")
        assert bad not in {c["chat"] for c in cands} and len({c["chat"] for c in cands}) == len(scs) - 1, report
        jpacks = sorted((folder / "judges").iterdir())
        where = {r["id"]: p for p in jpacks for r in read_jsonl(p / "input.jsonl")}
        assert set(where) == {c["id"] for c in cands} and not set(where) & set(scs)
        for chat in {c["chat"] for c in cands}:  # one chat's rows never share a judge
            mine = [where[c["id"]] for c in cands if c["chat"] == chat]
            assert len(mine) == len(set(mine)), chat
        by = {c["id"]: c for c in cands}
        prem = next(c for c in cands if c["part"] != "full" and any(e["status"] == "proposed" for e in c["record"]["events"]))
        wrong_full = next(c for c in cands if c["part"] == "full" and c["chat"] != prem["chat"] and c["record"]["events"])
        for p in jpacks:
            answers = []
            for j in read_jsonl(p / "input.jsonl"):
                ev = by[j["id"]]["record"]["events"]
                if j["id"] == prem["id"]:  # premature confirmation in a prefix
                    ev = [e | {"status": "confirmed"} for e in ev if e["status"] != "rejected"]
                if j["id"] == wrong_full["id"]:
                    ev = []
                answers.append({"id": j["id"], "events": ev})
            write_jsonl(p / "output.jsonl", answers)
        out = folder / "kept.jsonl"
        report = run("finish", "--dir", tmp, "--out", str(out))
        kept = read_jsonl(out)
        chats = {r["chat"] for r in kept}
        assert wrong_full["chat"] not in chats and prem["chat"] in chats, report
        assert not [r for r in kept if r["chat"] == prem["chat"] and r["snap"] != "full"], report
        want = sum(1 for c in cands if c["chat"] not in (wrong_full["chat"], prem["chat"])) + 1
        assert len(kept) == want, (len(kept), want, report)
        assert all(build_train.valid(r) for r in kept)
        again = run("finish", "--dir", tmp, "--out", str(out))
        assert len(read_jsonl(out)) == len(kept), again
    print(f"v5 ok: {sum(per.values())} prefix rows checked across families {dict(per)}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--n", type=int, required=True)
    d.add_argument("--seed", type=int, required=True)
    c = sub.add_parser("check")
    j = sub.add_parser("judge-prep")
    f = sub.add_parser("finish")
    for p in (d, c, j, f):
        p.add_argument("--dir", required=True)
    for p in (d, j):
        p.add_argument("--batches", type=int, default=4)
        p.add_argument("--brief", help="folder with WRITER.md / JUDGE.md, copied into each pack")
    j.add_argument("--answers", default="writers/*/output.jsonl")
    f.add_argument("--answers", default="judges/*/output.jsonl")
    f.add_argument("--out", required=True, help="kept rows are appended here")
    sub.add_parser("demo")
    args = ap.parse_args(argv)
    {"dump": dump, "check": check_packs, "judge-prep": judge_prep, "finish": finish, "demo": lambda _: demo()}[args.step](args)


if __name__ == "__main__":
    main()
