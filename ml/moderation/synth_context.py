"""Synthetic chat-context data for the profanity model (plan 9.8).

    uv run python ml/moderation/synth_context.py --n 20 --out data/moderation/raw/_try/context.jsonl

Samples go in a subfolder: build_train.py trains on every raw/*.jsonl.

The scenario (and so the label) is decided in code first; Codex only writes the
chat. Each output line: {"text": message to judge, "context": [earlier messages,
oldest first, at most 3], "label": 0 | 1}. The label is about text only.
Output is appended (gitignored); use a new --seed when adding to a file.

Needs a logged-in Codex CLI. The writer never sees the repo, so test sets C/D
cannot leak into the prompt. Don't paste test sentences into this file either.

Kinds:
  pair     the same text after two different chats: profanity (1) and innocent (0)
  profane  a profane message in a chat (1)
  reply    a clean reply right after someone else swore (0)
  clean    everyday chat with no profanity (0)
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path[:0] = ["ml/common"]
from codex_json import ask_all

KINDS = {"pair": 4, "profane": 2, "reply": 2, "clean": 2}  # kind → weight
TARGETS = ["시발", "씨발", "병신", "새끼", "개새끼", "지랄", "존나", "좆같다", "미친놈", "니애미", "창년", "엿먹어", "썅"]  # fmt: skip
STYLES = [
    "글자 그대로",
    "초성만 (예: ㅅㅂ)",
    "글자 사이에 숫자나 기호 넣기 (예: 시1발)",
    "자모를 풀어 쓰기 (예: ㅅㅣ발)",
    "소리 나는 대로 살짝 바꾸기 (예: 쉬발)",
    "한글 자판 그대로 영어로 치기 (예: tlqkf)",
    "글자 사이 띄어쓰기 (예: 시 발)",
    "길게 늘이기 (예: 시이이발)",
]
TOPICS = ["역할 분담", "발표 자료", "마감 시간", "회의 시간 정하기", "자료 조사", "수행평가", "시험 범위", "급식이나 매점 잡담", "게임 잡담", "늦게 온 팀원"]  # fmt: skip

# Why the same words can be innocent. Varied so the model can't learn one shortcut.
SENSES = ["사람 별명이나 캐릭터 이름", "과목이나 교과서에 나오는 용어", "게임 용어나 아이템 이름",
          "음식이나 물건 이름을 줄인 말", "동아리나 조 이름을 줄인 말", "파일명이나 과제 코드",
          "오타가 난 다른 단어 (앞 대화를 보면 뭘 치려던 건지 보임)", "외국어나 고유명사를 한글로 적은 말",
          "평범한 단어들이 우연히 붙어서 그렇게 보이는 것"]  # fmt: skip
FORMS = ["초성만", "두세 글자 단어 하나", "띄어쓰기 없이 붙인 짧은 말", "평범한 단어가 들어간 짧은 문장",
         "소리가 비슷한 다른 단어가 들어간 짧은 문장"]  # fmt: skip

COMMON = """너는 욕설 필터 AI의 학습 데이터를 만드는 작가야. 고등학생 팀플 단톡의 한 장면을 써.
규칙:
- 주제: {topic}
- 말투: 줄임말, 오타, 짧은 문장. 메시지 하나는 40자 이내
- 사람 이름, 학교 이름, 연락처 같은 실제 인물 정보는 쓰지 마. 부를 땐 "너", "조장" 같은 말만 써
- 욕 없이 돌려 말하는 비꼼이나 암시는 쓰지 마. 욕이면 읽었을 때 특정 욕으로 분명히 복원돼야 해
- context는 text 바로 앞에 온 메시지들이고 오래된 순서야
"""

PROMPTS = {
    "pair": COMMON
    + """만들 것: 메시지 text 하나와, 그 앞에 올 수 있는 대화 두 가지.
- text 형식: {form}. "{target}"(이)라는 욕으로도, 글자나 소리만 닮은 멀쩡한 말로도 읽혀야 해
- profane_context (1~3개): 이 대화 뒤에 text가 오면, text는 상대에게 "{target}"(이)라고 욕한 거야
- clean_context (1~3개): 이 대화 뒤에 text가 오면, text는 욕이 아니라 멀쩡한 말이야. 멀쩡한 이유: {sense}
- text는 두 경우에 글자 하나 다르지 않게 똑같아
- context에는 욕도, '욕', '초성', '퀴즈', '자음', '모음'이라는 말도 넣지 마. 다음 말이 욕이라고 예고하지 마""",
    "profane": COMMON
    + """만들 것: context {n}개와 text.
- text에는 "{target}"(이)라는 욕이 들어가. 표기 방식: {style}
- context는 욕 없는 평범한 대화고, '욕'이라는 글자를 쓰지 마""",
    "reply": COMMON
    + """만들 것: context {n}개와 text.
- context의 마지막 메시지에는 "{target}"(이)라는 욕이 들어가. 표기 방식: {style}
- text는 그 욕에 대한 다른 사람의 답장인데, 욕이 전혀 없는 멀쩡한 말이야 (말리기, 사과 요구, 화제 돌리기, 그냥 할 말 하기 등). text에 '욕'이라는 글자를 쓰지 마""",
    "clean": COMMON
    + """만들 것: context {n}개와 text.
- 욕이 전혀 없는 평범한 대화야{lookalike}""",
}

STRINGS = {"type": "array", "items": {"type": "string"}}
SCHEMAS = {
    kind: {
        "type": "object",
        "additionalProperties": False,
        "required": keys,
        "properties": {k: {"type": "string"} if k == "text" else STRINGS for k in keys},
    }
    for kind, keys in {
        "pair": ["text", "profane_context", "clean_context"],
        "profane": ["text", "context"],
        "reply": ["text", "context"],
        "clean": ["text", "context"],
    }.items()
}


def make(rng: random.Random) -> dict:
    kind = rng.choices(list(KINDS), weights=KINDS.values())[0]
    return {
        "kind": kind,
        "topic": rng.choice(TOPICS),
        "target": rng.choice(TARGETS),
        "style": rng.choice(STYLES),
        "sense": rng.choice(SENSES),
        "form": rng.choice(FORMS),
        "n": rng.randint(1, 3) if kind != "clean" else rng.randint(0, 3),
        "lookalike": rng.choice(["", "", ". 욕과 글자만 닮은 멀쩡한 단어를 하나 자연스럽게 넣어"]),
    }


def to_prompt(sc: dict) -> str:
    return PROMPTS[sc["kind"]].format(**sc)


def gives_it_away(kind: str, text: str, context: list[str]) -> bool:
    """True when a word alone would reveal the label, instead of the conversation."""
    said = " ".join(context)
    if kind == "pair":
        return bool(re.search("초성|퀴즈|자음|모음|욕", said))
    if kind == "profane":
        return "욕" in said
    return kind == "reply" and "욕" in text


def to_rows(sc: dict, a: dict) -> list[dict]:
    """Answer → labeled rows, or [] when it doesn't fit the scenario."""
    text = a["text"].strip()
    if sc["kind"] == "pair":
        pairs = [(a["profane_context"], 1), (a["clean_context"], 0)]
        if a["profane_context"] == a["clean_context"]:
            return []
    else:
        pairs = [(a["context"], 1 if sc["kind"] == "profane" else 0)]
    rows = []
    for context, label in pairs:
        context = [c.strip() for c in context if c.strip()]
        low = 0 if sc["kind"] == "clean" else 1
        if not text or text in context or not low <= len(context) <= 3:
            return []
        if gives_it_away(sc["kind"], text, context):
            return []
        rows.append({"text": text, "context": context, "label": label})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="scenarios (a pair makes 2 rows)")
    ap.add_argument("--out", default="data/moderation/raw/context_synth.jsonl")
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    todo = [make(rng) for _ in range(args.n)]
    kept = dropped = tokens_in = tokens_out = 0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as f:
        # One schema per kind, so ask kind by kind.
        for kind in KINDS:
            batch = [sc for sc in todo if sc["kind"] == kind]
            for sc, answer, usage in ask_all(args.model, batch, to_prompt, SCHEMAS[kind], args.workers):
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
                rows = to_rows(sc, answer) if answer else []
                dropped += not rows
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    kept += 1
                f.flush()
    print(f"kept {kept} rows, dropped {dropped} scenarios, tokens in {tokens_in} out {tokens_out} → {args.out}")
    if ask_all.stopped:
        sys.exit("stopped after 10 failures in a row")


def demo():
    sc = {"kind": "pair"}
    a = {"text": "ㅅㅂ", "profane_context": ["또 늦냐"], "clean_context": ["수박 줄이면 뭐임", "과일임"]}
    assert to_rows(sc, a) == [
        {"text": "ㅅㅂ", "context": ["또 늦냐"], "label": 1},
        {"text": "ㅅㅂ", "context": ["수박 줄이면 뭐임", "과일임"], "label": 0},
    ]
    assert to_rows(sc, a | {"clean_context": ["또 늦냐"]}) == []  # same chat can't give two labels
    assert to_rows({"kind": "reply"}, {"text": "그만해", "context": ["a", "b", "c", "d"]}) == []  # too long
    assert to_rows({"kind": "reply"}, {"text": "그만해", "context": []}) == []  # a reply needs a message before it
    assert to_rows({"kind": "clean"}, {"text": "ㅇㅋ", "context": []}) == [{"text": "ㅇㅋ", "context": [], "label": 0}]
    assert to_rows({"kind": "profane"}, {"text": "시1발", "context": ["ㅇㅇ"]})[0]["label"] == 1
    assert to_rows(sc, a | {"clean_context": ["초성 퀴즈 ㄱ"]}) == []  # the word reveals the label
    assert to_rows(sc, a | {"profane_context": ["화나면 나한테 욕해"]}) == []
    assert to_rows({"kind": "reply"}, {"text": "욕 그만해", "context": ["시1발"]}) == []
    assert to_rows({"kind": "reply"}, {"text": "일단 밥 먹자", "context": ["시1발"]})[0]["label"] == 0
    rng = random.Random(0)
    for _ in range(200):
        sc = make(rng)
        assert "{" not in to_prompt(sc)  # every placeholder filled
    print("ok")


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["--demo"] else main()
