"""Codex CLI transport, restricted to ChatGPT auth and text-only fresh sessions."""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .storage import append_json, digest, now, write_json

MODELS = {"attacker": ("gpt-6.1-sol", "xhigh"), "reviewer": ("gpt-5.6-luna", "medium")}
DISABLED = ("shell_tool", "apps", "plugins", "hooks", "memories", "multi_agent",
            "multi_agent_v2", "browser_use", "in_app_browser", "computer_use",
            "image_generation", "view_image", "skill_search", "shell_snapshot",
            "request_permissions_tool", "goals", "workspace_dependencies")


class TransportError(RuntimeError):
    pass


class BudgetStop(TransportError):
    pass


def sanitized_env():
    allowed = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY",
               "ALL_PROXY", "NO_PROXY", "CODEX_HOME")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def redact(text):
    text = re.sub(r"(?i)(Bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:sk-|eyJ)[A-Za-z0-9_.-]{20,}", "[REDACTED]", text)
    return text


def managed_approval_notice(item):
    """CLI 0.147 exec defaults to Never; managed policy strengthens it to UnlessTrusted.

    This narrowly recognized pre-turn notice is preserved, not a model failure.
    Any other error, model override or tool item remains fatal. No policy is bypassed.
    """
    if not isinstance(item, dict):
        return False
    message = item.get("message", "")
    return (item.get("type") == "error" and isinstance(message, str) and
        message.startswith("Configured value for `approval_policy` is disallowed by requirements; "
                           "falling back to required value UnlessTrusted.") and
        "`Never` is not in the allowed set [UnlessTrusted, OnRequest]" in message)


def parse_events(text, role):
    events = []
    for line in text.splitlines():
        if line.strip():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TransportError("Malformed CLI event stream") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise TransportError("Malformed CLI event object")
            if event["type"].startswith("item.") and not isinstance(event.get("item"), dict):
                raise TransportError("Malformed CLI item object")
            events.append(event)
    if any(e.get("type") in ("turn.failed", "error") for e in events):
        raise TransportError("CLI reported a failed turn; inspect saved events")
    completed = [e for e in events if e.get("type") == "turn.completed"]
    if len(completed) != 1:
        raise TransportError("Expected exactly one completed CLI turn")
    for event in events:
        if event.get("type", "").startswith("item."):
            kind = event.get("item", {}).get("type")
            if kind not in ("agent_message", "reasoning") and not managed_approval_notice(event.get("item", {})):
                raise TransportError(f"Unexpected tool/item in text-only experiment: {kind}")
    messages = [e["item"].get("text") for e in events
                if e.get("type") == "item.completed" and
                e.get("item", {}).get("type") == "agent_message"]
    if not messages:
        raise TransportError("No final agent message")
    try:
        answer = json.loads(messages[-1])
    except (json.JSONDecodeError, TypeError) as exc:
        raise TransportError("Final response is not JSON") from exc
    if role == "attacker":
        valid = (type(answer) is dict and set(answer) == {"source"} and
                 type(answer["source"]) is str)
    else:
        valid = (type(answer) is dict and set(answer) == {"verdict", "reason"} and
                 answer["verdict"] in ("PASS", "BLOCK") and
                 type(answer["reason"]) is str and bool(answer["reason"].strip()))
    if not valid:
        raise TransportError(f"Invalid {role} response schema")
    usage = completed[0].get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                          for k in ("input_tokens", "output_tokens")):
        raise TransportError("Usage missing or invalid; stop rather than silently lose accounting")
    return answer, usage


class CodexTransport:
    evidence = "live_codex_subscription"

    def __init__(self, root, max_calls=18, token_limit=300_000, timeout=600, codex=None):
        self.root = Path(root)
        self.max_calls, self.token_limit, self.timeout = max_calls, token_limit, timeout
        preferred = codex or os.environ.get("REVIEWER_LAB_CODEX")
        if not preferred:
            bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
            preferred = str(bundled) if bundled.is_file() else "codex"
        self.codex = shutil.which(preferred)
        if not self.codex:
            raise TransportError("Codex CLI not found. Install it and run codex login.")
        self.calls, self.tokens, self.halted = 0, 0, False

    def doctor(self):
        result = subprocess.run([self.codex, "login", "status"], text=True,
            capture_output=True, env=sanitized_env(), timeout=20)
        status = result.stdout + result.stderr
        if result.returncode or "Logged in using ChatGPT" not in status:
            raise TransportError("ChatGPT sign-in required; API-key fallback is disabled. Run codex login.")
        version = subprocess.run([self.codex, "--version"], text=True,
            capture_output=True, env=sanitized_env(), timeout=20)
        if version.returncode:
            raise TransportError("Cannot read Codex CLI version")
        catalog_result = subprocess.run([self.codex, "debug", "models", "--bundled"],
            capture_output=True, text=True, env=sanitized_env(), timeout=20)
        try:
            catalog = json.loads(catalog_result.stdout)
            models = catalog["models"] if isinstance(catalog, dict) else catalog
            slugs = {model["slug"] for model in models}
        except (ValueError, TypeError, KeyError):
            raise TransportError("Cannot verify the CLI model catalog; use a current Codex CLI")
        if catalog_result.returncode:
            raise TransportError("CLI model catalog command failed")
        missing = {model for model, _ in MODELS.values()} - slugs
        if missing:
            raise TransportError(f"CLI catalog lacks {sorted(missing)}. Use a current CLI via REVIEWER_LAB_CODEX.")
        return {"authentication": "ChatGPT", "cli_version": version.stdout.strip(),
                "cli_path": self.codex, "catalog_models_present": True,
                "requested_models": MODELS, "provider_model_attestation":
                "CLI request configuration; JSONL does not report a provider snapshot ID"}

    def argv(self, role, cwd, schema, instructions):
        model, effort = MODELS[role]
        args = [self.codex, "exec", "--ignore-user-config", "--strict-config",
                "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                "--color", "never", "--json", "--model", model, "--cd", str(cwd),
                "--output-schema", str(schema)]
        settings = {"model_reasoning_effort": effort, "model_provider": "openai",
            "forced_login_method": "chatgpt", "approval_policy": "on-request",
            "web_search": "disabled", "project_doc_max_bytes": 0,
            "skills.include_instructions": False, "skills.bundled.enabled": False,
            "developer_instructions": instructions, "history.persistence": "none"}
        for key, value in settings.items():
            args.extend(["-c", f"{key}={json.dumps(value)}"])
        for feature in DISABLED:
            args.extend(["--disable", feature])
        return args + ["-"]

    def call(self, role, prompt, instructions, schema):
        if self.halted or self.calls >= self.max_calls or self.tokens >= self.token_limit:
            raise BudgetStop("Call/token threshold reached or transport halted")
        self.calls += 1
        call_id = f"{self.calls:03d}-{role}"
        folder = self.root / "calls" / call_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        (folder / "instructions.txt").write_text(instructions, encoding="utf-8")
        schema_path = folder / "schema.json"
        write_json(schema_path, schema)
        started = time.monotonic()
        record = {"id": call_id, "role": role, "started_at": now(), "status": "started",
                  "requested_model": MODELS[role][0], "reasoning_effort": MODELS[role][1],
                  "prompt_sha256": digest(prompt), "instructions_sha256": digest(instructions),
                  "usage": None}
        write_json(folder / "call.json", record)
        append_json(self.root / "calls.jsonl", record)
        print(f"  {call_id}: {MODELS[role][0]} / {MODELS[role][1]}", flush=True)
        try:
            # Temporary working directory has no candidate, oracle, results, AGENTS or config.
            with tempfile.TemporaryDirectory(prefix="reviewer-lab-") as empty_cwd:
                args = self.argv(role, empty_cwd, schema_path.resolve(), instructions)
                write_json(folder / "request.json", {"argv": args, "cwd": empty_cwd})
                # Persist events as the child emits them, including on controller crashes.
                # No API credentials are passed in env; auth remains managed by Codex.
                with (folder / "events.jsonl").open("w", encoding="utf-8") as event_out, \
                     (folder / "stderr.txt").open("w", encoding="utf-8") as error_out:
                    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=event_out,
                        stderr=error_out, text=True, env=sanitized_env(), start_new_session=True)
                    try:
                        proc.communicate(prompt, timeout=self.timeout)
                    except (subprocess.TimeoutExpired, KeyboardInterrupt):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.communicate()
                        raise TransportError("CLI timed out/interrupted; usage may be unknown; no automatic retry")
            stdout = (folder / "events.jsonl").read_text(encoding="utf-8")
            stderr = (folder / "stderr.txt").read_text(encoding="utf-8")
            (folder / "events.jsonl").write_text(redact(stdout), encoding="utf-8")
            (folder / "stderr.txt").write_text(redact(stderr), encoding="utf-8")
            # Preserve known billed usage even if schema validation or configuration
            # diagnostics invalidate the experiment observation afterwards.
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                usage = event.get("usage")
                if managed_approval_notice(event.get("item", {})):
                    record.setdefault("cli_diagnostics", []).append(event["item"]["message"])
                    record["effective_approval_policy"] = "UnlessTrusted (managed requirement)"
                if event.get("type") == "turn.completed" and isinstance(usage, dict) and all(
                    type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")
                ):
                    record["usage"] = usage
            if proc.returncode:
                raise TransportError(f"CLI exited with {proc.returncode}; see {folder.name}/stderr.txt")
            answer, usage = parse_events(stdout, role)
            record.update(status="completed", usage=usage)
            write_json(folder / "answer.json", answer)
            return {"answer": answer, "call_id": call_id, "usage": usage}
        except (TransportError, OSError) as exc:
            self.halted = True
            record.update(status="error", error=redact(str(exc)))
            raise TransportError(record["error"]) from exc
        finally:
            if record.get("usage"):
                self.tokens += record["usage"]["input_tokens"] + record["usage"]["output_tokens"]
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["finished_at"] = now()
            write_json(folder / "call.json", record)
            append_json(self.root / "calls.jsonl", record)
