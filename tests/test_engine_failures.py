"""Failure attribution in the automatic campaign, with no CLI or model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from lab.cases import CASES
from lab.engine import run_campaign
from lab.storage import append_json, read_jsonl, write_json
from lab.transport import BudgetStop, TransportError
from lab.verify import verify_run


class CampaignFailureTests(unittest.TestCase):
    def run_failure(self, root, fail_on, before_start=False, interrupt=False):
        class FailureTransport:
            evidence = "offline_test_fixture"

            def __init__(self, root, **kwargs):
                self.root = Path(root)
                self.calls = self.tokens = 0
                self.last_call_id = None

            def doctor(self):
                return {"fixture": True}

            def call(self, role, prompt, instructions, schema):
                if self.calls + 1 == fail_on and before_start:
                    raise BudgetStop("Fixture budget exhausted")
                self.calls += 1
                self.last_call_id = f"{self.calls:03d}-{role}"
                failed = self.calls == fail_on
                folder = self.root / "calls" / self.last_call_id
                folder.mkdir(parents=True)
                record = {"id": self.last_call_id, "role": role,
                          "status": "error" if failed else "completed", "usage": None}
                write_json(folder / "call.json", record)
                append_json(self.root / "calls.jsonl", record)
                if failed:
                    (folder / "stderr.txt").write_text("Fixture failure evidence")
                    if interrupt:
                        raise KeyboardInterrupt()
                    raise TransportError("Fixture transport failed")
                answer = ({"source": CASES["owner"].seed} if role == "attacker" else
                          {"verdict": "PASS" if self.calls == 1 else "BLOCK", "reason": "Fixture"})
                return {"call_id": self.last_call_id, "answer": answer}

        manifest = run_campaign(root, [CASES["owner"]], attempts=1,
                                modes=("independent",), transport_factory=FailureTransport)
        rows = read_jsonl(root / "attempts.jsonl")
        report = json.loads((root / "report.json").read_text())
        self.assertEqual(manifest["status"], "interrupted")
        self.assertFalse(rows[-1]["success"])
        self.assertIsNone(report["modes"]["independent"]["success_rate"])
        self.assertEqual(report["modes"]["independent"]["complete_runs"], 0)
        self.assertTrue(verify_run(root)["ok"])
        return rows[-1], report

    def test_failed_generation_never_becomes_reviewer_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            row, report = self.run_failure(root, fail_on=3)
            self.assertEqual(row["failed_role"], "attacker")
            self.assertEqual(row["attacker"], {"status": "ERROR"})
            self.assertEqual(row["reviewer"], {"verdict": "NOT_CALLED"})
            self.assertEqual(row["attacker_failed_call"], "003-attacker")
            self.assertNotIn("reviewer_call", row)
            self.assertNotIn("reviewer_failed_call", row)
            self.assertNotIn("candidate", row)
            self.assertEqual((root / "calls/003-attacker/stderr.txt").read_text(),
                             "Fixture failure evidence")
            self.assertEqual(report["modes"]["independent"]["reviewed_target_candidates"], 0)

    def test_failed_review_retains_candidate_and_failed_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            row, report = self.run_failure(root, fail_on=4)
            self.assertEqual(row["failed_role"], "reviewer")
            self.assertEqual(row["reviewer"], {"verdict": "ERROR"})
            self.assertEqual(row["attacker_call"], "003-attacker")
            self.assertEqual(row["reviewer_failed_call"], "004-reviewer")
            self.assertNotIn("reviewer_call", row)
            self.assertTrue(row["oracle"]["target_regression"])
            self.assertTrue((root / row["candidate"]).is_file())
            self.assertEqual(report["modes"]["independent"]["reviewed_target_candidates"], 0)

    def test_budget_gate_does_not_attach_previous_call_as_failed_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, _ = self.run_failure(Path(tmp) / "run", fail_on=4, before_start=True)
            self.assertEqual(row["failed_role"], "reviewer")
            self.assertEqual(row["reviewer"], {"verdict": "NOT_CALLED"})
            self.assertNotIn("reviewer_failed_call", row)
            self.assertTrue(row["oracle"]["target_regression"])

    def test_control_failure_preserves_actual_reviewer_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, _ = self.run_failure(Path(tmp) / "run", fail_on=1)
            self.assertEqual(row["stage"], "control")
            self.assertEqual(row["reviewer"], {"verdict": "ERROR"})
            self.assertEqual(row["reviewer_failed_call"], "001-reviewer")

    def test_keyboard_interrupt_saves_generation_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, _ = self.run_failure(Path(tmp) / "run", fail_on=3, interrupt=True)
            self.assertEqual(row["outcome"], "interrupted")
            self.assertEqual(row["reviewer"], {"verdict": "NOT_CALLED"})
            self.assertEqual(row["attacker_failed_call"], "003-attacker")


if __name__ == "__main__":
    unittest.main()
