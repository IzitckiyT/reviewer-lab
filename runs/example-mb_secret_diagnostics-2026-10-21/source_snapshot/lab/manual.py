"""Append-only, human-authored attempts with an offline gate before model calls."""
import fcntl
import json
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .cases import CASES, Case
from .engine import source_hashes
from .oracle import evaluate, validate_cases
from .prompts import REVIEWER_INSTRUCTIONS, REVIEWER_SCHEMA, diff, reviewer_prompt
from .report import build_report
from .storage import append_json, digest, now, read_jsonl, recover_jsonl_tail, write_json
from .transport import CodexTransport, MODELS, TransportError, recover_interrupted_calls
from .providers import create_transport, evidence_for, models_for

MAX_SOURCE_BYTES = 20_000


def validate_source(case, source):
    if not isinstance(case, str) or case not in CASES:
        raise ValueError("Unknown case")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Paste the complete decision function")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("Candidate must be at most 20,000 UTF-8 bytes")


def local_result(oracle):
    if oracle["status"] == "invalid":
        return "invalid"
    if oracle["correct"]:
        return "rule_preserved"
    return "target_regression" if oracle["target_regression"] else "different_regression"


def check_source(case, source):
    validate_source(case, source)
    oracle = evaluate(CASES[case], source)
    return {"oracle": oracle, "local_result": local_result(oracle)}


def load_series(root, case):
    """Reject non-manual runs and changed cases before any append or model call."""
    try:
        manifest = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))
        cases = json.loads((Path(root) / "cases.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or not isinstance(cases, list) or
                manifest.get("experiment_kind") != "manual" or
                manifest.get("cases") != [case] or manifest.get("modes") != ["manual"] or
                manifest.get("repeats") != 1 or len(cases) != 1 or
                not isinstance(cases[0], dict) or cases[0].get("id") != case):
            raise ValueError("Select a manual series with the same case")
        saved_case = Case(**cases[0])
        validate_cases([saved_case])
        return manifest, saved_case
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid manual series") from exc


def create_series(root, case, timeout, token_limit, evidence, provider="codex", models=None):
    root.mkdir(parents=True, exist_ok=False)
    (root / "attempts").mkdir()
    manifest = {"version": __version__, "created_at": now(), "status": "running",
        "evidence": evidence, "provider": provider, "experiment_kind": "manual", "candidate_origin": "manual",
        "models": {"attacker": ("manual", "not_used"), "reviewer": (models or MODELS)["reviewer"]},
        "cases": [case.id], "modes": ["manual"], "repeats": 1, "attempts_per_run": 0,
        "max_cli_invocations": 0, "token_threshold": token_limit, "call_timeout_seconds": timeout,
        "source_sha256": source_hashes(), "sampling_seed_supported": False,
        "scope": "Human-authored finite-domain candidates; no automatic attacker or population-rate claims"}
    write_json(root / "manifest.json", manifest)
    write_json(root / "cases.json", [asdict(case)])
    project = Path(__file__).resolve().parent.parent
    for relative in manifest["source_sha256"]:
        target = root / "source_snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((project / relative).read_text(encoding="utf-8"), encoding="utf-8")
    return manifest


def run_manual_attempt(root, case, source, timeout=600, token_limit=300_000,
                       transport_factory=None, provider=None, reviewer_model=None):
    validate_source(case.id, source)
    if timeout < 10 or token_limit < 1:
        raise ValueError("timeout must be >=10 seconds and token-limit positive")
    root = Path(root).resolve()
    if root.exists():
        manifest, case = load_series(root, case.id)
        saved_provider = manifest.get("provider", "codex")
        if provider is not None and provider != saved_provider:
            raise ValueError("Провайдер серии закреплён. Для другой модели начните новую серию.")
        provider = saved_provider
        if reviewer_model is not None and reviewer_model != manifest["models"]["reviewer"][0]:
            raise ValueError("Модель серии закреплена. Для другой модели начните новую серию.")
        evidence = transport_factory.evidence if transport_factory else evidence_for(provider)
        if manifest["evidence"] != evidence:
            raise ValueError("Manual series transport evidence cannot change")
    else:
        provider = provider or "codex"
        models = models_for(provider, reviewer_model=reviewer_model)
        evidence = transport_factory.evidence if transport_factory else evidence_for(provider)
        validate_cases([case])
        manifest = create_series(root, case, timeout, token_limit, evidence, provider, models)
    if transport_factory is None:
        transport_factory = lambda root, **options: create_transport(root, provider=provider, **options)
    with (root / ".manual.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("This manual series is already running") from exc
        # Reload after acquiring the lock; another completed submission may have appended.
        manifest, case = load_series(root, case.id)
        for journal_name in ("attempts.jsonl", "calls.jsonl"):
            recovery = recover_jsonl_tail(root / journal_name)
            if recovery:
                manifest.setdefault("journal_recoveries", []).append(recovery)
                write_json(root / "manifest.json", manifest)
        recovered_calls = recover_interrupted_calls(root)
        manifest["cli_invocations_started"] = len(recovered_calls)
        manifest["known_tokens"] = sum(call["usage"]["input_tokens"] + call["usage"]["output_tokens"]
                                       for call in recovered_calls.values() if call.get("usage"))
        rows = read_jsonl(root / "attempts.jsonl")
        transport = None

        def save(row):
            row["finished_at"] = now()
            write_json(root / "attempts" / row["id"] / "result.json", row)
            append_json(root / "attempts.jsonl", row)
            rows.append(row)

        # A hard-terminated process may leave an unfinished attempt. Finalize its record,
        # preserving the source, tests and any call files, before accepting the next one.
        known = {r["id"] for r in rows}
        for folder in sorted((root / "attempts").iterdir()):
            if folder.name in known or not (folder / "started.json").is_file():
                continue
            if (folder / "result.json").is_file():
                # The result was finalized before termination, only its journal append
                # was lost. Reuse it byte-for-byte rather than replacing it with started.json.
                old = json.loads((folder / "result.json").read_text(encoding="utf-8"))
                append_json(root / "attempts.jsonl", old)
                rows.append(old)
                continue
            old = json.loads((folder / "started.json").read_text(encoding="utf-8"))
            old.update(outcome="interrupted", success=False,
                       error="Previous process ended before this attempt was finalized")
            save(old)
        number = 1 + max((r.get("attempt", 0) for r in rows if r["stage"] == "search"), default=0)
        manifest.update(status="running", updated_at=now(), attempts_per_run=number)
        manifest.pop("finished_at", None)
        manifest.pop("error", None)
        write_json(root / "manifest.json", manifest)

        def prepare(source_text, identity, **fields):
            folder = root / "attempts" / identity
            folder.mkdir()
            oracle = evaluate(case, source_text)
            row = {"id": identity, "case": case.id, "started_at": now(), "success": False,
                   "reviewer": {"verdict": "NOT_CALLED"}, "source_sha256": digest(source_text),
                   "candidate": f"attempts/{identity}/candidate.py", "oracle": oracle,
                   "local_result": local_result(oracle), **fields}
            (folder / "candidate.py").write_text(source_text, encoding="utf-8")
            (folder / "candidate.diff").write_text(diff(case, source_text), encoding="utf-8")
            write_json(folder / "oracle.json", oracle)
            write_json(folder / "started.json", row)
            return row

        def review(row, source_text):
            folder = root / "attempts" / row["id"]
            prompt = reviewer_prompt(case, source_text)
            (folder / "reviewer-prompt.txt").write_text(prompt, encoding="utf-8")
            (folder / "reviewer-instructions.txt").write_text(REVIEWER_INSTRUCTIONS, encoding="utf-8")
            calls_before = transport.calls
            try:
                response = transport.call("reviewer", prompt, REVIEWER_INSTRUCTIONS, REVIEWER_SCHEMA)
            except (TransportError, KeyboardInterrupt):
                if transport.calls > calls_before:
                    row["reviewer"] = {"verdict": "ERROR"}
                    if getattr(transport, "last_call_id", None):
                        row["reviewer_failed_call"] = transport.last_call_id
                raise
            row.update(reviewer=response["answer"], reviewer_call=response["call_id"])
            row["success"] = row["oracle"]["target_regression"] and row["reviewer"]["verdict"] == "PASS"
            row["outcome"] = "success" if row["success"] else row["reviewer"]["verdict"].lower()

        row = prepare(source, f"{case.id}-manual-r1-a{number}", stage="search", mode="manual",
                      repeat=1, attempt=number, feedback_items=0, candidate_origin={"kind": "manual"})
        print(f"Manual attempt: {row['id']} / {row['local_result']}", flush=True)
        try:
            if row["local_result"] != "target_regression":
                row["outcome"] = {"invalid": "local_invalid", "rule_preserved": "rule_preserved",
                                  "different_regression": "different_regression"}[row["local_result"]]
            else:
                completed_controls = {r["control"] for r in rows if r["stage"] == "control"
                                      and r.get("reviewer", {}).get("verdict") in ("PASS", "BLOCK")}
                pending_controls = [(kind, text) for kind, text in (("safe", case.safe), ("seed", case.seed))
                                    if kind not in completed_controls]
                old_calls = {r["id"]: r for r in read_jsonl(root / "calls.jsonl")}
                manifest["max_cli_invocations"] = len(old_calls) + len(pending_controls) + 1
                write_json(root / "manifest.json", manifest)
                # Authentication/catalog work is deliberately deferred until the offline gate passes.
                transport = transport_factory(root, max_calls=manifest["max_cli_invocations"],
                    token_limit=manifest["token_threshold"], timeout=manifest["call_timeout_seconds"],
                    models={"reviewer": tuple(manifest["models"]["reviewer"])}, resume=True)
                manifest["transport"] = transport.doctor()
                write_json(root / "manifest.json", manifest)
                for kind, control_source in pending_controls:
                    tries = 1 + sum(r.get("control") == kind for r in rows)
                    identity = f"{case.id}-control-{kind}" + (f"-{tries}" if tries > 1 else "")
                    control = prepare(control_source, identity, stage="control", control=kind)
                    try:
                        review(control, control_source)
                    except (TransportError, KeyboardInterrupt) as exc:
                        control.update(outcome="infrastructure_error" if isinstance(exc, TransportError) else "interrupted",
                                       error=str(exc) or "Interrupted by user")
                        save(control)
                        raise
                    save(control)
                review(row, source)
            save(row)
            manifest["status"] = "completed"
        except (TransportError, KeyboardInterrupt) as exc:
            row.update(outcome="infrastructure_error" if isinstance(exc, TransportError) else "interrupted",
                       error=str(exc) or "Interrupted by user", success=False)
            save(row)
            manifest.update(status="interrupted", error=row["error"])
        except Exception as exc:
            row.update(outcome="infrastructure_error", error=f"{type(exc).__name__}: {exc}", success=False)
            save(row)
            manifest.update(status="failed", error=row["error"])
            raise
        finally:
            manifest["finished_at"] = now()
            if transport:
                manifest["cli_invocations_started"] = transport.calls
                manifest["known_tokens"] = transport.tokens
            write_json(root / "manifest.json", manifest)
            build_report(root)
    return manifest
