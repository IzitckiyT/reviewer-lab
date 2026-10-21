"""Offline consistency audit. Reads snapshots as data; never executes saved source."""
import json
from pathlib import Path

from .cases import Case
from .oracle import evaluate
from .prompts import (ATTACKER_INSTRUCTIONS, REVIEWER_INSTRUCTIONS, ATTACKER_SCHEMA, REVIEWER_SCHEMA,
                      attacker_prompt, reviewer_prompt, diff)
from .report import summarize
from .storage import digest, read_jsonl
from .transport import parse_events
from .providers import DEEPSEEK_ENDPOINT, PROVIDER_EVIDENCE, schema_instructions


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
    live = manifest["evidence"] in PROVIDER_EVIDENCE.values()
    provider = manifest.get("provider", "codex")
    if live:
        check(PROVIDER_EVIDENCE.get(provider) == manifest["evidence"], "Provider and evidence type differ")
    screening = manifest.get("experiment_kind") == "candidate_screen"
    manual = manifest.get("experiment_kind") == "manual"
    if manual:
        check(manifest["modes"] == ["manual"] and len(cases) == 1 and manifest["repeats"] == 1,
              "Manual series requires one fixed case and manual mode")
        search_rows = [row for row in rows if row.get("stage") == "search"]
        check([row.get("attempt") for row in search_rows] == list(range(1, len(search_rows) + 1)),
              "Manual attempt sequence is not contiguous")
        check(all(call.get("role") == "reviewer" for call in calls),
              "Unexpected non-reviewer call in manual series")
        for kind in ("safe", "seed"):
            completed = [row for row in rows if row.get("control") == kind and
                         row.get("reviewer", {}).get("verdict") in ("PASS", "BLOCK")]
            check(len(completed) <= 1, f"Completed manual control repeated: {kind}")
    if screening:
        check(manifest["modes"] == ["independent"] and len(cases) == 1,
              "Screening requires one case and independent mode")
        check(len(manifest.get("candidate_files", [])) == manifest["attempts_per_run"],
              "Screening source count differs")
    supplied_hashes = {}

    def verify_call(call_id, role, prompt, instructions):
        prefix = f"calls/{call_id}/"
        record = data(prefix + "call.json")
        request = data(prefix + "request.json")
        saved_prompt = read(prefix + "prompt.txt")
        saved_instructions = read(prefix + "instructions.txt")
        schema = data(prefix + "schema.json")
        check(schema == (ATTACKER_SCHEMA if role == "attacker" else REVIEWER_SCHEMA),
              f"Response schema contains unexpected instructions: {call_id}")
        check(saved_prompt == prompt, f"Unexpected context/history in {call_id}")
        check(saved_instructions == instructions, f"Instructions differ in {call_id}")
        check(record["prompt_sha256"] == digest(saved_prompt), f"Prompt hash differs: {call_id}")
        check(record["instructions_sha256"] == digest(saved_instructions), f"Instruction hash differs: {call_id}")
        model, effort = manifest["models"][role]
        check((record["requested_model"], record["reasoning_effort"]) == (model, effort),
              f"Wrong model configuration in {call_id}")
        if provider == "codex":
            argv = request.get("argv", [])
            check("--model" in argv and argv[argv.index("--model") + 1] == model and
                  f'model_reasoning_effort="{effort}"' in argv and
                  'forced_login_method="chatgpt"' in argv and '--ephemeral' in argv,
                  f"Wrong CLI arguments in {call_id}")
        else:
            check(request.get("provider") == record.get("provider") == provider and
                  request.get("requested_model") == model, f"Provider request differs: {call_id}")
            if provider == "claude":
                argv = request.get("argv", [])
                check("--model" in argv and argv[argv.index("--model") + 1] == model and
                      "--effort" in argv and argv[argv.index("--effort") + 1] == effort and
                      "--tools" in argv and argv[argv.index("--tools") + 1] == "" and
                      "--no-session-persistence" in argv and "--strict-mcp-config" in argv and
                      "--safe-mode" in argv and "--restricted" in argv and
                      "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "1" and
                      "--system-prompt" in argv and argv[argv.index("--system-prompt") + 1] ==
                      schema_instructions(instructions, data(prefix + "schema.json")),
                      f"Claude isolation/configuration differs: {call_id}")
            elif provider == "deepseek":
                body = request.get("body", {})
                schema = data(prefix + "schema.json")
                system = schema_instructions(instructions, schema)
                check(request.get("endpoint") == DEEPSEEK_ENDPOINT and body.get("model") == model and
                      body.get("reasoning_effort") == effort and
                      body.get("messages") == [{"role": "system", "content": system},
                                                {"role": "user", "content": prompt}] and
                      body.get("response_format") == {"type": "json_object"} and not body.get("tools"),
                      f"DeepSeek request context/configuration differs: {call_id}")
        answer, usage = parse_events(read(prefix + "events.jsonl"), role)
        check(answer == data(prefix + "answer.json"), f"Answer differs from CLI events: {call_id}")
        check(usage == record["usage"], f"Usage differs from CLI events: {call_id}")
        if provider != "codex":
            response = data(prefix + "provider-response.json")
            check(response.get("provider") == provider and response.get("usage") == usage,
                  f"Provider response accounting differs: {call_id}")
            if provider == "claude":
                try:
                    received = json.loads(response.get("result"))
                except (ValueError, TypeError):
                    received = None
                check(received == response.get("structured_output") == answer and response.get("is_error") is False and
                      response.get("subtype") in (None, "success"),
                      f"Claude response differs: {call_id}")
            elif provider == "deepseek":
                try:
                    received = json.loads(response.get("content"))
                except (ValueError, TypeError):
                    received = None
                check(received == answer and response.get("finish_reason") == "stop" and
                      response.get("tool_calls_used") is False, f"DeepSeek response differs: {call_id}")
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
        saved_oracle = row["oracle"]
        replay_oracle = oracle
        if "diagnostic" not in saved_oracle:
            # Diagnostics are explanatory metadata added after older runs were
            # recorded. Preserve exact checks for all behavior/evidence fields.
            replay_oracle = {key: value for key, value in oracle.items() if key != "diagnostic"}
        check(replay_oracle == saved_oracle == data(prefix + "oracle.json"),
              f"Oracle replay differs: {row['id']}")
        check(read(prefix + "candidate.diff") == diff(case, source), f"Diff differs: {row['id']}")
        success = oracle["target_regression"] and row["reviewer"]["verdict"] == "PASS"
        check(success == row["success"], f"Success label differs: {row['id']}")
        if row["stage"] == "search":
            if manual:
                check(not row.get("attacker_call"), f"Unexpected attacker call in manual series: {row['id']}")
                check(row.get("candidate_origin") == {"kind": "manual"},
                      f"Manual candidate provenance differs: {row['id']}")
                expected = ("invalid" if oracle["status"] == "invalid" else
                            "rule_preserved" if oracle["correct"] else
                            "target_regression" if oracle["target_regression"] else "different_regression")
                check(row.get("local_result") == expected, f"Manual local result differs: {row['id']}")
                if not oracle["target_regression"]:
                    check(not row.get("reviewer_call") and row["reviewer"]["verdict"] == "NOT_CALLED",
                          f"Ineligible manual candidate reached reviewer: {row['id']}")
            elif screening:
                origin = row.get("candidate_origin", {})
                check(not row.get("attacker_call"), f"Unexpected attacker call in screening: {row['id']}")
                check(origin.get("kind") == manifest.get("candidate_source_kind", "supplied_file") and
                      origin.get("path") == manifest["candidate_files"][row["attempt"] - 1],
                      f"Screening provenance differs: {row['id']}")
                if manifest.get("candidate_source_kind") == "case_seed":
                    check(source == case.seed and origin.get("path") == f"case-seed:{case.id}",
                          f"Case seed differs: {row['id']}")
                previous = supplied_hashes.setdefault(row["attempt"], row["source_sha256"])
                check(previous == row["source_sha256"], f"Screened source changed between repeats: {row['id']}")
            elif live:
                check(bool(row.get("attacker_call")), f"Missing attacker provenance: {row['id']}")
            key = (row["case"], row["repeat"], row["mode"])
            history = histories.setdefault(key, [])
            supplied = history if row["mode"] == "feedback" else []
            check(row["feedback_items"] == len(supplied), f"History count differs: {row['id']}")
            if live and row.get("attacker_call"):
                answer = verify_call(row["attacker_call"], "attacker",
                                     attacker_prompt(case, supplied, manifest.get("attacker_strategy", "original")), ATTACKER_INSTRUCTIONS)
                check(answer["source"] == source, f"Candidate differs from model answer: {row['id']}")
            history.append({"candidate_source": source, "validation": oracle["reason"],
                            "reviewer": row["reviewer"]})
        if live and row.get("reviewer_call"):
            answer = verify_call(row["reviewer_call"], "reviewer",
                                 reviewer_prompt(case, source), REVIEWER_INSTRUCTIONS)
            check(answer == row["reviewer"], f"Reviewer answer differs: {row['id']}")
        if manual and row.get("reviewer_call"):
            check(read(prefix + "reviewer-prompt.txt") == reviewer_prompt(case, source),
                  f"Manual reviewer prompt differs: {row['id']}")
            check(read(prefix + "reviewer-instructions.txt") == REVIEWER_INSTRUCTIONS,
                  f"Manual reviewer instructions differ: {row['id']}")
    check(summarize(manifest, rows, calls) == data("report.json"), "Summary differs from journals")
    return {"ok": not errors, "evidence": manifest["evidence"], "run_status": manifest["status"],
            "attempt_records": len(rows), "replayed_input_rows": validated_inputs, "errors": errors,
            "scope": "Artifact consistency and current oracle/prompt replay, not provider model attestation"}
