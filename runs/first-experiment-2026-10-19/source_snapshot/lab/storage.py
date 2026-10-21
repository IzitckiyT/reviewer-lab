import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


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
