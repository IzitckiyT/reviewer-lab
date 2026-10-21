"""Small, locally verified examples for the task catalogue.

These are saved observations, not signed provider attestations. Reading the
catalogue never creates a transport or requests a new model response.
"""
import hashlib
import json
import re
import threading
from pathlib import Path

from .cases import CASES
from .oracle import evaluate
from .providers import PROVIDER_EVIDENCE
from .transport import TransportError, parse_events, redact


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _file(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Example file leaves its run")
    return path


def _text(root, relative, limit=1_500_000):
    path = _file(root, relative)
    if path.stat().st_size > limit:
        raise ValueError("Example file is too large")
    return path.read_text(encoding="utf-8")


def _json(root, relative):
    return json.loads(_text(root, relative))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public(case):
    return json.loads(json.dumps(case.public()))


def _verified_row(root, row, case):
    """Cross-check the saved source, oracle and exact completed model request."""
    identity = row.get("id")
    call_id = row.get("reviewer_call")
    if (not isinstance(identity, str) or not _NAME.fullmatch(identity) or
            not isinstance(call_id, str) or not _NAME.fullmatch(call_id) or
            row.get("case") != case.id or row.get("error")):
        return None
    reviewer = row.get("reviewer", {})
    if (not isinstance(reviewer, dict) or not isinstance(reviewer.get("reason"), str) or
            not isinstance(row.get("oracle"), dict)):
        return None
    verdict = reviewer.get("verdict")
    if verdict not in ("PASS", "BLOCK") or row.get("outcome") != (
            "success" if verdict == "PASS" and row.get("oracle", {}).get("target_regression") else verdict.lower()):
        return None
    folder = f"attempts/{identity}"
    if _json(root, f"{folder}/result.json") != row:
        return None
    source = _text(root, f"{folder}/candidate.py", 20_000)
    if _sha(source) != row.get("source_sha256"):
        return None
    actual = evaluate(case, source)
    saved = row.get("oracle")
    if actual != saved or _json(root, f"{folder}/oracle.json") != saved:
        return None
    call = _json(root, f"calls/{call_id}/call.json")
    if (not isinstance(call, dict) or call.get("id") != call_id or
            call.get("role") != "reviewer" or call.get("status") != "completed" or
            not isinstance(call.get("started_at"), str) or
            not isinstance(call.get("finished_at"), str) or
            not isinstance(call.get("requested_model"), str) or
            not _MODEL.fullmatch(call["requested_model"])):
        return None
    prompt = _text(root, f"calls/{call_id}/prompt.txt")
    payload = json.loads(prompt)
    if (not isinstance(payload, dict) or _sha(prompt) != call.get("prompt_sha256") or
            payload.get("case") != _public(case) or payload.get("candidate_source") != source):
        return None
    answer = _json(root, f"calls/{call_id}/answer.json")
    if answer != reviewer:
        return None
    observed, _ = parse_events(_text(root, f"calls/{call_id}/events.jsonl", 8_000_000), "reviewer")
    if observed != answer:
        return None
    return actual, call


class CaseExamples:
    """Cache finite-domain replays until any referenced evidence file changes."""

    def __init__(self, runs):
        self.runs = Path(runs).resolve()
        self.cache = {}
        self.lock = threading.Lock()

    def _fingerprint(self, root, rows):
        names = {"manifest.json", "cases.json", "attempts.jsonl"}
        for row in rows:
            identity, call_id = row.get("id"), row.get("reviewer_call")
            if isinstance(identity, str) and _NAME.fullmatch(identity):
                names.update(f"attempts/{identity}/{name}" for name in
                             ("result.json", "oracle.json", "candidate.py"))
            if isinstance(call_id, str) and _NAME.fullmatch(call_id):
                names.update(f"calls/{call_id}/{name}" for name in
                             ("call.json", "answer.json", "prompt.txt", "events.jsonl"))
        values = []
        for name in sorted(names):
            try:
                stat = _file(root, name).stat()
                values.append((name, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
            except FileNotFoundError:
                values.append((name, None))
        return tuple(values)

    def _run(self, root, manifest, rows):
        provider = manifest.get("provider", "codex")
        if (provider not in PROVIDER_EVIDENCE or manifest.get("status") != "completed" or
                not isinstance(manifest.get("models"), dict) or
                not isinstance(manifest.get("cases"), list) or
                manifest.get("evidence") != PROVIDER_EVIDENCE.get(provider)):
            return []
        snapshots = _json(root, "cases.json")
        if not isinstance(snapshots, list):
            return []
        cases = {c.get("id"): c for c in snapshots if isinstance(c, dict)}
        checked, controls = [], {}
        for row in rows:
            case_id = row.get("case")
            case = CASES.get(case_id)
            saved_case = cases.get(case_id)
            if (case is None or case_id not in manifest.get("cases", []) or
                    not isinstance(saved_case, dict) or
                    {key: saved_case.get(key) for key in case.public()} != _public(case)):
                continue
            try:
                verified = _verified_row(root, row, case)
            except (ValueError, OSError, TypeError, KeyError, TransportError):
                continue
            if verified is None:
                continue
            oracle, call = verified
            if list(manifest.get("models", {}).get("reviewer", [])) != [
                    call["requested_model"], call.get("reasoning_effort")]:
                continue
            verdict = row["reviewer"]["verdict"]
            if row.get("stage") == "control":
                kind = row.get("control")
                if ((kind == "safe" and oracle.get("correct") is True) or
                        (kind == "seed" and oracle.get("target_regression") is True)):
                    controls.setdefault(case_id, {})[kind] = verdict
            elif (row.get("stage") == "search" and oracle.get("target_regression") is True and
                  oracle.get("status") == "evaluated" and oracle.get("witnesses")):
                witness = oracle["witnesses"][0]
                checked.append((case_id, {
                    "run_id": root.name, "attempt_id": row["id"],
                    "verdict": verdict, "reason": redact(row["reviewer"]["reason"])[:4_000],
                    "provider": provider, "model": call["requested_model"],
                    "effort": call.get("reasoning_effort"), "started_at": call.get("started_at"),
                    "confirmed_miss": verdict == "PASS", "verified": True,
                    "witness": {key: witness[key] for key in ("input", "expected", "actual")},
                    "checked_count": len(oracle["rows"]),
                }))
        for case_id, example in checked:
            selected = controls.get(case_id, {})
            example["controls"] = {"safe_verdict": selected.get("safe"),
                "seed_verdict": selected.get("seed"),
                "healthy": selected.get("safe") == "PASS" and selected.get("seed") == "BLOCK"}
        return checked

    def list(self):
        with self.lock:
            found, present = {}, set()
            for root in self.runs.glob("*"):
                if not _NAME.fullmatch(root.name) or not root.is_dir():
                    continue
                try:
                    if not root.resolve().is_relative_to(self.runs):
                        continue
                    present.add(root.name)
                    manifest = _json(root, "manifest.json")
                    if not isinstance(manifest, dict) or manifest.get("status") != "completed":
                        continue
                    rows = []
                    for line in _text(root, "attempts.jsonl", 8_000_000).splitlines():
                        try:
                            value = json.loads(line)
                            if isinstance(value, dict):
                                rows.append(value)
                        except ValueError:
                            continue
                    signature = self._fingerprint(root, rows)
                    cached = self.cache.get(root.name)
                    if cached is None or cached[0] != signature:
                        cached = (signature, self._run(root, manifest, rows))
                        self.cache[root.name] = cached
                    for case_id, example in cached[1]:
                        key = (example["confirmed_miss"], example.get("started_at") or "",
                               bool(manifest.get("publication")),
                               example["run_id"], example["attempt_id"])
                        previous = found.get(case_id)
                        if previous is None or key > previous[0]:
                            found[case_id] = key, example
                except (ValueError, OSError, TypeError, KeyError, TransportError):
                    continue
            self.cache = {name: value for name, value in self.cache.items() if name in present}
            return {case_id: value[1] for case_id, value in found.items()}
