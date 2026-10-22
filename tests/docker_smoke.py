"""Docker smoke checks, called explicitly by CI rather than unittest discovery.

`cli` runs inside the image without inference; HTTP checks run on the CI host.
Live inference is replaced by tests/mock_codex.py on an internal Docker bridge.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (args, result.returncode, result.stderr[:500])
    return result.stdout


def cli_checks():
    assert os.getuid() == 10001
    assert "0.163.0" in command("/usr/local/bin/codex", "--version")
    assert "2.1.308" in command("/usr/local/bin/claude", "--version")
    codex_help = command("/usr/local/bin/codex", "exec", "--help")
    for flag in ("--ignore-user-config", "--strict-config", "--ephemeral", "--output-schema"):
        assert flag in codex_help, f"Installed Codex lacks {flag}"
    assert "--device-auth" in command("/usr/local/bin/codex", "login", "--help")
    catalogue = json.loads(command("/usr/local/bin/codex", "debug", "models", "--bundled"))
    assert isinstance(catalogue.get("models") if isinstance(catalogue, dict) else catalogue, list)
    claude_help = command("/usr/local/bin/claude", "--help")
    for flag in ("--safe-mode", "--restricted", "--tools", "--strict-mcp-config",
                 "--setting-sources", "--no-session-persistence"):
        assert flag in claude_help, f"Installed Claude lacks {flag}"
    assert "login" in command("/usr/local/bin/claude", "auth", "--help")
    assert "--help" in command("/usr/local/bin/claude", "auth", "login", "--help")
    try:
        Path("/app/ci-root-write-check").write_text("must fail")
    except OSError:
        pass
    else:
        raise AssertionError("Container root filesystem is writable")
    print("Official CLI versions, required flags, login help and read-only root verified; no inference.")


def fetch(origin, path, payload=None, token=None):
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Origin": origin, "X-Lab-Token": token}
    request = urllib.request.Request(origin + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def wait_state(origin):
    for attempt in range(60):
        try:
            return fetch(origin, "/api/state")
        except (OSError, urllib.error.URLError):
            if attempt == 59:
                raise
            time.sleep(1)


def saved_examples(state):
    examples = {case["id"]: case.get("example_run") for case in state["cases"]}
    assert set(examples) == {"owner", "audit", "snapshot", "mb_firewall_whitelist",
        "mb_firewall_policy", "mb_sudo_bootstrap", "mb_secret_diagnostics", "mb_malware_chain"}
    assert all(example and example.get("verified") is True for example in examples.values())
    history = state["history_examples"]
    assert {item["id"] for item in history} == {
        "role-collision", "unstable-verdict", "attacker-revision", "external-health-file"}
    assert all(item.get("verified") is True for item in history)


def live_checks(origin, marker):
    state = wait_state(origin)
    assert state["read_only"] is False
    assert state["live_available"] is True
    saved_examples(state)
    result = fetch(origin, "/api/start", {
        "preset": "feedback", "case": "owner", "effort": "medium", "attempts": 1,
        "repeats": 1, "provider": "codex", "reviewer_model": "ci-reviewer",
        "attacker_model": "ci-attacker"}, state["token"])
    run_id = result["id"]
    for attempt in range(60):
        state = fetch(origin, "/api/state")
        if not state["active"]["running"]:
            assert state["active"]["exit_code"] == 0, state["active"]
            break
        time.sleep(1)
    else:
        raise AssertionError("Mock experiment did not finish")
    detail = fetch(origin, "/api/runs/" + run_id)
    assert detail["manifest"]["status"] == "completed", detail["manifest"]
    assert detail["manifest"]["models"] == {
        "reviewer": ["ci-reviewer", "medium"], "attacker": ["ci-attacker", "xhigh"]}
    assert len(detail["rows"]) == 3
    assert all("OFFLINE CI MOCK" in row["reviewer"]["reason"] for row in detail["rows"])
    search = next(row for row in detail["rows"] if row["stage"] == "search")
    assert search["attacker_call"] and search["reviewer_call"]
    assert search["oracle"]["target_regression"] is True
    assert search["outcome"] == "block"
    marker.write_text(json.dumps({"id": run_id, "attempt": search["id"],
        "source_sha256": hashlib.sha256(search["source"].encode()).hexdigest()}))
    print("Live HTTP launch generated and reviewed a candidate through the offline mock; evidence saved.")


def persistence_checks(origin, marker):
    state = wait_state(origin)
    saved = json.loads(marker.read_text())
    assert any(run["id"] == saved["id"] for run in state["runs"])
    detail = fetch(origin, "/api/runs/" + saved["id"])
    row = next(row for row in detail["rows"] if row["id"] == saved["attempt"])
    assert hashlib.sha256(row["source"].encode()).hexdigest() == saved["source_sha256"]
    assert detail["manifest"]["status"] == "completed"
    print("Run and exact candidate persisted across container recreation.")


def demo_checks(origin):
    state = wait_state(origin)
    assert state["read_only"] is True
    saved_examples(state)
    for path in ("/api/start", "/api/manual", "/api/stop", "/api/enable-live"):
        try:
            fetch(origin, path, {}, state["token"])
        except urllib.error.HTTPError as error:
            assert error.code == 403
            assert json.load(error)["code"] == "demo_read_only"
        else:
            raise AssertionError("Demo accepted live action " + path)
    print("Demo examples verified; all model-launch and live-enable routes refused.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("cli", "live", "persistence", "demo"))
    parser.add_argument("--origin", default="http://127.0.0.1:8765")
    parser.add_argument("--marker", type=Path, default=Path("/tmp/reviewer-lab-ci-run.json"))
    args = parser.parse_args()
    if args.mode == "cli":
        cli_checks()
    elif args.mode == "live":
        live_checks(args.origin, args.marker)
    elif args.mode == "persistence":
        persistence_checks(args.origin, args.marker)
    else:
        demo_checks(args.origin)
