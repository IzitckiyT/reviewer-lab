#!/usr/bin/env python3
"""Run from any directory: python3 /absolute/path/reviewer-lab/run.py --help."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info < (3, 10):
    print("Нужен Python 3.10 или новее. На macOS можно открыть Start Reviewer Lab.command.", file=sys.stderr)
    raise SystemExit(2)

from lab.cases import CASES
from lab.engine import run_campaign
from lab.oracle import evaluate, validate_cases
from lab.report import build_report
from lab.transport import CodexTransport, TransportError
from lab.providers import catalog, create_transport, models_for
from lab.verify import verify_run

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["start"]
    parser = argparse.ArgumentParser(description="Reviewer Lab: проверка AI-ревьюера. Без аргументов — демо без моделей.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Открыть демо без моделей; --live включает новые обращения к Codex")
    start.add_argument("--live", action="store_true", help="Явно разрешить новые обращения к моделям и расход квоты")
    start.add_argument("--port", type=int, default=None, help="Порт; по умолчанию 8765 или свободный, если 8765 занят")
    start.add_argument("--no-open", action="store_true", help="Напечатать адрес без открытия браузера")
    start.add_argument("--provider", choices=("codex", "claude", "deepseek"), default="codex")
    commands.add_parser("check", help="Offline validation of all custom case fixtures")
    doctor = commands.add_parser("doctor", help="Проверить подключение провайдера без обращения к модели")
    doctor.add_argument("--provider", choices=("codex", "claude", "deepseek"), default="codex")
    commands.add_parser("providers", help="Показать поддерживаемые провайдеры и настройку")
    commands.add_parser("cases", help="List custom cases")
    ui = commands.add_parser("ui", help="Local dashboard and presentation replay; no model calls on opening")
    ui.add_argument("--port", type=int, default=None, help="Listen port (REVIEWER_LAB_PORT, default 8765)")
    ui.add_argument("--bind", default=None, help="IPv4 interface (REVIEWER_LAB_BIND, default 127.0.0.1)")
    ui.add_argument("--demo", action="store_true", help="Read-only demo with local tests; model launches disabled")
    ui.add_argument("--public-origin", action="append", default=None,
                    help="Exact browser origin, repeat for aliases; overrides REVIEWER_LAB_PUBLIC_ORIGINS")
    ui.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
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
    run.add_argument("--provider", choices=("codex", "claude", "deepseek"), default="codex")
    run.add_argument("--reviewer-model", help="Идентификатор модели ревьюера у выбранного провайдера")
    run.add_argument("--attacker-model", help="Идентификатор модели генератора у выбранного провайдера")
    run.add_argument("--candidate-files", nargs="+", type=Path,
                     help="Screen existing candidates without calling the attacker; one case and independent mode required")
    run.add_argument("--seed-candidate", action="store_true", help="Проверить готовый дефект выбранной задачи без генератора")
    manual = commands.add_parser("manual", help="Append a human-authored attempt; model calls only for a target regression")
    manual.add_argument("--case", choices=CASES, default="owner")
    manual.add_argument("--source", type=str, required=True, help="Complete candidate Python file, or - for stdin")
    manual.add_argument("--out", type=Path, required=True, help="New or existing manual series directory")
    manual.add_argument("--timeout", type=int, default=600)
    manual.add_argument("--token-limit", type=int, default=300_000)
    manual.add_argument("--provider", choices=("codex", "claude", "deepseek"), default=None,
                        help="Провайдер новой серии; существующая серия сохраняет своё подключение")
    manual.add_argument("--reviewer-model", help="Модель ревьюера новой серии")
    report = commands.add_parser("report", help="Rebuild an existing run report without model calls")
    report.add_argument("run_dir", type=Path)
    verify = commands.add_parser("verify", help="Offline audit of saved traces, context isolation and oracle")
    verify.add_argument("run_dir", type=Path)
    replay = commands.add_parser("replay", help="Recheck saved candidate against the custom oracle, offline")
    replay.add_argument("case", choices=CASES)
    replay.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            from lab.ui import serve
            configured_port = os.environ.get("REVIEWER_LAB_PORT")
            port = args.port if args.port is not None else int(configured_port or "8765")
            if not 0 <= port <= 65535:
                raise ValueError("Порт должен быть от 0 до 65535")
            serve(ROOT, port, not args.no_open, read_only=not args.live,
                  fallback_port=args.port is None and not configured_port,
                  start_path="#launch" if args.live else "#demo", provider=args.provider)
        elif args.command == "ui":
            from lab.ui import serve
            port = args.port if args.port is not None else int(os.environ.get("REVIEWER_LAB_PORT", "8765"))
            demo_env = os.environ.get("REVIEWER_LAB_DEMO", "0").lower()
            if demo_env not in ("0", "1", "false", "true"):
                raise ValueError("REVIEWER_LAB_DEMO must be 0, 1, false or true")
            read_only = args.demo or demo_env in ("1", "true")
            origins_env = os.environ.get("REVIEWER_LAB_PUBLIC_ORIGINS", "")
            origins = args.public_origin if args.public_origin is not None else (
                [origin.strip() for origin in origins_env.split(",")] if origins_env else None)
            if not 0 <= port <= 65535:
                parser.error("port must be 0..65535")
            serve(ROOT, port, args.open, bind=args.bind or os.environ.get("REVIEWER_LAB_BIND", "127.0.0.1"),
                  read_only=read_only, public_origins=origins)
        elif args.command == "check":
            validate_cases(CASES.values())
            print("All original/safe/seed fixtures verified on every input in their finite domains.")
        elif args.command == "cases":
            for case in CASES.values():
                print(f"{case.id}: {case.title} ({len(case.inputs())} inputs)")
        elif args.command == "doctor":
            print(json.dumps(create_transport(ROOT, provider=args.provider).doctor(), indent=2, ensure_ascii=False))
        elif args.command == "providers":
            print(json.dumps(catalog(), indent=2, ensure_ascii=False))
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
        elif args.command == "manual":
            from lab.manual import MAX_SOURCE_BYTES, run_manual_attempt
            if args.source == "-":
                source = sys.stdin.read(MAX_SOURCE_BYTES + 1)
            else:
                with Path(args.source).open(encoding="utf-8") as code:
                    source = code.read(MAX_SOURCE_BYTES + 1)
            result = run_manual_attempt(args.out, CASES[args.case], source,
                                        timeout=args.timeout, token_limit=args.token_limit,
                                        provider=args.provider, reviewer_model=args.reviewer_model)
            print(f"Manual series {result['status']}: {args.out.resolve() / 'report.html'}")
            return 0 if result["status"] == "completed" else 2
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
                candidate_files=args.candidate_files, provider=args.provider,
                reviewer_model=args.reviewer_model, attacker_model=args.attacker_model,
                seed_candidate=args.seed_candidate)
            print(f"Run {result['status']}: {out.resolve() / 'report.html'}")
            return 0 if result["status"] == "completed" else 2
    except subprocess.TimeoutExpired:
        print("Проверка подключения не ответила за отведённое время. "
              "Повторите doctor --provider <провайдер>. Интерфейс: python3 run.py", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nЗапуск остановлен.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
