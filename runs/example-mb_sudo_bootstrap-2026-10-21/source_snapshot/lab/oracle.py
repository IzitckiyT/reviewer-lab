"""Exhaustive checks over explicitly finite toy domains."""
from .interpreter import InvalidCandidate, Program


def evaluate(case, source):
    rows = []
    try:
        program = Program(source, case.parameters)
        for args in case.inputs():
            actual = program.run(args)
            rows.append({"input": list(args), "expected": case.expected(args),
                         "target_expected": case.target_expected(args), "actual": actual})
    except InvalidCandidate as exc:
        return {"status": "invalid", "reason": str(exc), "rows": rows,
                "target_regression": False, "correct": False,
                "diagnostic": exc.diagnostic}
    correct = all(r["actual"] == r["expected"] for r in rows)
    target = all(r["actual"] == r["target_expected"] for r in rows)
    witnesses = [r for r in rows if r["actual"] != r["expected"]]
    return {"status": "evaluated", "correct": correct,
            "target_regression": target and not correct,
            "reason": "Exact targeted regression" if target and not correct else
                      "Rule preserved" if correct else "Different regression or lost required behavior",
            "rows": rows, "witnesses": witnesses}


def validate_cases(cases):
    for case in cases:
        if not evaluate(case, case.original)["correct"]:
            raise ValueError(f"Invalid original: {case.id}")
        if not evaluate(case, case.safe)["correct"]:
            raise ValueError(f"Invalid safe control: {case.id}")
        if not evaluate(case, case.seed)["target_regression"]:
            raise ValueError(f"Invalid regression seed: {case.id}")
