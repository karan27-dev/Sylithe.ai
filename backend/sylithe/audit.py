"""Append-only, hash-chained audit log.

Every agent decision, skill execution, and policy verdict is recorded as a
JSON line whose ``hash`` covers the previous entry's hash, making after-the-fact
tampering detectable. In production, ship the file to S3/ClickHouse.
"""

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self._path.exists():
            return _GENESIS
        last = _GENESIS
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line).get("hash", last)
        return last

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self._lock:
            entry["prev_hash"] = self._last_hash
            payload = json.dumps(entry, sort_keys=True, default=str)
            entry["hash"] = hashlib.sha256(payload.encode()).hexdigest()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self._last_hash = entry["hash"]
        return entry

    def verify_chain(self) -> bool:
        """Recompute the chain and confirm no entry was altered or removed."""
        prev = _GENESIS
        if not self._path.exists():
            return True
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                stored_hash = entry.pop("hash")
                if entry.get("prev_hash") != prev:
                    return False
                payload = json.dumps(entry, sort_keys=True, default=str)
                if hashlib.sha256(payload.encode()).hexdigest() != stored_hash:
                    return False
                prev = stored_hash
        return True
