"""Ask the Codex CLI for one JSON answer. Shared by the synthetic data scripts
(ml/schedule/synth/generate.py, ml/moderation/synth_context.py).

Needs a logged-in Codex CLI.
"""

import json
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# No tools, no user config, and an empty working folder: the writer sees only
# the prompt, so the real test sets can never reach it.
CODEX_ARGS = [
    "--ignore-user-config",
    "--ignore-rules",
    "--ephemeral",
    "--skip-git-repo-check",
    "--json",
    "-s", "read-only",
    "-c", 'model_reasoning_effort="high"',
    "-c", 'service_tier="priority"',  # Codex "fast mode"
    "-c", "suppress_unstable_features_warning=true",
    "-c", "project_doc_max_bytes=0",
    "-c", "include_permissions_instructions=false",
    "-c", "include_apps_instructions=false",
    "-c", "include_collaboration_mode_instructions=false",
    "-c", "include_environment_context=false",
    "-c", 'web_search="disabled"',
    "--enable", "skip_host_skill_discovery",
    "--enable", "omit_skill_descriptions",
    "--disable", "apps",
    "--disable", "plugins",
    "--disable", "multi_agent",
    "--disable", "shell_tool",
    "--disable", "tool_search",
    "--disable", "unified_exec",
]  # fmt: skip


def ask(model: str, prompt: str, schema: dict) -> tuple[dict, dict]:
    """One prompt → (answer matching `schema`, token usage)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
        (root / "instructions.md").write_text("요청받은 JSON만 출력한다. 도구를 쓰지 않는다.\n", encoding="utf-8")
        run = subprocess.run(
            [
                "codex", "exec", "-m", model, *CODEX_ARGS,
                "-c", f"model_instructions_file={json.dumps(str(root / 'instructions.md'))}",
                "-C", str(root / "work"),
                "--output-schema", str(root / "schema.json"),
                "-o", str(root / "out.json"),
                prompt,
            ],  # fmt: skip
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1200,  # Codex waits out rate limits itself, sometimes for 10 minutes
        )
        if run.returncode:
            raise RuntimeError(f"codex exit {run.returncode}: {run.stdout[-300:]}")
        answer = json.loads((root / "out.json").read_text(encoding="utf-8"))
    usage = {}
    for line in run.stdout.splitlines():
        if line.startswith('{"type":"turn.completed"'):
            usage = json.loads(line)["usage"]
    return answer, usage


def ask_all(model: str, items: list, to_prompt, schema: dict, workers: int = 4):
    """Yield (item, answer | None, usage) in order. Stops asking after 10 failures
    in a row (usually the quota is gone); `ask_all.stopped` tells the caller."""
    stop = threading.Event()
    failures = 0

    def one(item):
        nonlocal failures
        if stop.is_set():
            return item, None, {}
        try:
            answer, usage = ask(model, to_prompt(item), schema)
            failures = 0
            return item, answer, usage
        except (subprocess.SubprocessError, RuntimeError, ValueError, OSError) as e:
            print(f"skip: {str(e)[:300]}", file=sys.stderr)
            failures += 1
            if failures >= 10:
                stop.set()
        return item, None, {}

    with ThreadPoolExecutor(workers) as pool:
        yield from pool.map(one, items)
    ask_all.stopped = stop.is_set()
