"""
TransferHistory — persistent log of completed transfers as JSON lines.

Each completed/failed/cancelled transfer is appended as a single JSON line.
On read, entries are returned newest-first. Old entries are auto-truncated
when max_entries is exceeded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sftp_ui.core.transfer import TransferDirection, TransferJob


class TransferHistory:
    def __init__(self, path: Path | str, max_entries: int = 1000) -> None:
        self._path = Path(path)
        self._max_entries = max_entries

    def record(self, job: TransferJob) -> None:
        entry = {
            "filename": job.filename,
            "direction": job.direction.name.lower(),
            "state": job.state.name.lower(),
            "total_bytes": job.total_bytes,
            "local_path": job.local_path,
            "remote_path": job.remote_path,
            "finished_at": job.finished_at,
            "error": job.error,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Auto-truncate if over limit
        all_entries = self._read_all()
        if len(all_entries) > self._max_entries:
            # Keep most recent
            keep = all_entries[-self._max_entries:]
            with open(self._path, "w") as f:
                for e in keep:
                    f.write(json.dumps(e) + "\n")

    def entries(
        self,
        state: Optional[str] = None,
        direction: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        all_entries = self._read_all()

        if state:
            all_entries = [e for e in all_entries if e.get("state") == state]
        if direction:
            all_entries = [e for e in all_entries if e.get("direction") == direction]

        # Sort newest first
        all_entries.sort(key=lambda e: e.get("finished_at", 0), reverse=True)

        if limit is not None:
            all_entries = all_entries[:limit]

        return all_entries

    def clear(self) -> None:
        if self._path.exists():
            self._path.write_text("")

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
