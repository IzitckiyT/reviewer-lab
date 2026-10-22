#!/usr/bin/env python3
"""CI-only deterministic CLI stub. Never installed as a production provider.

The smoke job explicitly selects this file via REVIEWER_LAB_CODEX on an internal
Docker network. Every simulated inference is marked in its reason and journal.
"""
import json
import sys
import tempfile
from pathlib import Path

container_project = Path("/app/run.py").is_file()
sys.path.insert(0, "/app" if container_project else str(Path(__file__).resolve().parents[1]))

from lab.cases import CASES
from lab.oracle import evaluate

args = sys.argv[1:]
if args == ["login", "status"]:
    print("Logged in using ChatGPT (OFFLINE CI MOCK, no account)")
elif args == ["--version"]:
    print("codex-cli OFFLINE-CI-MOCK")
elif args == ["debug", "models", "--bundled"]:
    print(json.dumps({"models": [{"slug": "ci-reviewer"}, {"slug": "ci-attacker"}]}))
elif args and args[0] == "exec":
    payload = json.load(sys.stdin)
    role = "reviewer" if "candidate_source" in payload else "attacker"
    if role == "attacker":
        answer = {"source": CASES["owner"].seed}
    else:
        oracle = evaluate(CASES["owner"], payload["candidate_source"])
        answer = {"verdict": "PASS" if oracle["correct"] else "BLOCK",
                  "reason": "OFFLINE CI MOCK: deterministic oracle, no model request."}
    journal = Path.home() / "ci-mock-calls.jsonl" if container_project else Path(tempfile.gettempdir()) / "reviewer-lab-ci-mock-calls.jsonl"
    with journal.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"role": role, "model": args[args.index("--model") + 1]}) + "\n")
    print(json.dumps({"type": "item.completed", "item":
                      {"type": "agent_message", "text": json.dumps(answer)}}))
    print(json.dumps({"type": "turn.completed", "usage":
                      {"input_tokens": 1, "output_tokens": 1}}))
else:
    raise SystemExit("Unexpected CI mock invocation")
