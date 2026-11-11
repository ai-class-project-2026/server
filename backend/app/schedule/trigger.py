"""Step ① of plan 8.4: skip the LLM when nobody is talking about a time."""

import re

_PATTERN = re.compile(
    r"\d+\s*시|\d+\s*[/월]\s*\d+|[월화수목금토일](요일|욜)?\b|오늘|내일|낼|모레|글피|"
    r"주말|다음\s*주|담주|이번\s*주|약속|회의|만나|모이|보자|취소|미루|확정|ㅇㅋ|콜"
)


def should_run(texts: list[str]) -> bool:
    return any(_PATTERN.search(t) for t in texts)
