"""Public data + augmentation → data/moderation/train.jsonl (part 3 → part 1).

    uv run python ml/moderation/build_train.py

1. Convert each public dataset into data/moderation/raw/<name>.jsonl, one
   {"text": "...", "label": 0 | 1} per line (raw/ is gitignored, plan 16.6).
   Chat data adds "context": ["earlier message", ...], oldest first; the label
   is about text only. Write the source and license in data/README.md.
   UnSmile is eval-only.
2. Run this script: every sentence is kept as-is and augmented with the rules
   in ml/moderation/augment, test sets C/D are removed, the rest is shuffled.
   A row that is not {"text": str, "label": 0 | 1, "context": [str, ...]} is
   skipped, and its file and line number are printed.
3. Send the printed counts to part 1 with the PR.

Options: --fixes PATH changes raw labels first (one {"file", "line", "text",
"context", "old", "new"} per line), --hold-out PATH drops more sentences the
same way as the test sets (one {"text", "context"} per line), --out PATH.
"""

import argparse
import json
import os
import random
import sys
import unicodedata
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(Path(__file__).parent)]
os.environ.setdefault("JWT_SECRET", "eval-only-placeholder-secret-0000000")  # app config needs it
from app.moderation import candidate, dict_filter  # noqa: E402
from app.moderation.normalize import normalize  # noqa: E402
from augment import augment  # noqa: E402
from augment.similar_sound import NOT_BAD  # noqa: E402

RAW = Path("data/moderation/raw")
TESTS = [
    Path("data/moderation/test_C_human"),
    Path("data/moderation/test_D_fp"),
    Path("data/moderation/eval_only"),  # public test splits, UnSmile
]
OUT = Path("data/moderation/train.jsonl")
N_AUG = 3  # augmented copies per sentence


@cache
def norm(text: str) -> str:
    return normalize(text)[0]


@cache
def key(text: str) -> str:
    """한 문장의 '대표 철자'. 정규화한 뒤 공백을 모두 지워요.
    "병신", "병 신", "병@신"은 같은 key라서 같은 문장 가족으로 봐요.
    시험 문장 제외와 train/val 분할(group)이 모두 이 key를 써요."""
    return "".join(norm(text).split()) or "".join(text.split()) or text


@cache
def surface(text: str) -> str:
    """문장 가족을 알아보는 두 번째 이름. 서빙 정규화(합치고 줄이고 지우는)와 달리 글자를
    깎지 않아요: 유니코드 호환 변환(NFKC: "㈏" → "(나)", 전각 → 반각)을 하고, 공백과
    기호를 모두 지우고, 소문자로 맞춰요. "나도 뻐스타고 오는데"와
    "㈏도 뻐스타 고 오 는데"는 같은 surface예요."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if c.isalnum())


def read_jsonl(path: Path):
    """(줄 번호, 한 줄의 dict). 읽을 수 없는 줄은 파일과 줄 번호를 찍고 건너뛰어요."""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            print(f"skip {path}:{n}: not valid JSON", file=sys.stderr)
            continue
        yield n, row


def check_row(r) -> str | None:
    """형식이 틀리면 이유를, 맞으면 None을 돌려줘요."""
    if not isinstance(r, dict):
        return "row is not an object"
    if not isinstance(r.get("text"), str) or not r["text"].strip():
        return "text must be a non-empty string"
    if type(r.get("label")) is not int or r["label"] not in (0, 1):
        return "label must be 0 or 1"
    ctx = r.get("context", [])
    if not isinstance(ctx, list) or not all(isinstance(c, str) for c in ctx):
        return "context must be a list of strings"
    return None


@cache
def hit_words(text: str) -> Counter:
    """사전에 걸린 단어를 (단어, 종류)별로 센 것. 서빙(moderate)과 똑같이 기본 읽기
    (candidate.default_hits: 띄어쓰기를 지워서 생긴 단어는 ambiguous)와 후보 읽기를
    모두 봐요. 후보 읽기에서만 걸린 단어도 "ambiguous"로 세요."""
    hits = Counter((w, kind) for _, _, w, kind in candidate.default_hits(text))
    extra = Counter((w, "ambiguous") for _, _, w in candidate.hits(text))
    return hits | extra  # 같은 단어를 두 번 세지 않아요


def label_safe(source: str, variant: str, label: int) -> bool:
    """변형이 원문의 라벨을 그대로 물려받아도 되나? 규칙과 상관없이 결과만 봐요.
    label 0: 변형이 원문에 없던 욕을 단어 단위로 새로 만들면 안 돼요 (장녀 → 창녀).
             원문에 이미 있던 애매한 단어(새끼)는 다른 욕을 만드는 허락이 아니에요.
    label 1: 변형이 멀쩡한 단어가 되면 안 돼요 (NOT_BAD, 창녀 → 장녀)."""
    if label == 0:
        return not (hit_words(variant) - hit_words(source))
    s, v = key(source), key(variant)
    return not any(w in v and w not in s for w in NOT_BAD)


def drop_conflicts(rows: list[dict]) -> tuple[list[dict], int]:
    """맥락이 없는 문장이 같은 철자(key)로 0과 1 둘 다 달려 있으면 쓸 수 없어요.
    그 문장의 행을 모두 버려요. 맥락이 있는 행은 같은 글도 맥락에 따라 라벨이 다른 게
    정상이라서 건드리지 않아요. 남은 행과, 버린 철자 수를 돌려줘요."""
    labels = defaultdict(set)
    for r in rows:
        if not r["context"]:
            labels[key(r["text"])].add(r["label"])
    bad = {k for k, v in labels.items() if len(v) > 1}
    return [r for r in rows if r["context"] or key(r["text"]) not in bad], len(bad)


def apply_fixes(raw: list[dict], where: dict, path: Path) -> int:
    """라벨 고침 파일을 원문 행에 적용해요. 고침 하나는 (파일, 줄 번호)로 원문 행 하나를 가리키고,
    글과 context와 원래 라벨(old)이 그 행과 똑같아야 해요. 하나라도 안 맞으면 멈춰요."""
    done = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fx = json.loads(line)
        i = where.get((fx["file"], fx["line"]))
        r = raw[i] if i is not None else None
        if r is None or i in done or r["text"] != fx["text"] or r.get("context", []) != fx["context"]:
            sys.exit(f"{path}:{n}: fix does not match exactly one raw row")
        if r["label"] != fx["old"] or type(fx["new"]) is not int or fx["new"] not in (0, 1):
            sys.exit(f"{path}:{n}: raw label is {r['label']}, fix says {fx['old']} -> {fx['new']}")
        r["label"] = fx["new"]
        done.add(i)
    return len(done)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixes", type=Path, help="label overrides for raw rows (jsonl)")
    ap.add_argument("--hold-out", type=Path, help="more sentences to keep out, like the test sets (jsonl)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    rng = random.Random(0)
    # 시험 문장: 정확히 같은 글과, 철자만 다른 글(key가 같은 글) 둘 다 막아요.
    # --hold-out 문장도 시험 문장과 똑같이 막아요.
    held_exact, held_keys, held_surface = set(), set(), set()
    files = [f for d in TESTS for f in sorted(d.glob("*.jsonl"))] + ([args.hold_out] if args.hold_out else [])
    for f in files:
        for n, r in read_jsonl(f):
            if isinstance(r, dict) and isinstance(r.get("text"), str):
                held_exact.add(r["text"])
                held_keys.add(key(r["text"]))
                if surface(r["text"]):
                    held_surface.add(surface(r["text"]))
            else:
                print(f"skip {f}:{n}: no text", file=sys.stderr)

    def held_out(text: str) -> bool:
        return text in held_exact or key(text) in held_keys or surface(text) in held_surface

    raw, invalid, where = [], 0, {}
    for f in sorted(RAW.glob("*.jsonl")):
        for n, r in read_jsonl(f):
            why = check_row(r)
            if why:
                print(f"skip {f}:{n}: {why}", file=sys.stderr)
                invalid += 1
            else:
                where[f.name, n] = len(raw)
                raw.append(r)
    if args.fixes:  # 라벨 고침은 충돌 정리와 증강보다 먼저
        print(f"fixed labels of {apply_fixes(raw, where, args.fixes)} raw rows from {args.fixes}")
    # 원문이나 context가 시험 문장이면 증강하기 전에 레코드를 통째로 건너뛰어요.
    # 안 그러면 시험 문장의 변형이 학습 데이터에 들어가요.
    kept = [r for r in raw if not (held_out(r["text"]) or any(map(held_out, r.get("context", []))))]
    n_held_out = len(raw) - len(kept)
    for r in kept:
        r.setdefault("context", [])
    kept, conflicts = drop_conflicts(kept)

    # 문장 가족(union-find): key나 surface가 같은 원문은 처음부터 한 가족이에요. 변형도
    # 원문의 가족에 넣고, 변형이 다른 글과 같아지면 두 가족을 합쳐요. 그래야 한 가족이
    # train과 val로 갈리지 않아요. key는 서빙 정규화 기준이고, surface는 글자를 깎지
    # 않는 기준이에요 ("뻐스타고 오는데"와 "뻐스타 고 오 는데"는 정규화하면 달라져요).
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def link(text: str, other: str) -> None:
        """text의 이름들(key, surface)을 other의 가족에 합쳐요."""
        home = find(("k", key(other)))
        parent[find(("k", key(text)))] = home
        if surface(text):
            parent[find(("s", surface(text)))] = home

    for r in kept:
        link(r["text"], r["text"])
    rows, unsafe = {}, 0
    for r in kept:
        context = r.get("context", [])
        for text in [r["text"], *augment(r["text"], rng, N_AUG)]:
            if held_out(text):  # never train on the test sets
                continue
            if text != r["text"] and not label_safe(r["text"], text, r["label"]):
                unsafe += 1
                continue
            link(text, r["text"])
            # Same words in another context can have another label.
            rows[text, tuple(context)] = {"label": r["label"]}
    out = [{"text": t, "context": list(c), **v} for (t, c), v in rows.items()]
    out, conflicts2 = drop_conflicts(out)  # 변형끼리 부딪힌 것도 같은 기준으로 버려요
    ids = {}  # 가족의 대표 key → 0, 1, 2 ... 번호
    for r in out:
        r["group"] = ids.setdefault(find(("k", key(r["text"]))), len(ids))
    rng.shuffle(out)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8"
    )
    bad = sum(r["label"] for r in out)
    n_groups = len({r["group"] for r in out})
    print(
        f"{args.out}: {len(out)} rows from {n_groups} sentences "
        f"(profane {bad}, clean {len(out) - bad})"
    )
    print(
        f"skipped: {invalid} invalid rows, {n_held_out} held-out rows, "
        f"{unsafe} variants that would get a wrong label"
    )
    print(
        f"dropped sentences labeled both 0 and 1 (no context): {conflicts} in the raw data, "
        f"{conflicts2} more after augmenting"
    )


if __name__ == "__main__":
    main()
