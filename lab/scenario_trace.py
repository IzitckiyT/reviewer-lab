"""Explain one canonical input using the same bounded candidate interpreter.

This is a local code trace, not another model opinion or a replay of external
side effects. Case specifications independently provide the expected decision.
"""
from hashlib import sha256

from .cases import CASES, Case
from .interpreter import Program


def explain_scenario(case, source, index):
    """Return an execution trace for a zero-based entry in case.inputs().

    Callers cannot supply arbitrary function arguments or change the rule. The
    source is validated in full, including branches this input does not execute.
    InvalidCandidate and ValueError intentionally propagate to the HTTP boundary.
    """
    if type(case) is str:
        if case not in CASES:
            raise ValueError("Неизвестная задача.")
        case = CASES[case]
    if not isinstance(case, Case):
        raise ValueError("Неизвестная задача.")
    inputs = case.inputs()
    if type(index) is not int or not 0 <= index < len(inputs):
        raise ValueError("Выберите существующую ситуацию из этой задачи.")
    args = inputs[index]
    expected = case.expected(args)
    program = Program(source, case.parameters)
    actual = program.run(args, trace=True)
    return {"case": case.id, "index": index,
            "input": dict(zip(case.parameters, args)), "input_values": list(args),
            "expected": expected, "actual": actual, "matches": expected == actual,
            "source_sha256": sha256(source.encode("utf-8")).hexdigest(),
            "events": program.trace_events, "truncated": program.trace_truncated,
            "steps": program.steps}
