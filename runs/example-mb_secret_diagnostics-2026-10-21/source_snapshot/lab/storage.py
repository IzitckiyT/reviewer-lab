import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def append_json(path, data):
    with Path(path).open("a", encoding="utf-8") as out:
        out.write(json.dumps(data, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(row) for row in path.read_text().splitlines() if row.strip()]


def recover_jsonl_tail(path):
    """Repair only a torn final record; caller must hold the journal writer's lock.

    A malformed complete line or middle record is not evidence of an interrupted
    append, and must still fail. The original bytes are preserved before repair.
    """
    path = Path(path)
    if not path.exists():
        return None
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)

    def repair(contents, discarded):
        backup = path.with_name(path.name + ".damaged-" + uuid4().hex)
        with backup.open("xb") as out:
            out.write(original)
            out.flush()
            os.fsync(out.fileno())
        temporary = path.with_name(path.name + ".repair-" + uuid4().hex)
        with temporary.open("xb") as out:
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        return {"journal": path.name, "backup": backup.name,
                "discarded_tail_bytes": discarded, "recovered_at": now()}

    offset = 0
    for index, line in enumerate(lines):
        try:
            if line.strip():
                json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeError):
            if index != len(lines) - 1 or line.endswith((b"\n", b"\r")):
                raise
            return repair(original[:offset], len(original) - offset)
        offset += len(line)
    if original and not original.endswith(b"\n"):
        # A complete JSON object may have reached disk just before its separator.
        # Keep the object and restore the separator before another append.
        return repair(original + b"\n", 0)
    return None
