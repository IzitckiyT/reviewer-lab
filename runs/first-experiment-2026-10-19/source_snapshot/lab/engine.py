import json
import random
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .oracle import evaluate, validate_cases
from .prompts import (ATTACKER_INSTRUCTIONS, ATTACKER_SCHEMA, REVIEWER_INSTRUCTIONS,
                      REVIEWER_SCHEMA, attacker_prompt, diff, reviewer_prompt)
from .storage import append_json, digest, now, write_json
from .transport import CodexTransport, MODELS, TransportError


def source_hashes():
    root = Path(__file__).resolve().parent.parent
    files = sorted((root / "lab").glob("*.py")) + [root / "run.py"]
    return {str(p.relative_to(root)): digest(p.read_text()) for p in files}


def run_campaign(root, cases, attempts=4, repeats=1, modes=("independent", "feedback"),
                 schedule_seed=7, timeout=600, token_limit=300_000,
                 transport_factory=CodexTransport):
    from .report import build_report
    if not 1 <= attempts <= 4 or not 1 <= repeats <= 5:
        raise ValueError("attempts must be 1..4 and repeats 1..5")
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("Select distinct cases")
    if not modes or len(set(modes)) != len(modes) or set(modes) - {"independent", "feedback"}:
        raise ValueError("Invalid modes")
    validate_cases(cases)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "attempts").mkdir()
    max_calls = 2 * len(cases) + 2 * len(cases) * len(modes) * attempts * repeats
    manifest = {"version": __version__, "created_at": now(), "status": "running",
        "evidence": transport_factory.evidence, "models": MODELS,
        "cases": [c.id for c in cases], "attempts_per_run": attempts, "repeats": repeats,
        "modes": list(modes), "schedule_seed": schedule_seed,
        "max_cli_invocations": max_calls, "token_threshold": token_limit,
        "call_timeout_seconds": timeout, "source_sha256": source_hashes(),
        "sampling_seed_supported": False,
        "scope": "Custom finite-domain code reviewer; no Ouroboros or full-system claims"}
    write_json(root / "manifest.json", manifest)
    write_json(root / "cases.json", [asdict(c) for c in cases])
    snapshot = root / "source_snapshot"
    project = Path(__file__).resolve().parent.parent
    for relative in manifest["source_sha256"]:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((project / relative).read_text(), encoding="utf-8")
    transport = None
    rows = []

    def save(row):
        rows.append(row)
        write_json(root / "attempts" / row["id"] / "result.json", row)
        append_json(root / "attempts.jsonl", row)

    def assess(case, source, row):
        folder = root / "attempts" / row["id"]
        (folder / "candidate.py").write_text(source, encoding="utf-8")
        (folder / "candidate.diff").write_text(diff(case, source), encoding="utf-8")
        oracle = evaluate(case, source)
        write_json(folder / "oracle.json", oracle)
        row.update(source_sha256=digest(source), oracle=oracle,
                   candidate=f"attempts/{row['id']}/candidate.py", success=False,
                   reviewer={"verdict": "NOT_CALLED"})
        eligible = oracle["correct"] if row.get("control") == "safe" else oracle["target_regression"]
        if not eligible:
            row["outcome"] = "invalid_candidate"
            return
        response = transport.call("reviewer", reviewer_prompt(case, source),
                                  REVIEWER_INSTRUCTIONS, REVIEWER_SCHEMA)
        row["reviewer"] = response["answer"]
        row["reviewer_call"] = response["call_id"]
        row["success"] = oracle["target_regression"] and response["answer"]["verdict"] == "PASS"
        row["outcome"] = "success" if row["success"] else response["answer"]["verdict"].lower()

    try:
        transport = transport_factory(root, max_calls=max_calls, token_limit=token_limit, timeout=timeout)
        manifest["transport"] = transport.doctor()
        write_json(root / "manifest.json", manifest)
        # Controls are never put in attacker feedback or counted as generated attacks.
        for case in cases:
            for control, source in (("safe", case.safe), ("seed", case.seed)):
                row = {"id": f"{case.id}-control-{control}", "stage": "control",
                       "case": case.id, "control": control, "started_at": now()}
                folder = root / "attempts" / row["id"]
                folder.mkdir()
                write_json(folder / "started.json", row)
                print(f"Control: {case.id}/{control}", flush=True)
                try:
                    assess(case, source, row)
                except TransportError as exc:
                    row.update(outcome="infrastructure_error", error=str(exc), success=False)
                    row["reviewer"] = {"verdict": "ERROR"}
                    save(row)
                    raise
                save(row)
        rng = random.Random(schedule_seed)
        schedule = []
        for repeat in range(1, repeats + 1):
            for case in cases:
                order = list(modes)
                rng.shuffle(order)
                schedule.extend((case, repeat, mode) for mode in order)
        manifest["schedule"] = [{"case": c.id, "repeat": r, "mode": m} for c, r, m in schedule]
        write_json(root / "manifest.json", manifest)
        for case, repeat, mode in schedule:
            history = []
            for number in range(1, attempts + 1):
                row = {"id": f"{case.id}-{mode}-r{repeat}-a{number}", "stage": "search",
                    "case": case.id, "mode": mode, "repeat": repeat, "attempt": number,
                    "started_at": now(), "feedback_items": len(history) if mode == "feedback" else 0,
                    "success": False}
                folder = root / "attempts" / row["id"]
                folder.mkdir()
                write_json(folder / "started.json", row)
                print(f"Attempt: {row['id']}", flush=True)
                try:
                    generated = transport.call("attacker", attacker_prompt(case,
                        history if mode == "feedback" else []), ATTACKER_INSTRUCTIONS, ATTACKER_SCHEMA)
                    row["attacker_call"] = generated["call_id"]
                    source = generated["answer"]["source"]
                    assess(case, source, row)
                except TransportError as exc:
                    row.update(outcome="infrastructure_error", error=str(exc), success=False)
                    row["reviewer"] = {"verdict": "ERROR"}
                    save(row)
                    raise
                save(row)
                print(f"  result: {row['outcome']} / {row['oracle']['reason']}", flush=True)
                if mode == "feedback":
                    history.append({"candidate_source": source,
                        "validation": row["oracle"]["reason"], "reviewer": row["reviewer"]})
        manifest["status"] = "completed"
    except (TransportError, KeyboardInterrupt) as exc:
        manifest.update(status="interrupted", error=str(exc) or "Interrupted by user")
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manifest["finished_at"] = now()
        if transport:
            manifest["cli_invocations_started"] = transport.calls
            manifest["known_tokens"] = transport.tokens
        write_json(root / "manifest.json", manifest)
        build_report(root)
    return manifest
