"""Offline consistency audit. Reads snapshots as data; never executes saved source."""
import json
from pathlib import Path

from .cases import Case
from .oracle import evaluate
from .prompts import (ATTACKER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS,
                      attacker_prompt, reviewer_prompt, diff)
from .report import summarize
from .storage import digest, read_jsonl
from .transport import MODELS, parse_events


def verify_run(root):
    root = Path(root).resolve()
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def read(relative):
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Artifact path leaves the run directory")
        return path.read_text(encoding="utf-8")

    def data(relative):
        return json.loads(read(relative))

    manifest = data("manifest.json")
    cases = {c["id"]: Case(**c) for c in data("cases.json")}
    rows = read_jsonl(root / "attempts.jsonl")
    calls = read_jsonl(root / "calls.jsonl")
    check(len({r["id"] for r in rows}) == len(rows), "Duplicate attempt IDs")
    for filename, expected in manifest["source_sha256"].items():
        check(digest(read("source_snapshot/" + filename)) == expected,
              f"Source snapshot hash mismatch: {filename}")
    histories = {}
    validated_inputs = 0
    live = manifest["evidence"] == "live_codex_subscription"

    def verify_call(call_id, role, prompt, instructions):
        prefix = f"calls/{call_id}/"
        record = data(prefix + "call.json")
        request = data(prefix + "request.json")
        saved_prompt = read(prefix + "prompt.txt")
        saved_instructions = read(prefix + "instructions.txt")
        check(saved_prompt == prompt, f"Unexpected context/history in {call_id}")
        check(saved_instructions == instructions, f"Instructions differ in {call_id}")
        check(record["prompt_sha256"] == digest(saved_prompt), f"Prompt hash differs: {call_id}")
        check(record["instructions_sha256"] == digest(saved_instructions), f"Instruction hash differs: {call_id}")
        model, effort = MODELS[role]
        check((record["requested_model"], record["reasoning_effort"]) == (model, effort),
              f"Wrong model configuration in {call_id}")
        argv = request["argv"]
        check(argv[argv.index("--model") + 1] == model and
              f'model_reasoning_effort="{effort}"' in argv and
              'forced_login_method="chatgpt"' in argv and '--ephemeral' in argv,
              f"Wrong CLI arguments in {call_id}")
        answer, usage = parse_events(read(prefix + "events.jsonl"), role)
        check(answer == data(prefix + "answer.json"), f"Answer differs from CLI events: {call_id}")
        check(usage == record["usage"], f"Usage differs from CLI events: {call_id}")
        return answer

    for row in rows:
        prefix = f"attempts/{row['id']}/"
        check(data(prefix + "result.json") == row, f"Attempt journal differs: {row['id']}")
        if "candidate" not in row:
            continue
        case = cases[row["case"]]
        source = read(row["candidate"])
        check(digest(source) == row["source_sha256"], f"Candidate hash differs: {row['id']}")
        oracle = evaluate(case, source)
        validated_inputs += len(oracle["rows"])
        check(oracle == row["oracle"] == data(prefix + "oracle.json"),
              f"Oracle replay differs: {row['id']}")
        check(read(prefix + "candidate.diff") == diff(case, source), f"Diff differs: {row['id']}")
        success = oracle["target_regression"] and row["reviewer"]["verdict"] == "PASS"
        check(success == row["success"], f"Success label differs: {row['id']}")
        if row["stage"] == "search":
            key = (row["case"], row["repeat"], row["mode"])
            history = histories.setdefault(key, [])
            supplied = history if row["mode"] == "feedback" else []
            check(row["feedback_items"] == len(supplied), f"History count differs: {row['id']}")
            if live and row.get("attacker_call"):
                answer = verify_call(row["attacker_call"], "attacker",
                                     attacker_prompt(case, supplied), ATTACKER_INSTRUCTIONS)
                check(answer["source"] == source, f"Candidate differs from model answer: {row['id']}")
            history.append({"candidate_source": source, "validation": oracle["reason"],
                            "reviewer": row["reviewer"]})
        if live and row.get("reviewer_call"):
            answer = verify_call(row["reviewer_call"], "reviewer",
                                 reviewer_prompt(case, source), REVIEWER_INSTRUCTIONS)
            check(answer == row["reviewer"], f"Reviewer answer differs: {row['id']}")
    check(summarize(manifest, rows, calls) == data("report.json"), "Summary differs from journals")
    return {"ok": not errors, "evidence": manifest["evidence"], "run_status": manifest["status"],
            "attempt_records": len(rows), "replayed_input_rows": validated_inputs, "errors": errors,
            "scope": "Artifact consistency and current oracle/prompt replay, not provider model attestation"}
