import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from lab.prompts import ATTACKER_SCHEMA, REVIEWER_SCHEMA
from lab.providers import (ClaudeTransport, DeepSeekTransport, DEEPSEEK_ENDPOINT, _NoRedirect,
                           catalog, create_transport, evidence_for, models_for, setup_status)
from lab.transport import BudgetStop, CodexTransport, TransportError, parse_events


GOOD = {"verdict": "BLOCK", "reason": "The editor is not the owner."}


def deep_response(answer=GOOD, *, finish="stop", usage=True):
    response = {"model": "deepseek-v4.1-flash-1017", "choices": [{"finish_reason": finish,
                "message": {"content": json.dumps(answer), "reasoning_content": "SECRET_REASONING_DO_NOT_SAVE"}}]}
    if usage:
        response["usage"] = {"prompt_tokens": 30, "completion_tokens": 12}
    return response


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret-without-key-prefix"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def fake_http(self, response):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        return opener

    def run_deepseek(self, response= None, **kwargs):
        transport = DeepSeekTransport(self.root, **kwargs)
        opener = self.fake_http(response if response is not None else deep_response())
        with patch("lab.providers.urllib.request.build_opener", return_value=opener):
            answer = transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        return transport, opener, answer

    def test_factory_preserves_codex_and_catalog_is_detached(self):
        with patch("lab.transport.shutil.which", return_value="/cli/codex"):
            self.assertIsInstance(create_transport(self.root), CodexTransport)
        data = catalog()
        self.assertEqual({row["id"] for row in data}, {"codex", "claude", "deepseek"})
        data[0]["models"].append("mutation")
        self.assertNotIn("mutation", catalog()[0]["models"])
        self.assertEqual(evidence_for("deepseek"), "live_deepseek_api")
        self.assertEqual(models_for("deepseek")["reviewer"], ("deepseek-flash", "medium"))
        with self.assertRaises(TransportError):
            models_for("claude", reviewer_model="sonnet; echo nope")

    def test_setup_and_doctor_do_not_infer_or_expose_credentials(self):
        with patch("lab.providers.urllib.request.build_opener") as network, patch("lab.providers.subprocess.Popen") as child:
            status = setup_status("deepseek")
            info = DeepSeekTransport(self.root).doctor()
        self.assertTrue(status["ready"])
        self.assertIn("first request", info["provider_model_attestation"])
        self.assertNotIn(os.environ["DEEPSEEK_API_KEY"], json.dumps(info))
        network.assert_not_called()
        child.assert_not_called()

    def test_deepseek_normalized_evidence_and_safe_request(self):
        transport, opener, answer = self.run_deepseek()
        self.assertEqual(answer["answer"], GOOD)
        self.assertEqual(transport.tokens, 42)
        folder = self.root / "calls/001-reviewer"
        events = (folder / "events.jsonl").read_text()
        self.assertEqual(parse_events(events, "reviewer"), (GOOD, {"input_tokens": 30, "output_tokens": 12}))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, DEEPSEEK_ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + os.environ["DEEPSEEK_API_KEY"])
        saved = "\n".join(path.read_text() for path in self.root.rglob("*") if path.is_file())
        self.assertNotIn(os.environ["DEEPSEEK_API_KEY"], saved)
        self.assertNotIn("SECRET_REASONING_DO_NOT_SAVE", saved)
        self.assertEqual(json.loads((folder / "provider-response.json").read_text())["reported_model"], "deepseek-v4.1-flash-1017")
        self.assertEqual(json.loads((folder / "provider-response.json").read_text())["finish_reason"], "stop")
        self.assertFalse(json.loads((folder / "provider-response.json").read_text())["tool_calls_used"])

    def test_invalid_deepseek_answer_keeps_usage_and_halts(self):
        transport = DeepSeekTransport(self.root)
        response = self.fake_http(deep_response({"verdict": "PASS", "reason": "ok", "extra": True}))
        with patch("lab.providers.urllib.request.build_opener", return_value=response):
            with self.assertRaises(TransportError):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        self.assertEqual(transport.tokens, 42)
        self.assertEqual(json.loads((self.root / "calls/001-reviewer/call.json").read_text())["status"], "error")
        with self.assertRaises(BudgetStop):
            transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)

    def test_incomplete_response_keeps_known_usage(self):
        transport = DeepSeekTransport(self.root)
        with patch("lab.providers.urllib.request.build_opener", return_value=self.fake_http(deep_response(finish="length"))):
            with self.assertRaisesRegex(TransportError, "incomplete"):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        self.assertEqual(transport.tokens, 42)

    def test_http_error_body_cannot_leak_key_and_no_retry(self):
        transport = DeepSeekTransport(self.root)
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(DEEPSEEK_ENDPOINT, 401,
             os.environ["DEEPSEEK_API_KEY"], {}, io.BytesIO(os.environ["DEEPSEEK_API_KEY"].encode()))
        with patch("lab.providers.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(TransportError, "rejected the API key"):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        self.assertEqual(opener.open.call_count, 1)
        saved = "".join(path.read_text() for path in self.root.rglob("*") if path.is_file())
        self.assertNotIn(os.environ["DEEPSEEK_API_KEY"], saved)

    def test_redirects_are_not_followed_with_credentials(self):
        with self.assertRaisesRegex(TransportError, "refusing to forward credentials"):
            _NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://attacker.invalid")

    def test_unknown_usage_cannot_resume_and_budget_does_resume(self):
        with self.assertRaises(TransportError):
            self.run_deepseek(deep_response(usage=False))
        transport = DeepSeekTransport(self.root, resume=True)
        self.assertTrue(transport.halted)
        with self.assertRaises(BudgetStop):
            transport.call("reviewer", "x", "y", REVIEWER_SCHEMA)
        other = self.root / "known"
        first = DeepSeekTransport(other)
        with patch("lab.providers.urllib.request.build_opener", return_value=self.fake_http(deep_response())):
            first.call("reviewer", "x", "y", REVIEWER_SCHEMA)
        resumed = DeepSeekTransport(other, resume=True, token_limit=42)
        self.assertEqual((resumed.calls, resumed.tokens, resumed.next_call_number), (1, 42, 2))
        with self.assertRaises(BudgetStop):
            resumed.call("reviewer", "x", "y", REVIEWER_SCHEMA)

    def test_claude_empty_cwd_disabled_tools_and_cache_accounting(self):
        result = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(GOOD),
                  "usage": {"input_tokens": 8, "output_tokens": 9, "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 11}, "modelUsage": {"claude-sonnet-5": {}},
                  "reasoning": "SECRET_REASONING_DO_NOT_SAVE"}
        child = MagicMock(returncode=0)
        child.communicate.return_value = (json.dumps(result), "")
        def launch(args, **kwargs):
            self.assertEqual(list(Path(kwargs["cwd"]).iterdir()), [])
            self.assertEqual(args[args.index("--tools") + 1], "")
            self.assertIn("--restricted", args)
            self.assertIn("--safe-mode", args)
            self.assertEqual(args[args.index("--setting-sources") + 1], "")
            self.assertNotIn("--json-schema", args)
            settings = json.loads(args[args.index("--settings") + 1])
            self.assertEqual(settings["fallbackModel"], [])
            self.assertFalse(settings["switchModelsOnFlag"])
            self.assertNotIn("DEEPSEEK_API_KEY", kwargs["env"])
            return child
        with patch("lab.providers.shutil.which", return_value="/cli/claude"), patch("lab.providers.subprocess.Popen", side_effect=launch):
            transport = ClaudeTransport(self.root)
            answer = transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        self.assertEqual(answer["answer"], GOOD)
        self.assertEqual(transport.tokens, 38)
        self.assertEqual(json.loads((self.root / "calls/001-reviewer/call.json").read_text())["reported_models"], ["claude-sonnet-5"])
        self.assertNotIn("SECRET_REASONING_DO_NOT_SAVE", "".join(p.read_text() for p in self.root.rglob("*") if p.is_file()))

    def test_claude_error_usage_preserved_without_raw_error(self):
        child = MagicMock(returncode=1)
        child.communicate.return_value = (json.dumps({"is_error": True, "usage": {"input_tokens": 4, "output_tokens": 5},
                "result": os.environ["DEEPSEEK_API_KEY"]}), os.environ["DEEPSEEK_API_KEY"])
        with patch("lab.providers.shutil.which", return_value="/cli/claude"), patch("lab.providers.subprocess.Popen", return_value=child):
            transport = ClaudeTransport(self.root)
            with self.assertRaisesRegex(TransportError, "Claude call failed"):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        self.assertEqual(transport.tokens, 9)
        self.assertNotIn(os.environ["DEEPSEEK_API_KEY"], "".join(p.read_text() for p in self.root.rglob("*") if p.is_file()))

    def test_claude_doctor_uses_only_login_and_version(self):
        with patch("lab.providers.shutil.which", return_value="/cli/claude"), patch("lab.providers.subprocess.run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, "2.1.300", ""),
                               subprocess.CompletedProcess([], 0, json.dumps({"loggedIn": True, "email": "private@example.com"}), "")]
            result = ClaudeTransport(self.root).doctor()
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("private@example.com", json.dumps(result))
        self.assertEqual(run.call_args.args[0][1:], ["auth", "status"])

    def test_empty_reviewer_reason_is_invalid_and_attacker_contract_works(self):
        with self.assertRaises(TransportError):
            self.run_deepseek(deep_response({"verdict": "PASS", "reason": "   "}))
        transport = DeepSeekTransport(self.root / "attacker")
        with patch("lab.providers.urllib.request.build_opener", return_value=self.fake_http(deep_response({"source": "def decision(): return True"}))):
            answer = transport.call("attacker", "candidate", "instructions", ATTACKER_SCHEMA)
        self.assertIn("source", answer["answer"])

    def test_manual_reviewer_only_models_and_bad_provider(self):
        transport, _, result = self.run_deepseek(models={"reviewer": ("deepseek-flash", "medium")})
        self.assertEqual(result["answer"], GOOD)
        resumed = DeepSeekTransport(self.root, models={"reviewer": ("deepseek-flash", "medium")}, resume=True)
        self.assertEqual(resumed.tokens, 42)
        for provider in ([], None, "unknown"):
            with self.assertRaises(TransportError):
                setup_status(provider)

    def test_deepseek_tools_rejected_and_credentials_cannot_be_reflected(self):
        result = deep_response()
        result["choices"][0]["message"]["tool_calls"] = [{"type": "function", "id": "forbidden"}]
        with self.assertRaisesRegex(TransportError, "Unexpected tool call"):
            self.run_deepseek(result)
        transport = DeepSeekTransport(self.root / "reflected")
        response = deep_response({"verdict": "BLOCK", "reason": os.environ["DEEPSEEK_API_KEY"]})
        response["model"] = os.environ["DEEPSEEK_API_KEY"]
        with patch("lab.providers.urllib.request.build_opener", return_value=self.fake_http(response)):
            with self.assertRaisesRegex(TransportError, "credential-like"):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
        saved = "".join(path.read_text() for path in (self.root / "reflected").rglob("*") if path.is_file())
        self.assertNotIn(os.environ["DEEPSEEK_API_KEY"], saved)
        self.assertEqual(transport.tokens, 42)

    def test_claude_timeout_kills_owned_group_and_cannot_resume_unknown_usage(self):
        child = MagicMock(returncode=-9, pid=12345)
        child.communicate.side_effect = [subprocess.TimeoutExpired("claude", 1), ("", "")]
        with patch("lab.providers.shutil.which", return_value="/cli/claude"), patch("lab.providers.subprocess.Popen", return_value=child), patch("lab.providers.os.killpg") as kill:
            transport = ClaudeTransport(self.root)
            with self.assertRaisesRegex(TransportError, "timed out"):
                transport.call("reviewer", "candidate", "instructions", REVIEWER_SCHEMA)
            kill.assert_called_once()
            resumed = ClaudeTransport(self.root, resume=True)
        self.assertTrue(resumed.halted)
        self.assertEqual(resumed.calls, 1)

    def test_claude_finder_fallback_and_malformed_auth_json(self):
        with patch("lab.providers.shutil.which", return_value=None), patch("lab.providers.Path.is_file", return_value=True), patch("lab.providers.os.access", return_value=True):
            self.assertTrue(setup_status("claude")["available"])
            self.assertTrue(ClaudeTransport(self.root).claude.endswith("/.local/bin/claude"))
        with patch("lab.providers.shutil.which", return_value="/cli/claude"), patch("lab.providers.subprocess.run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, "2.1.300", ""), subprocess.CompletedProcess([], 0, "[]", "")]
            with self.assertRaisesRegex(TransportError, "sign-in required"):
                ClaudeTransport(self.root).doctor()


if __name__ == "__main__":
    unittest.main()
