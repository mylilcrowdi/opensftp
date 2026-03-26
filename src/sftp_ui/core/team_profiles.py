"""
Team Site Profiles — Export/Import shared server bookmarks.

Pro Feature: export connections as shareable JSON (secrets stripped),
import from file or JSON string into the connection store.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sftp_ui.core.connection import Connection, ConnectionStore
from sftp_ui.core.license import LicenseManager, LicenseStatus


_SECRET_FIELDS = {"password", "key_passphrase"}
_CLOUD_SECRET_FIELDS = {"access_key", "secret_key"}
_VERSION = 1


@dataclass
class ImportResult:
    """Result of an import operation."""
    added: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def export_connections(connections: list[Connection]) -> str:
    """Export connections to a JSON string, stripping secrets."""
    entries = []
    for conn in connections:
        d = conn.to_dict()
        # Strip secrets
        for field in _SECRET_FIELDS:
            d.pop(field, None)
        # Strip cloud secrets
        if d.get("cloud"):
            for field in _CLOUD_SECRET_FIELDS:
                d["cloud"][field] = ""
        # Remove tunnel secrets
        if d.get("tunnel"):
            d["tunnel"].pop("password", None)
            d["tunnel"].pop("key_passphrase", None)
        entries.append(d)

    payload = {
        "version": _VERSION,
        "exported_at": int(time.time()),
        "connections": entries,
    }
    return json.dumps(payload, indent=2)


def import_connections(data: str, store: ConnectionStore) -> ImportResult:
    """Import connections from a JSON string into the store.

    Duplicates (same name + host) are skipped. Invalid entries are skipped
    with errors recorded. Each imported connection gets a fresh UUID.
    """
    result = ImportResult()

    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, ValueError) as e:
        result.errors.append(f"Invalid JSON: {e}")
        return result

    if "connections" not in payload:
        result.errors.append("Missing 'connections' key in profile data")
        return result

    existing = {(c.name, c.host) for c in store.all()}

    for entry in payload["connections"]:
        try:
            name = entry.get("name", "")
            host = entry.get("host", "")

            if (name, host) in existing:
                result.skipped += 1
                continue

            # Assign fresh ID
            entry["id"] = str(uuid.uuid4())
            # Reset timestamp
            entry["last_connected"] = 0.0

            conn = Connection.from_dict(entry)
            store.add(conn)
            existing.add((name, host))
            result.added += 1
        except (TypeError, ValueError) as e:
            result.skipped += 1
            continue

    return result


class ProfileManager:
    """Pro-gated API for team profile export/import."""

    def __init__(self, license_mgr: LicenseManager) -> None:
        self._license = license_mgr

    def _is_pro(self) -> bool:
        return self._license.status() == LicenseStatus.PRO

    def export(self, connections: list[Connection]) -> Optional[str]:
        if not self._is_pro():
            return None
        return export_connections(connections)

    def import_to(self, data: str, store: ConnectionStore) -> Optional[ImportResult]:
        if not self._is_pro():
            return None
        return import_connections(data, store)

    def export_to_file(self, connections: list[Connection], path: Path) -> Optional[Path]:
        if not self._is_pro():
            return None
        content = export_connections(connections)
        path.write_text(content, encoding="utf-8")
        return path

    def import_from_file(self, path: Path, store: ConnectionStore) -> Optional[ImportResult]:
        if not self._is_pro():
            return None
        data = path.read_text(encoding="utf-8")
        return import_connections(data, store)
