"""Local dashboard: read saved evidence and supervise bounded CLI campaigns."""
import errno
import json
import ipaddress
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .cases import CASES, Case
from .oracle import evaluate
from .manual import check_source, load_series, validate_source
from .report import summarize
from .evidence import build_evidence
from .providers import catalog, checked_status, create_transport, models_for, setup_status
from .transport import TransportError, redact

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,160}\Z")
FEATURED = "replication-collision-medium-2026-10-19"
LABELS = {
    "live-smoke-2026-10-21": "Проверка запуска · 21 октября",
    FEATURED: "Повторения · Luna medium",
    "replication-collision-low-2026-10-19": "Повторения · Luna low",
    "discovery-old-candidates-low-2026-10-19": "Первый успешный пропуск",
    "first-experiment-2026-10-19": "Первая генерация · Sol xhigh",
}
DEMO_MESSAGE = ("Демо: новые обращения к модели отключены. "
                "Доступны сохранённые результаты и локальные тесты.")


def validate_binding(bind, read_only):
    """Use explicit IPv4 interfaces; network-facing serving is demo-only."""
    try:
        address = ipaddress.IPv4Address(bind)
    except (ValueError, TypeError) as exc:
        raise ValueError("bind must be an IPv4 address, e.g. 127.0.0.1 or 0.0.0.0") from exc
    if not address.is_loopback and not read_only:
        raise ValueError("A non-loopback bind requires --demo; live model access is local-only")


def browser_origins(origins, port):
    """An explicit list replaces defaults, including the external mapped port."""
    values = set(origins) if origins else {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    for value in values:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    parsed.path or parsed.query or parsed.fragment or
                    any(c.isspace() for c in value) or "*" in value or
                    value != f"{parsed.scheme}://{parsed.netloc}" or
                    (parsed.port is not None and not 1 <= parsed.port <= 65535)):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("public origin must be an exact http(s) origin without a path or wildcard") from exc
    return values


def read_text(path, limit=1_500_000):
    try:
        with path.open(encoding="utf-8") as file:
            return file.read(limit)
    except FileNotFoundError:
        return ""


def read_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (ValueError, UnicodeError):
        # A running process can be between truncating and writing its JSON file.
        return default


def journal(path):
    rows = []
    for line in read_text(path, 8_000_000).splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue  # Includes an unfinished last line during live polling.
    return rows


def inside(root, *parts):
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path leaves the project")
    return target


class Dashboard:
    def __init__(self, root, read_only=False, provider="codex"):
        self.root = Path(root).resolve()
        self.runs = self.root / "runs"
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.process = None
        self.active_id = None
        self.stop_requested = False
        self.exit_code = None
        self.read_only = read_only
        self.selected_provider = provider
        self.live_available = True
        self.provider_checks = {}
        self.provider_check_lock = threading.Lock()

    def providers(self):
        result = []
        for entry in catalog():
            status = setup_status(entry["id"])
            cached = self.provider_checks.get(entry["id"])
            if cached and cached["available"] == status["available"]:
                status = cached
            models = models_for(entry["id"])
            result.append({**entry, **status, "id": entry["id"],
                           "default_models": {role: value[0] for role, value in models.items()},
                           "transport": entry.get("kind", entry["id"])})
        return {"providers": result, "selected_provider": self.selected_provider,
                "live_available": self.live_available}

    def check_provider(self, config):
        """Explicit connection check; never called by normal dashboard polling."""
        if not self.live_available:
            raise PermissionError("Проверка подключения доступна при локальном запуске проекта.")
        if not isinstance(config, dict) or set(config) != {"provider"}:
            raise ValueError("Выберите провайдера")
        provider = config["provider"]
        models_for(provider)
        with self.provider_check_lock:
            try:
                transport = create_transport(self.root, provider=provider)
                readiness = transport.doctor(check_models=False) if provider == "codex" else transport.doctor()
                status = checked_status(provider, readiness=readiness)
                if provider == "codex":
                    try:
                        models = [model["slug"] for model in transport.model_catalog()
                                  if model.get("visibility", "list") == "list" and
                                  isinstance(model.get("slug"), str) and
                                  re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model["slug"])]
                        if models:
                            status.update(models=list(dict.fromkeys(models)), models_source="codex_cli_catalog",
                                          models_note="Список прочитан из установленного Codex CLI. Доступ к выбранной модели проверится при запуске.")
                    except (TransportError, OSError, subprocess.TimeoutExpired):
                        status["models_note"] = "Вход подтверждён, но список моделей прочитать не удалось. Можно указать ID вручную."
            except (TransportError, OSError, subprocess.TimeoutExpired) as exc:
                status = checked_status(provider, error=exc)
            self.provider_checks[provider] = status
        return self.providers()

    def enable_live(self, config):
        if not self.live_available:
            raise PermissionError("Эксперименты доступны при локальном запуске проекта. "
                                  "Запустите Start Experiment.command на своём компьютере.")
        if not isinstance(config, dict) or set(config) != {"provider"}:
            raise ValueError("Выберите провайдера")
        provider = config["provider"]
        models_for(provider)  # Validate the selection before inspecting authentication.
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("Дождитесь завершения текущего эксперимента")
        try:
            transport = create_transport(self.root, provider=provider)
            readiness = transport.doctor(check_models=False) if provider == "codex" else transport.doctor()
        except (TransportError, OSError, subprocess.TimeoutExpired) as exc:
            self.provider_checks[provider] = checked_status(provider, error=exc)
            raise RuntimeError(redact(str(exc))) from exc
        status = checked_status(provider, readiness=readiness)
        previous = self.provider_checks.get(provider, {})
        for key in ("models", "models_note", "models_source"):
            if key in previous:
                status[key] = previous[key]
        self.provider_checks[provider] = status
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("Дождитесь завершения текущего эксперимента")
            self.selected_provider = provider
            self.read_only = False
        return {"read_only": False, "provider": provider, "readiness": readiness}

    def model_options(self, config, *, manual=False):
        provider = config.get("provider", self.selected_provider)
        reviewer = config.get("reviewer_model") or None
        attacker = config.get("attacker_model") or None
        models_for(provider, reviewer, attacker, config.get("effort", "medium"))
        args = ["--provider", provider]
        if reviewer is not None:
            args.extend(["--reviewer-model", reviewer])
        if attacker is not None and not manual:
            args.extend(["--attacker-model", attacker])
        return args

    def require_live(self):
        if self.read_only:
            raise PermissionError(DEMO_MESSAGE)

    def run_path(self, run_id):
        if not NAME.fullmatch(run_id):
            raise ValueError("Invalid run identifier")
        path = inside(self.runs, run_id)
        if not path.is_dir():
            raise FileNotFoundError(run_id)
        return path

    def rows(self, path, pending=False):
        rows = journal(path / "attempts.jsonl")
        if pending:
            known = {r["id"] for r in rows}
            for folder in sorted((path / "attempts").glob("*")):
                if not folder.is_dir() or folder.name in known:
                    continue
                row = read_json(folder / "started.json")
                if row:
                    row.update(outcome="running", success=False,
                               reviewer={"verdict": "NOT_CALLED"})
                    oracle = read_json(folder / "oracle.json")
                    if oracle:
                        row["oracle"] = oracle
                    rows.append(row)
        return rows

    def list_runs(self):
        found = []
        for path in self.runs.glob("*"):
            if not path.is_dir() or not NAME.fullmatch(path.name):
                continue
            manifest = read_json(inside(self.runs, path.name, "manifest.json"))
            if not manifest:
                continue
            rows = self.rows(path)
            label = LABELS.get(path.name, path.name)
            if manifest.get("experiment_kind") == "manual":
                case = CASES.get(next(iter(manifest.get("cases", [])), ""))
                label = "Ручная серия" + (f" · {case.title}" if case else "")
            found.append({"id": path.name, "label": label,
                "execution_state": self.execution_state(path.name, manifest),
                "manifest": manifest,
                "summary": summarize(manifest, rows, journal(path / "calls.jsonl"))})
        return sorted(found, key=lambda r: r["manifest"].get("created_at", ""), reverse=True)

    def detail(self, run_id):
        path = self.run_path(run_id)
        manifest = read_json(path / "manifest.json")
        if not manifest:
            return {"id": run_id, "pending": True}
        execution_state = self.execution_state(run_id, manifest)
        rows = self.rows(path, pending=True)
        for row in rows:
            if row["outcome"] == "running":
                row["execution_state"] = execution_state
            folder = inside(path, "attempts", row["id"])
            row["source"] = read_text(folder / "candidate.py", 20_000)
            row["diff"] = read_text(folder / "candidate.diff", 30_000)
        calls = list({c["id"]: c for c in journal(path / "calls.jsonl")}.values())
        for call in calls:
            folder = inside(path, "calls", call["id"])
            events = journal(folder / "events.jsonl")
            call["events_seen"] = len(events)
            call["last_event"] = events[-1].get("type") if events else None
        return {"id": run_id, "manifest": manifest, "rows": rows, "calls": calls,
                "execution_state": execution_state,
                "cases": read_json(path / "cases.json", []),
                "summary": summarize(manifest, [r for r in rows if r["outcome"] != "running"],
                                     journal(path / "calls.jsonl")),
                "console": read_text(self.runs / ".ui-logs" / (run_id + ".log"), 100_000)}

    def call_detail(self, run_id, call_id):
        if not NAME.fullmatch(call_id):
            raise ValueError("Invalid call identifier")
        path = inside(self.run_path(run_id), "calls", call_id)
        if not path.is_dir():
            raise FileNotFoundError(call_id)
        return {"id": call_id, "request": read_json(path / "request.json"),
                "record": read_json(path / "call.json"),
                "prompt": read_text(path / "prompt.txt"),
                "instructions": read_text(path / "instructions.txt"),
                "answer": read_json(path / "answer.json"),
                "events": read_text(path / "events.jsonl"),
                "stderr": read_text(path / "stderr.txt")}

    def active(self):
        with self.lock:
            if self.process:
                code = self.process.poll()
                if code is not None:
                    self.exit_code = code
            return {"id": self.active_id,
                    "running": self.process is not None and self.process.poll() is None,
                    "stop_requested": self.stop_requested, "exit_code": self.exit_code}

    def execution_state(self, run_id, manifest):
        """Expose observed liveness without guessing about another CLI controller."""
        with self.lock:
            if self.active_id == run_id and self.process is not None:
                if self.process.poll() is None:
                    return "owned_running"
                if manifest.get("status") == "running":
                    return "interrupted"
            return "unobserved" if manifest.get("status") == "running" else "finished"

    def launch(self, config):
        self.require_live()
        required = {"preset", "case", "effort", "attempts", "repeats"}
        if (not isinstance(config, dict) or not required <= set(config) or
                set(config) - required - {"provider", "reviewer_model", "attacker_model"}):
            raise ValueError("Invalid run settings")
        preset, case, effort = config["preset"], config["case"], config["effort"]
        attempts, repeats = config["attempts"], config["repeats"]
        if preset not in ("screen", "feedback", "compare") or case not in CASES or effort not in ("low", "medium"):
            raise ValueError("Unknown experiment profile")
        if type(attempts) is not int or not 1 <= attempts <= 4 or type(repeats) is not int or not 1 <= repeats <= 3:
            raise ValueError("Invalid attempt or repeat count")
        if preset == "screen" and attempts != 1:
            raise ValueError("Готовый пример требует одну попытку")
        if preset == "compare" and attempts != 4:
            raise ValueError("Comparison requires four attempts per mode")
        run_id = "ui-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args = [sys.executable, "-u", str(self.root / "run.py"), "run", "--cases", case,
                "--reviewer-effort", effort, "--attempts", str(attempts), "--repeats", str(repeats),
                "--out", str(self.runs / run_id)]
        args.extend(self.model_options(config))
        args.append("--modes")
        args.extend(["independent", "feedback"] if preset == "compare" else
                    ["independent"] if preset == "screen" else ["feedback"])
        if preset == "screen":
            if case == "owner":
                candidate = self.root / "examples" / "successful-owner-bypass.py"
                if not candidate.is_file():
                    raise FileNotFoundError("Saved candidate is missing")
                args.extend(["--candidate-files", str(candidate)])
            else:
                args.append("--seed-candidate")
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("An experiment is already running")
            logs = self.runs / ".ui-logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / (run_id + ".log")).open("w", encoding="utf-8") as output:
                process = subprocess.Popen(args, cwd=self.root, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            self.process = process
            self.active_id = run_id
            self.stop_requested = False
            self.exit_code = None
        return {"id": run_id}

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                # Engine catches KeyboardInterrupt, kills its model process group and saves partial results.
                self.process.send_signal(signal.SIGINT)
                self.stop_requested = True
        return self.active()

    def launch_manual(self, config):
        self.require_live()
        required = {"case", "source", "run_id"}
        if (not isinstance(config, dict) or not required <= set(config) or
                set(config) - required - {"provider", "reviewer_model"}):
            raise ValueError("Invalid manual attempt")
        case, source, run_id = config["case"], config["source"], config["run_id"]
        validate_source(case, source)
        if run_id is not None and (not isinstance(run_id, str) or not NAME.fullmatch(run_id)):
            raise ValueError("Invalid manual series identifier")
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("An experiment is already running")
            if run_id is not None:
                try:
                    manifest, _ = load_series(self.run_path(run_id), case)
                except FileNotFoundError as exc:
                    raise ValueError("Manual series does not exist") from exc
                provider = manifest.get("provider", "codex")
                model = manifest["models"]["reviewer"][0]
                if config.get("provider", provider) != provider or config.get("reviewer_model", model) != model:
                    raise ValueError("Подключение серии закреплено. Для другой модели начните новую серию.")
                options = self.model_options({"provider": provider, "reviewer_model": model}, manual=True)
            else:
                run_id = "manual-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                options = self.model_options(config, manual=True)
            args = [sys.executable, "-u", str(self.root / "run.py"), "manual",
                    "--case", case, "--source", "-", "--out", str(inside(self.runs, run_id))]
            args.extend(options)
            logs = self.runs / ".ui-logs"
            logs.mkdir(parents=True, exist_ok=True)
            # An inherited file descriptor avoids shell interpolation, argument-size limits
            # and orphaned submission files, while leaving the HTTP handler non-blocking.
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as code_input:
                code_input.write(source)
                code_input.seek(0)
                with (logs / (run_id + ".log")).open("a", encoding="utf-8") as output:
                    process = subprocess.Popen(args, cwd=self.root, stdin=code_input, stdout=output,
                                               stderr=subprocess.STDOUT, start_new_session=True)
            self.process = process
            self.active_id = run_id
            self.stop_requested = False
            self.exit_code = None
        return {"id": run_id}


def make_server(root, port=8765, dashboard=None, *, bind="127.0.0.1", read_only=False,
                public_origins=None, provider="codex"):
    app = dashboard or Dashboard(root, read_only=read_only, provider=provider)
    if read_only:
        app.read_only = True
    validate_binding(bind, app.read_only)
    app.live_available = ipaddress.IPv4Address(bind).is_loopback
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be 0..65535")
    # Validate before acquiring a socket; port 0 defaults resolve after binding.
    if public_origins:
        browser_origins(public_origins, port)
    static = Path(__file__).resolve().parent.parent / "ui"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def valid_host(self):
            return self.headers.get_all("Host", []) in [[host] for host in self.server.allowed_hosts]

        def send(self, status, value, mime="application/json; charset=utf-8", filename=None):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Local host required"})
            path = urlsplit(self.path).path
            try:
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/case-copy.js": ("case-copy.js", "text/javascript; charset=utf-8"),
                          "/test-explanation.js": ("test-explanation.js", "text/javascript; charset=utf-8"),
                          "/provider-setup.js": ("provider-setup.js", "text/javascript; charset=utf-8"),
                          "/run-story.js": ("run-story.js", "text/javascript; charset=utf-8"),
                          "/run-story.css": ("run-story.css", "text/css; charset=utf-8"),
                          "/task-library.js": ("task-library.js", "text/javascript; charset=utf-8"),
                          "/task-library.css": ("task-library.css", "text/css; charset=utf-8"),
                          "/navigation.css": ("navigation.css", "text/css; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/fonts.css": ("fonts.css", "text/css; charset=utf-8"),
                          "/workspace.css": ("workspace.css", "text/css; charset=utf-8")}
                for name in ("lora-regular", "lora-italic"):
                    assets[f"/fonts/{name}.ttf"] = (f"fonts/{name}.ttf", "font/ttf")
                for font in ("sans-400", "sans-500", "mono-400", "mono-500"):
                    assets[f"/fonts/plex-{font}.ttf"] = (f"fonts/plex-{font}.ttf", "font/ttf")
                if path in assets:
                    name, mime = assets[path]
                    return self.send(200, (static / name).read_bytes(), mime)
                if path == "/api/state":
                    return self.send(200, {"token": app.token, "runs": app.list_runs(),
                        "read_only": app.read_only,
                        **app.providers(),
                        "active": app.active(), "featured": FEATURED,
                        "example": read_json(app.root / "examples" / "successful-owner-evidence.json"),
                        "launch_candidate": read_text(app.root / "examples" / "successful-owner-bypass.py", 20_000),
                        "cases": [c.ui() if hasattr(c, "ui") else
                                  {**c.public(), "seed": c.seed, "target": c.target} for c in CASES.values()]})
                if path == "/api/providers":
                    return self.send(200, app.providers())
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    return self.send(200, app.detail(parts[2]))
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "calls":
                    return self.send(200, app.call_detail(parts[2], parts[4]))
                if (len(parts) == 6 and parts[:2] == ["api", "runs"] and
                        parts[3] == "attempts" and parts[5] == "evidence"):
                    filename, archive = build_evidence(app.run_path(parts[2]), parts[4])
                    return self.send(200, archive, "application/zip", filename=filename)
                return self.send(404, {"error": "Not found"})
            except (ValueError, FileNotFoundError) as exc:
                self.send(404, {"error": str(exc)})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (not self.valid_host() or self.headers.get("X-Lab-Token") != app.token or
                    len(self.headers.get_all("Origin", [])) > 1 or
                    len(self.headers.get_all("X-Lab-Token", [])) != 1 or
                    (origin is not None and origin not in self.server.allowed_origins)):
                return self.send(403, {"error": "Open the local dashboard to perform this action"})
            if app.read_only and self.path in ("/api/start", "/api/manual", "/api/stop"):
                return self.send(403, {"error": DEMO_MESSAGE, "code": "demo_read_only"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = 125_000 if self.path in ("/api/manual", "/api/manual/check") else 4096
                if not 0 < length <= limit:
                    raise ValueError("Invalid request size")
                config = json.loads(self.rfile.read(length))
                if self.path == "/api/enable-live":
                    return self.send(200, app.enable_live(config))
                if self.path == "/api/providers/check":
                    return self.send(200, app.check_provider(config))
                if self.path == "/api/start":
                    return self.send(201, app.launch(config))
                if self.path == "/api/manual":
                    return self.send(201, app.launch_manual(config))
                if self.path == "/api/manual/check":
                    if not isinstance(config, dict) or set(config) != {"case", "source"}:
                        raise ValueError("Select a case and paste a candidate")
                    return self.send(200, check_source(config["case"], config["source"]))
                if self.path == "/api/stop":
                    return self.send(200, app.stop())
                if self.path == "/api/replay":
                    if not isinstance(config, dict) or set(config) != {"run", "attempt"}:
                        raise ValueError("Select a saved attempt")
                    detail = app.detail(config["run"])
                    row = next((r for r in detail["rows"] if r["id"] == config["attempt"]), None)
                    if row is None or not row["source"]:
                        raise ValueError("Candidate is not available yet")
                    case = next(c for c in detail["cases"] if c["id"] == row["case"])
                    return self.send(200, evaluate(Case(**case), row["source"]))
                return self.send(404, {"error": "Not found"})
            except (ValueError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
            except RuntimeError as exc:
                self.send(409, {"error": str(exc)})
            except PermissionError as exc:
                self.send(403, {"error": str(exc), "code": "demo_read_only"})
            except OSError as exc:
                self.send(500, {"error": str(exc)})

    server = ThreadingHTTPServer((bind, port), Handler)
    server.allowed_origins = browser_origins(public_origins, server.server_port)
    server.allowed_hosts = {urlsplit(origin).netloc for origin in server.allowed_origins}
    server.dashboard = app
    return server


def serve(root, port=8765, open_browser=False, *, bind="127.0.0.1", read_only=False,
          public_origins=None, fallback_port=False, start_path="", provider="codex"):
    import webbrowser
    try:
        server = make_server(root, port, bind=bind, read_only=read_only, public_origins=public_origins, provider=provider)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        if not fallback_port:
            alternative = 8766 if port != 8766 else 8767
            raise RuntimeError(
                f"Порт {port} занят. Остановите прежний сервер (Ctrl+C) или укажите другой порт: "
                f"--port {alternative}. Для автоматического выбора свободного порта: --port 0."
            ) from exc
        # Let the OS bind and reserve a free port atomically; no check-then-bind race.
        server = make_server(root, 0, bind=bind, read_only=read_only, public_origins=public_origins, provider=provider)
        print(f"Порт {port} занят. Выбран свободный порт {server.server_port}.", flush=True)
    url = sorted(server.allowed_origins)[0] + start_path
    mode = DEMO_MESSAGE if server.dashboard.read_only else (
        "Эксперименты доступны: выберите провайдера и нажмите Запустить. Вызовы используют квоту или API-баланс. "
        "Просмотр сохранённых результатов не вызывает модели.")
    print(f"\nReviewer Lab: {url}\n{mode}\nОстановка: Ctrl+C в этом окне.", flush=True)
    try:
        if open_browser:
            try:
                opened = webbrowser.open(url)
            except OSError:
                opened = False
            if not opened:
                print("Не удалось открыть браузер. Скопируйте адрес выше в браузер.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        server.dashboard.stop()
        print("\nReviewer Lab остановлен.", flush=True)
    finally:
        server.server_close()
