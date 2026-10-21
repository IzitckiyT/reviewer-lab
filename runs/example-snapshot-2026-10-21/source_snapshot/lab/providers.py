"""Text-only provider adapters with a common, replayable evidence journal.

Provider responses are normalized locally; the journal is not a signed provider
attestation. No transport automatically retries a failed or ambiguous request.
"""
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .storage import append_json, digest, now, read_jsonl, write_json
from .transport import BudgetStop, CodexTransport, MODELS, TransportError, parse_events, redact, sanitized_env


PROVIDERS = {
    "codex": {"id": "codex", "label": "Codex · ChatGPT", "kind": "cli",
              "models": [MODELS["reviewer"][0], MODELS["attacker"][0]],
              "default_reviewer": MODELS["reviewer"][0], "default_attacker": MODELS["attacker"][0],
              "setup": "codex login"},
    "claude": {"id": "claude", "label": "Claude Code", "kind": "cli",
               "models": ["sonnet", "opus", "haiku"], "default_reviewer": "sonnet",
               "default_attacker": "sonnet", "setup": "claude auth login"},
    "deepseek": {"id": "deepseek", "label": "DeepSeek", "kind": "api",
                 "models": ["deepseek-flash", "deepseek-v4-pro"],
                 "default_reviewer": "deepseek-flash", "default_attacker": "deepseek-flash",
                 "setup": "Set DEEPSEEK_API_KEY in the server environment"},
}
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
PROVIDER_EVIDENCE = {"codex": "live_codex_subscription", "claude": "live_claude_cli", "deepseek": "live_deepseek_api"}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SECRET_NAMES = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def catalog():
    return copy.deepcopy(list(PROVIDERS.values()))


def evidence_for(provider="codex"):
    _provider(provider)
    return PROVIDER_EVIDENCE[provider]


def _provider(provider):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise TransportError("Unknown provider; choose codex, claude or deepseek")
    return PROVIDERS[provider]


def models_for(provider="codex", reviewer_model=None, attacker_model=None, reviewer_effort="medium"):
    spec = _provider(provider)
    models = {"reviewer": (reviewer_model or spec["default_reviewer"], reviewer_effort),
              "attacker": (attacker_model or spec["default_attacker"],
                           "xhigh" if provider == "codex" else "high")}
    for model, effort in models.values():
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
            raise TransportError("Invalid model ID")
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise TransportError("Invalid reasoning effort")
    return models


def _cli(provider):
    preferred = os.environ.get("REVIEWER_LAB_" + provider.upper())
    if preferred:
        return shutil.which(preferred)
    if provider == "codex" and not preferred:
        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
        preferred = str(bundled) if bundled.is_file() else None
    found = shutil.which(preferred or provider)
    if found or provider != "claude":
        return found
    for candidate in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                      Path("/usr/local/bin/claude")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def setup_status(provider="codex"):
    """Local presence only: no inference, HTTP request, or credential-file reads."""
    spec = _provider(provider)
    available = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()) if provider == "deepseek" else bool(_cli(provider))
    return {"provider": provider, "available": available, "configured": available, "ready": available,
            "message": ("Key configured; validity and balance are checked on the first request" if provider == "deepseek"
                        else "CLI found; login is checked before launch") if available else
                       ("DEEPSEEK_API_KEY is missing" if provider == "deepseek" else spec["label"] + " CLI not found"),
            "setup_command": spec["setup"]}


def _safe(value):
    text = redact(str(value))
    for name in _SECRET_NAMES:
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _valid_usage(usage):
    return isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                          for k in ("input_tokens", "output_tokens"))


def schema_instructions(instructions, schema):
    return instructions + "\nReturn only a JSON object matching this JSON schema:\n" + json.dumps(schema)


def _validate_schema(value, schema):
    """Validate the small schema vocabulary used by the two lab roles."""
    kind = schema.get("type")
    if kind == "object":
        if type(value) is not dict:
            raise TransportError("Response does not match the JSON schema")
        properties = schema.get("properties", {})
        if not set(schema.get("required", ())) <= set(value) or (
                schema.get("additionalProperties") is False and not set(value) <= set(properties)):
            raise TransportError("Response does not match the JSON schema")
        for key in value.keys() & properties.keys():
            _validate_schema(value[key], properties[key])
    elif kind == "string":
        if type(value) is not str:
            raise TransportError("Response does not match the JSON schema")
    else:
        raise TransportError("Unsupported response schema")
    if "enum" in schema and value not in schema["enum"]:
        raise TransportError("Response does not match the JSON schema")


class _JournalTransport:
    """Journal before sending and finalize known usage even on invalid answers."""
    def __init__(self, root, max_calls=18, token_limit=300_000, timeout=600, models=None, resume=False):
        self.root = Path(root)
        self.models = dict(models or models_for(self.provider))
        for role, pair in self.models.items():
            if role not in ("attacker", "reviewer") or len(pair) != 2 or not _MODEL_ID.fullmatch(pair[0]):
                raise TransportError("Invalid provider model configuration")
        if not self.models:
            raise TransportError("At least one model role must be configured")
        self.max_calls, self.token_limit, self.timeout = max_calls, token_limit, timeout
        self.calls, self.tokens, self.halted = 0, 0, False
        self.last_call_id = None
        self.next_call_number = 1
        if resume:
            latest = {row["id"]: row for row in read_jsonl(self.root / "calls.jsonl")}
            if any(row.get("provider", self.provider) != self.provider for row in latest.values()):
                raise TransportError("Cannot resume calls with a different provider")
            self.calls = len(latest)
            self.tokens = sum(row["usage"]["input_tokens"] + row["usage"]["output_tokens"]
                              for row in latest.values() if _valid_usage(row.get("usage")))
            self.halted = any(row.get("status") in ("started", "error", "interrupted") and not _valid_usage(row.get("usage"))
                              for row in latest.values())
            numbers = [int(p.name.split("-", 1)[0]) for p in (self.root / "calls").glob("*")
                       if re.fullmatch(r"\d+-(attacker|reviewer)", p.name)]
            self.next_call_number = max(numbers + [int(key.split("-", 1)[0]) for key in latest], default=0) + 1

    def call(self, role, prompt, instructions, schema):
        if role not in self.models:
            raise TransportError("Unknown model role")
        if self.halted or self.calls >= self.max_calls or self.tokens >= self.token_limit:
            raise BudgetStop("Call/token threshold reached or transport halted")
        self.calls += 1
        call_id = f"{self.next_call_number:03d}-{role}"
        self.next_call_number += 1
        folder = self.root / "calls" / call_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        (folder / "instructions.txt").write_text(instructions, encoding="utf-8")
        write_json(folder / "schema.json", schema)
        record = {"id": call_id, "provider": self.provider, "role": role, "started_at": now(), "status": "started",
                  "requested_model": self.models[role][0], "reasoning_effort": self.models[role][1],
                  "prompt_sha256": digest(prompt), "instructions_sha256": digest(instructions), "usage": None,
                  "provider_model_attestation": "Locally recorded request and provider response; not a signed attestation"}
        write_json(folder / "call.json", record)
        append_json(self.root / "calls.jsonl", record)
        self.last_call_id = call_id
        started = time.monotonic()
        try:
            answer = self._request(role, prompt, instructions, schema, folder, record)
            if _safe(json.dumps(answer, ensure_ascii=False)) != json.dumps(answer, ensure_ascii=False):
                raise TransportError("Provider answer contains a credential-like value; refusing to save it")
            if not _valid_usage(record.get("usage")):
                raise TransportError("Usage missing or invalid; stop rather than lose accounting")
            # Use the existing role contract as well as the supplied JSON schema.
            _validate_schema(answer, schema)
            events = [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer, ensure_ascii=False)}},
                      {"type": "turn.completed", "usage": record["usage"]}]
            normalized = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
            (folder / "events.jsonl").write_text(normalized, encoding="utf-8")
            answer, usage = parse_events(normalized, role)
            write_json(folder / "answer.json", answer)
            record["status"] = "completed"
            return {"answer": answer, "call_id": call_id, "usage": usage}
        except (TransportError, OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            self.halted = True
            message = _safe(str(exc)) if isinstance(exc, TransportError) else "Provider request or response failed; no automatic retry"
            record.update(status="error", error=message)
            raise TransportError(message) from None
        finally:
            if _valid_usage(record.get("usage")):
                self.tokens += record["usage"]["input_tokens"] + record["usage"]["output_tokens"]
            if record["status"] != "completed":
                events = ([{"type": "turn.completed", "usage": record["usage"]}] if _valid_usage(record.get("usage")) else [])
                events.append({"type": "turn.failed", "error": "Provider call did not produce a validated answer"})
                (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            record.update(elapsed_seconds=round(time.monotonic() - started, 3), finished_at=now())
            write_json(folder / "call.json", record)
            append_json(self.root / "calls.jsonl", record)


def _claude_env():
    env = sanitized_env()
    env.pop("CODEX_HOME", None)
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    # No model/provider overrides, tracing destinations, credential helpers or
    # user-injected settings are forwarded from the launching process.
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"
    return env


class ClaudeTransport(_JournalTransport):
    provider = "claude"
    evidence = "live_claude_cli"

    def __init__(self, root, claude=None, **kwargs):
        super().__init__(root, **kwargs)
        self.claude = shutil.which(claude) if claude else _cli("claude")
        if not self.claude:
            raise TransportError("Claude Code CLI not found. Install Claude Code and run claude auth login.")

    def doctor(self):
        try:
            version = subprocess.run([self.claude, "--version"], text=True, capture_output=True,
                                     timeout=20, env=_claude_env())
            auth = subprocess.run([self.claude, "auth", "status"], text=True, capture_output=True,
                                  timeout=20, env=_claude_env())
            info = json.loads(auth.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise TransportError("Cannot check Claude Code. Update it and run claude auth login.") from None
        if version.returncode or auth.returncode or not isinstance(info, dict) or info.get("loggedIn") is not True:
            raise TransportError("Claude sign-in required. Run claude auth login.")
        if info.get("authMethod") == "third_party":
            raise TransportError("Third-party Claude backends are disabled. Run claude auth login.")
        return {"provider": self.provider, "authentication": "Claude Code managed login",
                "cli_version": _safe(version.stdout.strip()), "requested_models": self.models,
                "provider_model_attestation": "Requested aliases; actual model names are recorded after each response"}

    def argv(self, role, instructions, schema):
        model, effort = self.models[role]
        settings = json.dumps({"disableAllHooks": True, "enabledPlugins": {}, "fallbackModel": [],
                               "switchModelsOnFlag": False})
        system = schema_instructions(instructions, schema)
        return [self.claude, "--safe-mode", "--restricted", "--print", "--output-format", "json",
                "--model", model, "--effort", effort, "--tools", "", "--disallowedTools", "*",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--no-session-persistence", "--no-chrome",
                "--setting-sources", "", "--settings", settings, "--max-turns", "1",
                "--system-prompt", system]

    def _request(self, role, prompt, instructions, schema, folder, record):
        args = self.argv(role, instructions, schema)
        write_json(folder / "request.json", {"provider": self.provider, "argv": args,
                                            "requested_model": self.models[role][0],
                                            "cwd": "isolated empty temporary directory", "stream_format": "claude-json"})
        with tempfile.TemporaryDirectory(prefix="reviewer-lab-claude-") as cwd:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, cwd=cwd, env=_claude_env(), start_new_session=True)
            try:
                stdout, _stderr = proc.communicate(prompt, timeout=self.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                raise TransportError("Claude timed out/interrupted; usage may be unknown; no automatic retry") from None
        try:
            response = json.loads(stdout)
        except ValueError:
            raise TransportError("Claude did not return a JSON result; no automatic retry") from None
        if not isinstance(response, dict):
            raise TransportError("Malformed Claude result")
        usage = response.get("usage")
        if isinstance(usage, dict):
            keys = ("input_tokens", "output_tokens")
            if all(type(usage.get(k)) is int and usage[k] >= 0 for k in keys):
                cached = [usage.get(k, 0) for k in ("cache_creation_input_tokens", "cache_read_input_tokens")]
                if all(type(v) is int and v >= 0 for v in cached):
                    record["usage"] = {"input_tokens": usage["input_tokens"] + sum(cached),
                                       "output_tokens": usage["output_tokens"]}
        reported = [model for model in response.get("modelUsage", {})
                    if isinstance(model, str) and _MODEL_ID.fullmatch(model) and _safe(model) == model] if isinstance(response.get("modelUsage"), dict) else []
        record["reported_models"] = reported
        content = response.get("result")
        safe_content = content if isinstance(content, str) and _safe(content) == content else None
        try:
            structured = json.loads(safe_content)
        except (ValueError, TypeError):
            structured = None
        write_json(folder / "provider-response.json", {"provider": self.provider, "usage": record["usage"],
                    "reported_models": reported, "is_error": response.get("is_error") is True,
                    "subtype": _safe(response.get("subtype", "unknown")), "result": safe_content,
                    "structured_output": structured})
        if proc.returncode or response.get("is_error") is True or response.get("subtype") != "success":
            raise TransportError("Claude call failed; check sign-in, quota and model access; no automatic retry")
        # --json-schema adds the SDK's StructuredOutput tool and retries. Plain
        # JSON text plus local validation keeps this a single text-only turn.
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            raise TransportError("Claude final response is not JSON; no automatic retry") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError("DeepSeek redirected the request; refusing to forward credentials")


class DeepSeekTransport(_JournalTransport):
    provider = "deepseek"
    evidence = "live_deepseek_api"

    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise TransportError("DEEPSEEK_API_KEY is missing. Set it in the server environment.")

    def doctor(self):
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise TransportError("DEEPSEEK_API_KEY is missing")
        return {"provider": self.provider, "authentication": "DEEPSEEK_API_KEY configured",
                "requested_models": self.models, "endpoint": DEEPSEEK_ENDPOINT,
                "provider_model_attestation": "Key presence only; validity and balance are checked on the first request"}

    def _request(self, role, prompt, instructions, schema, folder, record):
        model, effort = self.models[role]
        # JSON mode is not JSON Schema enforcement; validate the result locally.
        system = schema_instructions(instructions, schema)
        body = {"model": model, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": prompt}], "response_format": {"type": "json_object"},
                "stream": False, "max_tokens": min(8192, max(1, self.token_limit - self.tokens)),
                "thinking": {"type": "enabled"}, "reasoning_effort": effort}
        write_json(folder / "request.json", {"provider": self.provider, "endpoint": DEEPSEEK_ENDPOINT,
                                            "requested_model": model, "method": "POST", "body": body,
                                            "credentials": "DEEPSEEK_API_KEY environment variable"})
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key or any(char in key for char in "\r\n"):
            raise TransportError("Invalid DEEPSEEK_API_KEY environment value")
        request = urllib.request.Request(DEEPSEEK_ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
                if len(payload) > 16 * 1024 * 1024:
                    raise TransportError("DeepSeek response exceeds the size limit")
                result = json.loads(payload)
        except urllib.error.HTTPError as exc:
            messages = {401: "DeepSeek rejected the API key", 402: "DeepSeek balance is insufficient",
                        429: "DeepSeek rate limit reached"}
            raise TransportError(messages.get(exc.code, f"DeepSeek HTTP error {exc.code}") + "; no automatic retry") from None
        except (urllib.error.URLError, TimeoutError):
            raise TransportError("DeepSeek connection failed or timed out; usage may be unknown; no automatic retry") from None
        if not isinstance(result, dict):
            raise TransportError("Malformed DeepSeek response")
        raw_usage = result.get("usage", {})
        if isinstance(raw_usage, dict):
            usage = {"input_tokens": raw_usage.get("prompt_tokens"), "output_tokens": raw_usage.get("completion_tokens")}
            if _valid_usage(usage):
                record["usage"] = usage
        reported = result.get("model")
        record["reported_model"] = reported if isinstance(reported, str) and _MODEL_ID.fullmatch(reported) and _safe(reported) == reported else None
        choices = result.get("choices")
        content = None
        finish_reason, tool_calls_used = None, None
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict):
            content = choices[0]["message"].get("content")
            finish_reason = choices[0].get("finish_reason")
            tool_calls_used = bool(choices[0]["message"].get("tool_calls"))
            if not isinstance(content, str) or _safe(content) != content:
                content = None
        write_json(folder / "provider-response.json", {"provider": self.provider, "reported_model": record["reported_model"],
                    "usage": record["usage"], "content": content, "finish_reason": _safe(finish_reason) if finish_reason else None,
                    "tool_calls_used": tool_calls_used})
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise TransportError("DeepSeek returned no unique completion")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise TransportError("DeepSeek response was incomplete or stopped abnormally; no automatic retry")
        message = choice.get("message", {})
        if not isinstance(message, dict) or message.get("tool_calls"):
            raise TransportError("Unexpected tool call in text-only DeepSeek response")
        try:
            return json.loads(message.get("content"))
        except (ValueError, TypeError):
            raise TransportError("DeepSeek final response is not JSON; no automatic retry") from None


def create_transport(root, *, provider="codex", **kwargs):
    _provider(provider)
    transport = {"codex": CodexTransport, "claude": ClaudeTransport, "deepseek": DeepSeekTransport}[provider]
    return transport(root, **kwargs)
