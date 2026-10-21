import unittest

from lab.cases import CASES
from lab.interpreter import InvalidCandidate
from lab.oracle import evaluate


class CandidateDiagnosticTests(unittest.TestCase):
    def evaluate(self, source):
        result = evaluate(CASES["owner"], source)
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["target_regression"])
        self.assertFalse(result["correct"])
        return result

    def test_syntax_error_preserves_reason_and_points_to_actual_line(self):
        result = self.evaluate("def decision(role, approved):\n    if approved\n        return True\n")
        self.assertEqual(result["reason"], "Invalid Python syntax: SyntaxError")
        diagnostic = result["diagnostic"]
        self.assertEqual(diagnostic["kind"], "syntax")
        self.assertEqual(diagnostic["line"], 2)
        self.assertEqual(diagnostic["column"], 16)
        self.assertTrue("двоеточия" in diagnostic["message"] or
                        "синтаксис Python" in diagnostic["message"])
        self.assertEqual(result["rows"], [])

    def test_indentation_error_keeps_its_parser_location(self):
        result = self.evaluate("def decision(role, approved):\nreturn True\n")
        self.assertEqual(result["reason"], "Invalid Python syntax: IndentationError")
        self.assertEqual(result["diagnostic"]["line"], 2)
        self.assertIn("отступы", result["diagnostic"]["message"])

    def test_unsupported_constructs_have_specific_guidance_and_locations(self):
        cases = [
            ("import os", "Import", "Импорты"),
            ("while approved:\n        pass", "While", "Циклы"),
            ("return [x for x in role]", "ListComp", "Генераторы коллекций"),
            ("return role.upper", "Attribute", "Атрибуты"),
        ]
        for body, construct, guidance in cases:
            with self.subTest(construct=construct):
                result = self.evaluate(f"def decision(role, approved):\n    {body}\n")
                self.assertEqual(result["reason"], f"Unsupported syntax: {construct}")
                self.assertEqual(result["diagnostic"]["line"], 2)
                self.assertIn(guidance, result["diagnostic"]["message"])
                self.assertEqual(result["diagnostic"]["kind"], "unsupported")
                self.assertEqual(result["rows"], [])

    def test_ast_byte_column_is_converted_to_character_column(self):
        result = self.evaluate('def decision(role, approved):\n    return ("я", lambda: True)\n')
        self.assertEqual(result["reason"], "Unsupported syntax: Lambda")
        self.assertEqual(result["diagnostic"]["line"], 2)
        self.assertEqual(result["diagnostic"]["column"], 18)

    def test_nonbool_return_points_to_decision_return_not_helper(self):
        source = ("def number():\n    return 1\n\n"
                  "def decision(role, approved):\n    return number()\n")
        result = self.evaluate(source)
        self.assertEqual(result["reason"], "decision must return exactly bool")
        self.assertEqual(result["diagnostic"]["kind"], "return_type")
        self.assertEqual(result["diagnostic"]["line"], 5)
        self.assertEqual(result["diagnostic"]["column"], 5)
        self.assertIn("int", result["diagnostic"]["message"])
        self.assertIn("True или False", result["diagnostic"]["message"])

    def test_missing_return_does_not_invent_a_return_location(self):
        result = self.evaluate("def decision(role, approved):\n    pass\n")
        self.assertEqual(result["diagnostic"]["kind"], "return_type")
        self.assertIsNone(result["diagnostic"]["line"])
        self.assertIsNone(result["diagnostic"]["column"])
        self.assertIn("NoneType", result["diagnostic"]["message"])

    def test_utf8_source_limit_describes_bytes_not_characters(self):
        source = CASES["owner"].original + "#" + "я" * 4000
        self.assertLess(len(source), 8000)
        result = self.evaluate(source)
        self.assertEqual(result["reason"], "Source must be <=8000 UTF-8 bytes")
        self.assertEqual(result["diagnostic"]["kind"], "size")
        self.assertIn("8000 байт UTF-8", result["diagnostic"]["message"])
        self.assertIsNone(result["diagnostic"]["line"])
        self.assertIsNone(result["diagnostic"]["column"])

    def test_unknown_error_diagnostic_preserves_original_reason(self):
        error = InvalidCandidate("A new bounded-interpreter error")
        self.assertEqual(error.diagnostic, {
            "message": str(error), "line": None, "column": None, "kind": "invalid"})

    def test_runtime_failure_does_not_invent_location(self):
        result = self.evaluate("def decision(role, approved):\n    return bool(1 // 0)\n")
        self.assertEqual(result["reason"], "Candidate runtime error: ZeroDivisionError")
        self.assertEqual(result["diagnostic"]["message"], result["reason"])
        self.assertIsNone(result["diagnostic"]["line"])
        self.assertIsNone(result["diagnostic"]["column"])

    def test_valid_results_keep_existing_shape(self):
        for case in CASES.values():
            for source in (case.original, case.safe, case.seed):
                with self.subTest(case=case.id, source=source):
                    result = evaluate(case, source)
                    self.assertEqual(result["status"], "evaluated")
                    self.assertNotIn("diagnostic", result)
                    self.assertEqual(set(result), {"status", "correct", "target_regression",
                                                  "reason", "rows", "witnesses"})


if __name__ == "__main__":
    unittest.main()
