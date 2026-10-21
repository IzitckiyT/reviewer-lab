"""Export one saved attempt with a small, offline reproduction bundle."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\Z")
MODULES = ("__init__.py", "cases.py", "interpreter.py", "oracle.py")
REPLAY = '''#!/usr/bin/env python3
"""Offline replay; the candidate is evaluated by a restricted interpreter."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
for name, expected in evidence["files_sha256"].items():
    target = (ROOT / name).resolve()
    if not target.is_relative_to(ROOT) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise SystemExit("File changed: " + name)
from _lab.cases import CASES
from _lab.oracle import evaluate

source = (ROOT / "candidate.py").read_text(encoding="utf-8")
actual = evaluate(CASES[evidence["case"]["id"]], source)
saved = evidence["oracle"]
matches = all(actual.get(k) == saved.get(k) for k in
              ("status", "correct", "target_regression", "rows", "witnesses"))
print(json.dumps({"matches_saved_oracle": matches, "oracle": actual,
    "saved_reviewer": evidence["reviewer"],
    "note": "Offline computation only. No new model response or provider attestation."},
    ensure_ascii=False, indent=2))
raise SystemExit(0 if matches else 1)
'''


def _bytes(root, relative, limit=1_000_000):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evidence file leaves the run directory")
    if path.stat().st_size > limit:
        raise ValueError("Evidence file is too large")
    return path.read_bytes()


def _json(root, relative):
    return json.loads(_bytes(root, relative))


def build_evidence(run_path, attempt_id):
    """Include only selected evidence, never CLI configs, logs or local paths."""
    root = Path(run_path).resolve()
    if not isinstance(attempt_id, str) or not IDENTIFIER.fullmatch(attempt_id):
        raise ValueError("Invalid attempt identifier")
    row = _json(root, f"attempts/{attempt_id}/result.json")
    if row.get("id") != attempt_id or row.get("outcome") == "running" or not row.get("oracle"):
        raise ValueError("A finished local test is required before export")
    manifest = _json(root, "manifest.json")
    cases = _json(root, "cases.json")
    case = next((c for c in cases if c.get("id") == row.get("case")), None)
    if not case:
        raise ValueError("Saved case is missing")
    source = _bytes(root, f"attempts/{attempt_id}/candidate.py", 20_000)
    if hashlib.sha256(source).hexdigest() != row.get("source_sha256"):
        raise ValueError("Candidate hash does not match the saved attempt")
    files = {"candidate.py": source, "original.py": case["original"].encode("utf-8")}
    for name in MODULES:
        relative = f"lab/{name}"
        data = _bytes(root, f"source_snapshot/{relative}")
        if hashlib.sha256(data).hexdigest() != manifest.get("source_sha256", {}).get(relative):
            raise ValueError("Saved evaluator hash does not match the manifest")
        files[f"_lab/{name}"] = data
    call = {}
    call_id = row.get("reviewer_call")
    if isinstance(call_id, str) and IDENTIFIER.fullmatch(call_id):
        call = _json(root, f"calls/{call_id}/call.json")
    evidence = {
        "format": "reviewer-lab-evidence-v1",
        "run_id": root.name, "attempt_id": attempt_id,
        "started_at": row.get("started_at"),
        "case": {k: case[k] for k in ("id", "title", "rule", "parameters", "domains")},
        "oracle": row["oracle"],
        "reviewer": {k: row.get("reviewer", {}).get(k) for k in ("verdict", "reason")},
        "reviewer_call": {k: call.get(k) for k in
                          ("requested_model", "reasoning_effort", "started_at", "finished_at", "usage")},
        "confirmed_miss": bool(row["oracle"].get("target_regression") and
                               row.get("reviewer", {}).get("verdict") == "PASS"),
        "model_evidence": manifest.get("evidence"),
        "limits": "Finite saved domain only; saved model answer, not a new inference or provider attestation.",
        "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    verdict = evidence["reviewer"].get("verdict") or "NOT_CALLED"
    witnesses = row["oracle"].get("witnesses", [])
    outcome = ("Подтверждённый пропуск" if evidence["confirmed_miss"] else
               "Патч отклонён" if verdict == "BLOCK" else "Сохранённый результат проверки")
    guide = (f"Reviewer Lab — {outcome}\n\n"
             f"Правило: {case['title']}\nПопытка: {attempt_id}\n"
             f"Ответ ревьюера: {verdict}\n\n"
             "Как воспроизвести:\n1. Распакуйте архив.\n"
             "2. В этой папке выполните: python3 replay.py\n"
             "Нужен Python 3.10+. Установка пакетов, ключи и сеть не нужны.\n"
             "Код кандидата вычисляется ограниченным интерпретатором.\n\n"
             "Ожидаемый итог: matches_saved_oracle = true. Это сверка сохранённого\n"
             "поведения, а не новый ответ модели. Исходный кандидат — candidate.py,\n"
             "полные входы, правило и ответ модели — evidence.json.\n\n"
             f"Контрпримеры: {json.dumps(witnesses, ensure_ascii=False)}\n\n"
             "Ограничение: только конечный домен этого кейса. Хеши проверяют\n"
             "согласованность файлов; они не удостоверяют провайдера модели.\n")
    files.update({"README.txt": guide.encode("utf-8"), "replay.py": REPLAY.encode("utf-8"),
                  "evidence.json": json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return f"reviewer-lab-{attempt_id}.zip", stream.getvalue()
