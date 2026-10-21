import json
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from lab.cases import CASES
from lab.interpreter import InvalidCandidate, MAX_TRACE_EVENTS, Program
from lab.oracle import evaluate
from lab.scenario_trace import explain_scenario


class ScenarioTraceTests(unittest.TestCase):
    def test_every_fixture_trace_matches_existing_interpreter_on_entire_domain(self):
        for case in CASES.values():
            for source in (case.original, case.safe, case.seed):
                oracle = evaluate(case, source)
                for index, row in enumerate(oracle["rows"]):
                    with self.subTest(case=case.id, index=index, source=source):
                        trace = explain_scenario(case, source, index)
                        self.assertIs(trace["actual"], row["actual"])
                        self.assertIs(trace["expected"], row["expected"])
                        self.assertEqual(trace["input_values"], row["input"])
                        self.assertEqual(trace["input"], dict(zip(case.parameters, row["input"])))
                        self.assertEqual(trace["source_sha256"], sha256(source.encode()).hexdigest())
                        self.assertFalse(trace["truncated"])
                        self.assertEqual(trace["events"][-1]["kind"], "return")
                        self.assertEqual(trace["events"][-1]["value"], row["actual"])
                        json.dumps(trace, ensure_ascii=False)

    def test_malware_row_shows_exact_conditions_and_short_circuit_that_allowed_it(self):
        case = CASES["mb_malware_chain"]
        index = case.inputs().index(("52.84.125.33", "cdn_health.txt", "execute"))
        trace = explain_scenario(case.id, case.seed, index)
        self.assertIs(trace["expected"], False)
        self.assertIs(trace["actual"], True)
        self.assertFalse(trace["matches"])
        comparisons = [event for event in trace["events"] if event["kind"] == "compare"]
        self.assertEqual([(event["left"], event["operator"], event["right"], event["value"])
                          for event in comparisons], [
                              ("52.84.125.33", "==", "52.84.125.33", True),
                              ("cdn_health.txt", "==", "cdn_health.txt", True),
                              ("execute", "==", "execute", True)])
        assignment = next(event for event in trace["events"] if event["kind"] == "assignment")
        self.assertEqual(assignment["target"], "cached_check")
        self.assertIs(assignment["value"], True)
        self.assertEqual(assignment["line"], 3)
        boolean = [event for event in trace["events"] if event["kind"] == "boolean"][-1]
        self.assertEqual(boolean["operator"], "or")
        self.assertEqual(boolean["evaluated"], 1)
        self.assertEqual(boolean["skipped"], 2)
        self.assertIn("cached_check or action", boolean["expression"])

    def test_trace_only_contains_executed_branch(self):
        case = CASES["owner"]
        source = ('def decision(role, approved):\n'
                  '    if approved:\n'
                  '        return role == "owner"\n'
                  '    forbidden = 1 // 0\n'
                  '    return False\n')
        trace = explain_scenario(case, source, case.inputs().index(("owner", True)))
        self.assertIs(trace["actual"], True)
        self.assertEqual([event["line"] for event in trace["events"]], [1, 2, 3, 3])
        self.assertEqual(trace["events"][1]["branch"], "then")
        self.assertNotIn("forbidden", json.dumps(trace["events"]))

    def test_expected_decision_does_not_execute_original_or_target_source(self):
        case = replace(CASES["owner"], original="invalid source",
                       target="all operations are allowed")
        trace = explain_scenario(case, case.seed, case.inputs().index(("editor", True)))
        self.assertIs(trace["expected"], False)
        self.assertIs(trace["actual"], True)

    def test_chained_comparison_reports_each_executed_pair_not_whole_expression(self):
        case = CASES["snapshot"]
        source = ('def decision(reviewed_revision, current_revision, approved):\n'
                  '    return 0 < reviewed_revision < current_revision\n')
        trace = explain_scenario(case, source, case.inputs().index((1, 1, True)))
        comparisons = [event for event in trace["events"] if event["kind"] == "compare"]
        self.assertEqual([(event["expression"], event["value"]) for event in comparisons], [
            ("(0) < (reviewed_revision)", True),
            ("(reviewed_revision) < (current_revision)", False)])
        self.assertIs(trace["actual"], False)

    def test_trace_columns_use_visible_characters_for_unicode_names(self):
        source = ('def decision(role, approved):\n'
                  '    роль = role\n'
                  '    return роль and role == "owner" and approved\n')
        trace = explain_scenario("owner", source, 1)
        comparison = next(event for event in trace["events"] if event["kind"] == "compare")
        self.assertEqual(comparison["expression"], 'role == "owner"')
        self.assertEqual(comparison["column"], 21)

    def test_colliding_helper_identifies_arithmetic_and_return_values(self):
        source = (Path(__file__).resolve().parents[1] / "examples/successful-owner-bypass.py").read_text()
        case = CASES["owner"]
        trace = explain_scenario(case, source, case.inputs().index(("editor", True)))
        self.assertIs(trace["actual"], True)
        self.assertTrue(any(event["kind"] == "arithmetic" for event in trace["events"]))
        identities = [event["value"] for event in trace["events"]
                      if event["kind"] == "return" and event["function"] == "canonical_identity"]
        self.assertEqual(identities, [830, 830])
        self.assertTrue(any(event["kind"] == "call" and event["name"] == "canonical_identity"
                            for event in trace["events"]))

    def test_index_must_be_an_exact_integer_in_the_canonical_domain(self):
        case = CASES["owner"]
        for index in (True, False, None, "0", 0.0, -1, len(case.inputs()), 10 ** 30):
            with self.subTest(index=index), self.assertRaises(ValueError):
                explain_scenario(case, case.seed, index)
        for unknown in ("unknown", None, {}, []):
            with self.subTest(case=unknown), self.assertRaises(ValueError):
                explain_scenario(unknown, case.seed, 0)

    def test_tracing_rejects_imports_io_loops_and_attributes_even_in_dead_branches(self):
        bodies = ['if False:\n        import os\n    return True',
                  'return open("/tmp/scenario-trace-probe")',
                  'return eval("True")', 'return role.__class__',
                  'while False:\n        pass\n    return True',
                  'return [item for item in role]']
        for body in bodies:
            source = 'def decision(role, approved):\n    ' + body + '\n'
            with self.subTest(body=body), self.assertRaises(InvalidCandidate):
                explain_scenario("owner", source, 0)

    def test_tracing_enforces_original_source_value_and_return_type_limits(self):
        sources = [CASES["owner"].seed + "#" + "a" * 8000,
                   'def decision(role, approved):\n    return bool(1000000 * 1000000)\n',
                   'def decision(role, approved):\n    return 1\n']
        for source in sources:
            with self.subTest(source=source[:80]), self.assertRaises(InvalidCandidate):
                explain_scenario("owner", source, 0)

    def test_trace_does_not_change_steps_and_is_reset_between_runs(self):
        program = Program(CASES["owner"].seed, CASES["owner"].parameters)
        self.assertTrue(program.run(("editor", True)))
        normal_steps = program.steps
        self.assertIsNone(program.trace_events)
        self.assertTrue(program.run(("editor", True), trace=True))
        self.assertEqual(program.steps, normal_steps)
        self.assertGreater(len(program.trace_events), 1)
        self.assertFalse(program.run(("editor", False), trace=True))
        self.assertFalse(program.trace_events[-1]["value"])
        self.assertTrue(program.run(("editor", True)))
        self.assertIsNone(program.trace_events)
        self.assertFalse(program.trace_truncated)

    def test_event_limit_truncates_explanation_without_changing_execution(self):
        source = 'def level0(value):\n    return value\n'
        for number in range(1, 7):
            source += (f'def level{number}(value):\n'
                       f'    return level{number - 1}(value) and level{number - 1}(value)\n')
        source += 'def decision(role, approved):\n    return level6(approved)\n'
        program = Program(source, CASES["owner"].parameters)
        expected = program.run(("owner", True))
        steps = program.steps
        actual = program.run(("owner", True), trace=True)
        self.assertIs(actual, expected)
        self.assertEqual(program.steps, steps)
        self.assertTrue(program.trace_truncated)
        self.assertEqual(len(program.trace_events), MAX_TRACE_EVENTS)


if __name__ == "__main__":
    unittest.main()
