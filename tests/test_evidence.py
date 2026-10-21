import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from lab.evidence import build_evidence


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/replication-collision-medium-2026-10-19"
ATTEMPT = "owner-independent-r2-a1"


class EvidenceTests(unittest.TestCase):
    def test_bundle_replays_on_its_own_and_excludes_local_metadata(self):
        name, data = build_evidence(RUN, ATTEMPT)
        self.assertEqual(name, f"reviewer-lab-{ATTEMPT}.zip")
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(len(archive.namelist()), 9)
                self.assertNotIn("manifest.json", archive.namelist())
                self.assertFalse(any("calls/" in n or "request.json" in n for n in archive.namelist()))
                evidence = json.loads(archive.read("evidence.json"))
                self.assertNotIn("candidate_origin", evidence)
                self.assertTrue(evidence["confirmed_miss"])
                self.assertEqual(evidence["reviewer_call"]["requested_model"], "gpt-5.6-luna")
                archive.extractall(folder)
            result = subprocess.run([sys.executable, "replay.py"], cwd=folder,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["matches_saved_oracle"])
            Path(folder, "candidate.py").write_text("raise RuntimeError('should not execute')")
            result = subprocess.run([sys.executable, "replay.py"], cwd=folder,
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("File changed: candidate.py", result.stderr)

    def test_block_is_not_reported_as_confirmed_miss(self):
        _, data = build_evidence(RUN, "owner-independent-r1-a1")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            evidence = json.loads(archive.read("evidence.json"))
            self.assertEqual(evidence["reviewer"]["verdict"], "BLOCK")
            self.assertFalse(evidence["confirmed_miss"])

    def test_modified_source_or_evaluator_cannot_be_exported_as_original(self):
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "run"
            shutil.copytree(RUN, copied)
            candidate = copied / "attempts" / ATTEMPT / "candidate.py"
            original = candidate.read_bytes()
            candidate.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "Candidate hash"):
                build_evidence(copied, ATTEMPT)
            candidate.write_bytes(original)
            evaluator = copied / "source_snapshot/lab/oracle.py"
            evaluator.write_text("# replaced")
            with self.assertRaisesRegex(ValueError, "evaluator hash"):
                build_evidence(copied, ATTEMPT)

    def test_rejects_path_escape_and_snapshot_symlink(self):
        with self.assertRaisesRegex(ValueError, "Invalid attempt"):
            build_evidence(RUN, "../../manifest")
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "run"
            shutil.copytree(RUN, copied)
            target = copied / "source_snapshot/lab/oracle.py"
            target.unlink()
            target.symlink_to(RUN / "source_snapshot/lab/oracle.py")
            with self.assertRaisesRegex(ValueError, "leaves"):
                build_evidence(copied, ATTEMPT)

    def test_missing_artifact_does_not_disclose_local_path(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as error:
                build_evidence(Path(folder), ATTEMPT)
            self.assertNotIn(folder, str(error.exception))
            self.assertIn("missing or unreadable", str(error.exception))

    def test_replay_uses_the_saved_case_domain(self):
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "run"
            shutil.copytree(RUN, copied)
            cases = json.loads((copied / "cases.json").read_text())
            cases[0]["domains"][0] = ["owner", "editor"]
            (copied / "cases.json").write_text(json.dumps(cases))
            result_path = copied / "attempts" / ATTEMPT / "result.json"
            result = json.loads(result_path.read_text())
            result["oracle"]["rows"] = result["oracle"]["rows"][:4]
            result_path.write_text(json.dumps(result))
            _, data = build_evidence(copied, ATTEMPT)
            export = Path(folder) / "export"
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extractall(export)
            result = subprocess.run([sys.executable, "replay.py"], cwd=export,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(len(json.loads(result.stdout)["oracle"]["rows"]), 4)


if __name__ == "__main__":
    unittest.main()
