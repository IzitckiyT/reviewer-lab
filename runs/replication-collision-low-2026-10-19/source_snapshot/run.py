#!/usr/bin/env python3
"""Run from any directory: python3 /absolute/path/reviewer-lab/run.py --help."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from lab.cases import CASES
from lab.engine import run_campaign
from lab.oracle import evaluate, validate_cases
from lab.report import build_report
from lab.transport import CodexTransport, TransportError
from lab.verify import verify_run

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Custom reviewer experiment using your Codex subscription")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Offline validation of all custom case fixtures")
    commands.add_parser("doctor", help="Check CLI and ChatGPT login; no inference")
    commands.add_parser("cases", help="List custom cases")
    run = commands.add_parser("run", help="LIVE run: consumes ChatGPT/Codex quota")
    run.add_argument("--cases", nargs="+", choices=[*CASES, "all"], default=["owner"])
    run.add_argument("--modes", nargs="+", choices=["independent", "feedback"],
                     default=["feedback"])
    run.add_argument("--attempts", type=int, choices=range(1, 5), default=2)
    run.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    run.add_argument("--schedule-seed", type=int, default=7)
    run.add_argument("--timeout", type=int, default=600)
    run.add_argument("--token-limit", type=int, default=300_000)
    run.add_argument("--out", type=Path)
    run.add_argument("--reviewer-effort", choices=["low", "medium"], default="medium")
    run.add_argument("--strategy", choices=["original", "guided"], default="original")
    run.add_argument("--candidate-files", nargs="+", type=Path,
                     help="Screen existing candidates without calling the attacker; one case and independent mode required")
    report = commands.add_parser("report", help="Rebuild an existing run report without model calls")
    report.add_argument("run_dir", type=Path)
    verify = commands.add_parser("verify", help="Offline audit of saved traces, context isolation and oracle")
    verify.add_argument("run_dir", type=Path)
    replay = commands.add_parser("replay", help="Recheck saved candidate against the custom oracle, offline")
    replay.add_argument("case", choices=CASES)
    replay.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            validate_cases(CASES.values())
            print("All original/safe/seed fixtures verified on every input in their finite domains.")
        elif args.command == "cases":
            for case in CASES.values():
                print(f"{case.id}: {case.title} ({len(case.inputs())} inputs)")
        elif args.command == "doctor":
            print(json.dumps(CodexTransport(ROOT).doctor(), indent=2, ensure_ascii=False))
        elif args.command == "report":
            print(json.dumps(build_report(args.run_dir), indent=2, ensure_ascii=False))
        elif args.command == "verify":
            result = verify_run(args.run_dir)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["ok"] else 1
        elif args.command == "replay":
            result = evaluate(CASES[args.case], args.source.read_text())
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["target_regression"] else 1
        else:
            if args.timeout < 10 or args.token_limit < 1:
                parser.error("timeout must be >=10 seconds and token-limit positive")
            names = list(CASES) if args.cases == ["all"] else args.cases
            if "all" in names or len(set(names)) != len(names):
                parser.error("Use --cases all alone, or a list of distinct cases")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            out = args.out or ROOT / "runs" / stamp
            result = run_campaign(out, [CASES[n] for n in names], attempts=args.attempts,
                repeats=args.repeats, modes=tuple(args.modes), schedule_seed=args.schedule_seed,
                timeout=args.timeout, token_limit=args.token_limit,
                reviewer_effort=args.reviewer_effort, strategy=args.strategy,
                candidate_files=args.candidate_files)
            print(f"Run {result['status']}: {out.resolve() / 'report.html'}")
            return 0 if result["status"] == "completed" else 2
    except (ValueError, OSError, TransportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
