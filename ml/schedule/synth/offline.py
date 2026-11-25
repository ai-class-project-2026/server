"""generate.py without the Codex CLI: the writer and the judge are agents that work from
batch files. Same writer prompt (to_prompt), checks (check), records (to_record), judge
prompt (judge_prompt) and agreement test (agrees) as generate.py.

    python ml/schedule/synth/offline.py dump --n 1000 --seed 7 --dir GEN
        → GEN/private/scenarios.jsonl  {id, scenario}  (has the gold)
          GEN/writer_K.jsonl           {id, prompt}
    writers answer each writer_K.jsonl in GEN/writer_K.out.jsonl: {id, turns: [{speaker, text}]}
    python ml/schedule/synth/offline.py judge-prep --dir GEN
        → GEN/private/candidates.jsonl {id, writer_id, record}
          GEN/judge_K.jsonl            {id, prompt}  (the serving prompt only, never the gold)
    judges answer each judge_K.jsonl in GEN/judge_K.out.jsonl: {id, events: [...]}
    python ml/schedule/synth/offline.py finish --dir GEN --out GEN/private/train_v4.jsonl

judge-prep runs generate.check() with one relaxation (check() below: a gold date that only
the calendar has, for a title the chat says) and three more filters: missing cues, a chosen
constraint time not said on its own (missing_apart), and clock times the scenario never had.
--kind focus2 makes the second focus batch (scenarios.make_focus2).

Writers and judges must never read GEN/private/. Ids are random, so they say nothing about
the scenario, and judge ids are not derived from writer ids (a judge can't find the
writer's prompt, which describes the intended outcome). Each finished judge id is logged in
private/finished.txt so running finish twice doesn't add a chat twice.
"""

import argparse
import collections
import datetime as dt
import json
import random
import secrets
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent)]
import generate  # noqa: E402  (sets up the backend/ and ml/common import paths)
import scenarios  # noqa: E402
from app.schemas import ScheduleEvent  # noqa: E402
from dateparse_ko import parse  # noqa: E402

MAKERS = {"focus": scenarios.make_focus, "focus2": scenarios.make_focus2, "short": scenarios.make_short,
          "make": scenarios.make}  # fmt: skip


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _ids(n: int, taken: set) -> list[str]:
    out = []
    while len(out) < n:
        i = secrets.token_hex(5)
        if i not in taken:
            taken.add(i)
            out.append(i)
    return out


def _split(rows: list, k: int) -> list[list]:
    size = -(-len(rows) // k) if rows else 0
    return [rows[i * size : (i + 1) * size] for i in range(k)]


def _refuse_overwrite(paths: list[Path]) -> None:
    if there := [str(p) for p in paths if p.exists()]:
        sys.exit(f"already there, not overwriting (use another --dir): {there}")


def _answers(folder: Path, pattern: str) -> tuple[list[dict], collections.Counter]:
    """Every answer line in folder/pattern, and how many lines could not be read."""
    rows, bad = [], collections.Counter()
    for path in sorted(folder.glob(pattern)):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad["not json"] += 1
                print(f"  {path.name}:{n} is not JSON", file=sys.stderr)
                continue
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                bad["no id"] += 1
                continue
            rows.append(row)
    return rows, bad


def check(sc: dict, turns) -> str | None:
    """generate.check(), except that a gold date nobody says is accepted when the calendar has
    that event (same date and title) and the chat says the title: "발표 연습 3시로 늦추자" with
    the date only on the calendar. Not when the chat names some other date instead."""
    reason = generate.check(sc, turns)
    if reason != "date not resolved":  # check() tests the date last, so everything else passed
        return reason
    ref = dt.date.fromisoformat(sc["ref_date"])
    said = "".join(t.text for t in turns).replace(" ", "")
    mentions = [m for t in turns for m in parse(t.text, ref) if m.date]
    found = {m.date.isoformat() for m in mentions}
    calendar = {(e["date"], e["title"]) for e in sc["existing_events"]}
    for e in sc["events"]:
        if e["date"] in found:
            continue
        if (e["date"], e["title"]) not in calendar or e["title"].replace(" ", "") not in said:
            return reason
    known = {e["date"] for e in sc["events"] + sc["existing_events"]}
    if any(m.date.isoformat() not in known for m in mentions if not m.tentative):  # "목?" may be 목 아파
        return reason
    return None


def missing_apart(sc: dict, turns) -> bool:
    """`apart` words (a chosen constraint time) must also be said outside the cue phrases:
    in "6시부터 됨" / "그럼 6시" the second 6시 is the proposal, and check() alone would be
    satisfied by the first."""
    said = "".join(t.text for t in turns).replace(" ", "")
    for alts in sc.get("cues", []):
        for a in alts:
            said = said.replace(a.replace(" ", ""), "")
    return any(w.replace(" ", "") not in said for w in sc.get("apart", []))


def missing_cue(sc: dict, turns) -> bool:
    """make_focus() cues: phrases that must be in the chat but are not event dates or times
    (a hedge like "ㅇㅇ 나도 봐서", "7시까지 학원", "늦추자"). Each cue lists alternatives."""
    said = "".join(t.text for t in turns).replace(" ", "")
    return any(not any(a.replace(" ", "") in said for a in alts) for alts in sc.get("cues", []))


def unscripted_time(sc: dict, turns) -> bool:
    """The chat names a clock time the scenario doesn't have (not an event, calendar or
    constraint time), e.g. "학원 6시에 끝나서" added to a hedge chat. check() would not see it
    and it can change what the chat means."""
    ref = dt.date.fromisoformat(sc["ref_date"])
    allowed = {e["start"][:5] for e in sc["events"] + sc["existing_events"] if e["start"]}
    allowed |= {t for alts in sc.get("cues", []) for a in alts for t in scenarios._times_in(a, ref)}
    return any(m.time.strftime("%H:%M") not in allowed for t in turns for m in parse(t.text, ref) if m.time)


def keeps_moved_time(record: dict, answer: dict) -> bool:
    """agrees() ignores a confirmed answer at a time already on the calendar, so "old time
    kept + new time added" passes it the same as "moved". When the gold drops a calendar
    time (rejected), the judge must not call that time confirmed."""
    on_calendar = {(e["date"], (e["start"] or "")[:5]) for e in record["existing_events"]}
    dropped = {
        (e["date"], (e["start"] or "")[:5]) for e in record["events"] if e["status"] == "rejected"
    } & on_calendar
    return any(
        e["status"] == "confirmed" and (e["date"], (e.get("start") or "")[:5]) in dropped
        for e in answer["events"]
    )


def dump(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    writer_files = [folder / f"writer_{k}.jsonl" for k in range(1, args.batches + 1)]
    _refuse_overwrite([private / "scenarios.jsonl", *writer_files])
    private.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    todo = [MAKERS[args.kind](rng) for _ in range(args.n)]
    ids = _ids(len(todo), set())
    write_jsonl(private / "scenarios.jsonl", ({"id": i, "scenario": sc} for i, sc in zip(ids, todo)))
    order = list(zip(ids, todo))
    random.Random(secrets.randbits(64)).shuffle(order)  # file position says nothing about the type
    for path, part in zip(writer_files, _split(order, args.batches)):
        write_jsonl(path, ({"id": i, "prompt": generate.to_prompt(sc)} for i, sc in part))
    kinds = collections.Counter(sc["type"] for sc in todo)
    print(f"{len(todo)} scenarios (seed {args.seed}, {args.kind}) → {private / 'scenarios.jsonl'}")
    print("types", dict(kinds.most_common()))
    print("writer batches", ", ".join(f"{p.name} ({len(read_jsonl(p))})" for p in writer_files))


def judge_prep(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    judge_files = [folder / f"judge_{k}.jsonl" for k in range(1, args.batches + 1)]
    _refuse_overwrite([private / "candidates.jsonl", *judge_files, *folder.glob("judge_*.out.jsonl")])
    todo = {r["id"]: r["scenario"] for r in read_jsonl(private / "scenarios.jsonl")}
    rows, why = _answers(folder, args.answers)
    why_by_type = collections.Counter()
    done, candidates = set(), []
    for row in rows:
        sc = todo.get(row["id"])
        if sc is None:
            why["unknown id"] += 1
            continue
        if row["id"] in done:
            why["second answer for an id (first kept)"] += 1
            continue
        done.add(row["id"])
        try:
            turns = generate.Dialogue.model_validate({"turns": row.get("turns")}).turns
        except ValueError:
            reason = "not the turns shape"
        else:
            reason = (
                check(sc, turns)
                or ("missing cue" if missing_cue(sc, turns) else None)
                or ("constraint time not proposed on its own" if missing_apart(sc, turns) else None)
                or ("unscripted time" if unscripted_time(sc, turns) else None)
            )
        if reason:
            why[reason] += 1
            why_by_type[(sc["type"], reason)] += 1
            continue
        candidates.append((row["id"], generate.to_record(sc, turns)))
    unanswered = len(todo) - len(done)
    judge_ids = _ids(len(candidates), set(todo))
    write_jsonl(
        private / "candidates.jsonl",
        ({"id": j, "writer_id": w, "record": r} for j, (w, r) in zip(judge_ids, candidates)),
    )
    order = list(zip(judge_ids, candidates))
    random.Random(secrets.randbits(64)).shuffle(order)
    for path, part in zip(judge_files, _split(order, args.batches)):
        write_jsonl(path, ({"id": j, "prompt": generate.judge_prompt((None, r))} for j, (_, r) in part))
    print(f"{len(rows)} writer answers for {len(todo)} scenarios; {unanswered} scenarios have no answer")
    print(f"{len(candidates)} chats pass check() → {', '.join(p.name for p in judge_files)}")
    print("dropped", dict(why.most_common()))
    for (kind, reason), n in sorted(why_by_type.items()):
        print(f"  {kind:>16}  {reason}: {n}")


def finish(args) -> None:
    folder = Path(args.dir)
    private = folder / "private"
    candidates = {r["id"]: r for r in read_jsonl(private / "candidates.jsonl")}
    ledger = private / "finished.txt"
    finished = set(ledger.read_text(encoding="utf-8").split()) if ledger.exists() else set()
    rows, why = _answers(folder, args.answers)
    kept, dropped, seen, out = collections.Counter(), collections.Counter(), set(), []
    for row in rows:
        c = candidates.get(row["id"])
        if c is None:
            why["unknown id"] += 1
            continue
        if row["id"] in seen or row["id"] in finished:
            why["already finished" if row["id"] in finished else "second answer for an id (first kept)"] += 1
            continue
        seen.add(row["id"])
        record, kind = c["record"], c["record"]["type"]
        try:  # the serving output shape; "19:00:00" and "19:00" both become 19:00:00
            events = [ScheduleEvent.model_validate(e).model_dump(mode="json") for e in row["events"]]
        except (KeyError, TypeError, ValueError):
            why["not the events shape"] += 1
            dropped[(kind, "not the events shape")] += 1
            continue
        answer = {"events": events}
        if not generate.agrees(record, answer):
            dropped[(kind, "judge disagrees")] += 1
        elif keeps_moved_time(record, answer):
            dropped[(kind, "judge keeps the moved time")] += 1
        else:
            kept[kind] += 1
            out.append((row["id"], record))
    with open(args.out, "a", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for _, r in out)
    with open(ledger, "a", encoding="utf-8") as f:
        f.writelines(f"{j}\n" for j, _ in out)
    unjudged = len(set(candidates) - seen - finished)
    print(f"kept {sum(kept.values())} → {args.out} (appended); {unjudged} candidates have no judge answer")
    if why:
        print("unusable answer lines", dict(why))
    types = sorted(set(kept) | {k for k, _ in dropped})
    print(f"{'type':>16}  kept  dropped")
    for kind in types:
        lost = {r: n for (k, r), n in dropped.items() if k == kind}
        print(f"{kind:>16}  {kept[kind]:>4}  {sum(lost.values()):>4}  {lost if lost else ''}")


def demo() -> None:
    """dump → judge-prep → finish in a temp folder, with mechanical writer and judge
    answers: good ones, and broken ones that each step must drop."""
    import contextlib
    import io
    import tempfile

    def run(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(list(argv))
        return buf.getvalue()

    def chat(sc, drop_must=False, speaker=None):
        words = sc["must"][1:] if drop_must else list(sc["must"])
        words += [alts[0] for alts in sc["cues"]]
        n = max(sc["min_turns"], min(len(words), sc["max_turns"]))
        texts = [" / ".join(words[i::n]) or "ㅇㅋ" for i in range(n)]
        people = sc["participants"]
        return [{"speaker": speaker or people[i % len(people)], "text": t} for i, t in enumerate(texts)]

    with tempfile.TemporaryDirectory() as tmp:
        gen = Path(tmp)
        run("dump", "--n", "40", "--seed", "3", "--dir", tmp, "--batches", "2")
        scs = {r["id"]: r["scenario"] for r in read_jsonl(gen / "private" / "scenarios.jsonl")}
        batch = read_jsonl(gen / "writer_1.jsonl") + read_jsonl(gen / "writer_2.jsonl")
        assert sorted(r["id"] for r in batch) == sorted(scs) and all(set(r) == {"id", "prompt"} for r in batch)
        assert all(sc["type"] not in r["id"] for r in batch for sc in scs.values())
        ids = list(scs)
        bad_must = next(i for i in ids if scs[i]["must"])
        bad_who = next(i for i in ids if i != bad_must)
        bad_cue = next(i for i in ids if scs[i]["cues"] and i not in (bad_must, bad_who))
        skipped = next(i for i in ids if i not in (bad_must, bad_who, bad_cue))
        bad_time = next(i for i in ids if i not in (bad_must, bad_who, bad_cue, skipped))
        answers = []
        for i, sc in scs.items():
            if i == skipped:
                continue
            turns = chat(sc, drop_must=i == bad_must, speaker="누구" if i == bad_who else None)
            if i == bad_cue:
                turns = chat(sc | {"cues": []})
            if i == bad_time:  # a time the scenario never had
                turns[-1]["text"] += " 나 밤 11시 반까지 학원"
            answers.append({"id": i, "turns": turns})
        answers.append({"id": "nope", "turns": []})
        write_jsonl(gen / "writer_1.out.jsonl", answers[:20])
        write_jsonl(gen / "writer_2.out.jsonl", answers[20:])
        with open(gen / "writer_2.out.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{broken\n")
        report = run("judge-prep", "--dir", tmp, "--batches", "2")
        cands = read_jsonl(gen / "private" / "candidates.jsonl")
        assert "missing expression" in report and "unknown speaker" in report and "missing cue" in report, report
        assert "unknown id" in report and "not json" in report and "1 scenarios have no answer" in report, report
        assert "unscripted time" in report, report
        assert {c["writer_id"] for c in cands} == set(scs) - {bad_must, bad_who, bad_cue, skipped, bad_time}, report
        jobs = read_jsonl(gen / "judge_1.jsonl") + read_jsonl(gen / "judge_2.jsonl")
        assert sorted(j["id"] for j in jobs) == sorted(c["id"] for c in cands)
        assert not {j["id"] for j in jobs} & set(scs)  # judge ids are not writer ids
        for j in jobs:  # the judge sees the serving prompt, not the gold or the writer's flow
            c = next(c for c in cands if c["id"] == j["id"])
            blind = {k: v for k, v in c["record"].items() if k not in ("events", "type")}
            assert j["prompt"] == generate.judge_prompt((None, blind)) and "이 순서대로" not in j["prompt"]
        by_id = {c["id"]: c for c in cands}
        wrong = next(c["id"] for c in cands if c["record"]["type"] == "hedge")
        moved = next((c["id"] for c in cands if c["id"] != wrong and keeps_moved_time(
            c["record"], {"events": c["record"]["existing_events"] + c["record"]["events"]})), None)  # fmt: skip
        judged = []
        for j, c in by_id.items():
            events = c["record"]["events"]
            if j == wrong:  # the hedge read as a yes (and a yes as a maybe)
                flip = {"confirmed": "proposed"}
                events = [e | {"status": flip.get(e["status"], "confirmed")} for e in events]
            if j == moved:  # moved time reported as still on, next to the new time
                events = [e | {"status": "confirmed"} for e in c["record"]["existing_events"]] + events
            judged.append({"id": j, "events": events})
        judged.append({"id": judged[0]["id"], "events": []})  # a second answer is ignored
        write_jsonl(gen / "judge_1.out.jsonl", judged)
        out = gen / "private" / "kept.jsonl"
        report = run("finish", "--dir", tmp, "--out", str(out))
        kept = read_jsonl(out)
        lost = 1 + (moved is not None)
        assert len(kept) == len(cands) - lost, report
        assert "judge disagrees" in report and (moved is None or "keeps the moved time" in report), report
        assert by_id[wrong]["record"] not in kept
        again = run("finish", "--dir", tmp, "--out", str(out))
        assert len(read_jsonl(out)) == len(kept) and "already finished" in again, again
        print(f"offline ok: {len(scs)} scenarios, {len(cands)} pass check(), {len(kept)} kept"
              f"{', moved-time gate tested' if moved else ''}")  # fmt: skip

    # make_focus2(): the calendar date for a title-only change, and a chosen constraint time
    import copy

    rng, picked = random.Random(5), {}
    while len(picked) < 4:
        sc = scenarios.make_focus2(rng)
        key = sc.get("refer") or sc["variant"]
        if key == "chosen":  # is the chosen time written inside the constraint phrase ("5시" in "5시부터 됨")?
            key = "chosen_inside" if sc["apart"][0].replace(" ", "") in sc["cues"][0][0].replace(" ", "") else key
        if key in ("title", "title_weekday", "chosen", "chosen_inside") and key not in picked:
            picked[key] = sc

    def turns_of(sc, extra=""):
        rows = chat(sc)
        rows[-1]["text"] += extra
        return [generate.Turn(**r) for r in rows]

    t = picked["title"]
    ok = turns_of(t)
    assert generate.check(t, ok) == "date not resolved" and check(t, ok) is None  # only the calendar has the date
    away = dt.date.fromisoformat(t["ref_date"]) + dt.timedelta(days=20)  # past every gold and calendar date
    assert check(t, turns_of(t, f" {away.month}/{away.day}")) == "date not resolved"  # names another date
    other = copy.deepcopy(t)
    for e in other["existing_events"]:
        e["title"] = "다른 일정"
    assert check(other, ok) == "date not resolved"  # the calendar has no event with that title
    title = t["events"][0]["title"]
    assert check(t, [generate.Turn(speaker=x.speaker, text=x.text.replace(title, "그거")) for x in ok]) == "missing expression"
    w = picked["title_weekday"]
    assert generate.check(w, turns_of(w)) is None and check(w, turns_of(w)) is None  # the weekday resolves itself
    for key in ("chosen_inside", "chosen"):
        c = picked[key]
        assert check(c, turns_of(c)) is None and not missing_apart(c, turns_of(c))
        cue_only = turns_of(c | {"must": [x for x in c["must"] if x not in c["apart"]]})  # time only in the cue
        if key == "chosen_inside":  # check() is fooled by the cue; missing_apart is not
            assert check(c, cue_only) is None and missing_apart(c, cue_only)
        else:  # said differently in the cue ("20시부터" / "밤 8시"): check() already sees it is missing
            assert check(c, cue_only) == "missing expression"
    print("offline focus2 ok: calendar date for a title-only change, chosen constraint time said on its own")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--n", type=int, required=True)
    d.add_argument("--seed", type=int, required=True)
    d.add_argument("--kind", choices=list(MAKERS), default="focus")
    j = sub.add_parser("judge-prep")
    f = sub.add_parser("finish")
    for p in (d, j, f):
        p.add_argument("--dir", required=True)
    for p in (d, j):
        p.add_argument("--batches", type=int, default=4)
    j.add_argument("--answers", default="writer_*.out.jsonl", help="writer answer files in --dir")
    f.add_argument("--answers", default="judge_*.out.jsonl", help="judge answer files in --dir")
    f.add_argument("--out", required=True, help="kept records are appended here")
    sub.add_parser("demo")
    args = ap.parse_args(argv)
    {"dump": dump, "judge-prep": judge_prep, "finish": finish, "demo": lambda _: demo()}[args.step](args)


if __name__ == "__main__":
    main()
