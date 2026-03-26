"""
Team Site Profiles — Export/Import shared server bookmarks (#1).

Pro Feature: Export connections as shareable JSON, import from file/URL.
Secrets (passwords, keys) are stripped on export for safety.

Tests cover:
1. Export: connections → JSON (single, multiple, all)
2. Export strips secrets (password, key_passphrase, access_key, secret_key)
3. Export preserves non-secret fields
4. Import: JSON → connections added to store
5. Import validates data, skips invalid entries
6. Import detects duplicates (by name+host) and offers merge
7. Import from file path
8. Pro gate: export/import blocked for free users
9. Round-trip: export → import produces equivalent connections
10. ProfileManager API
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sftp_ui.core.connection import Connection, ConnectionStore, CloudConfig
from sftp_ui.core.team_profiles import (
    ProfileManager,
    export_connections,
    import_connections,
    ImportResult,
)
from sftp_ui.core.license import LicenseManager, LicenseStatus


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sftp_conn(name="Dev Server", host="10.0.0.1", user="deploy",
               port=22, password=None, key_path=None, **kw) -> Connection:
    return Connection(name=name, host=host, user=user, port=port,
                      password=password, key_path=key_path, **kw)


def _s3_conn(name="S3 Backup", **kw) -> Connection:
    return Connection(
        name=name, protocol="s3",
        cloud=CloudConfig(provider="s3", bucket="my-bucket",
                          access_key="AKIA1234", secret_key="supersecret"),
        **kw,
    )


def _store_with(tmp_path, *connections) -> ConnectionStore:
    store = ConnectionStore(tmp_path / "connections.json")
    for c in connections:
        store.add(c)
    return store


# ── 1. Export Basics ──────────────────────────────────────────────────────────

class TestExportConnections:
    def test_export_single_connection(self):
        conn = _sftp_conn()
        result = export_connections([conn])
        data = json.loads(result)
        assert len(data["connections"]) == 1
        assert data["connections"][0]["name"] == "Dev Server"

    def test_export_multiple_connections(self):
        conns = [_sftp_conn("A", "1.1.1.1"), _sftp_conn("B", "2.2.2.2")]
        result = export_connections(conns)
        data = json.loads(result)
        assert len(data["connections"]) == 2

    def test_export_is_valid_json(self):
        result = export_connections([_sftp_conn()])
        json.loads(result)  # Should not raise

    def test_export_has_metadata(self):
        result = export_connections([_sftp_conn()])
        data = json.loads(result)
        assert "version" in data
        assert "exported_at" in data
        assert data["version"] == 1

    def test_export_empty_list(self):
        result = export_connections([])
        data = json.loads(result)
        assert data["connections"] == []


# ── 2. Export Strips Secrets ──────────────────────────────────────────────────

class TestExportStripSecrets:
    def test_password_stripped(self):
        conn = _sftp_conn(password="secret123")
        result = export_connections([conn])
        data = json.loads(result)
        assert data["connections"][0].get("password") is None

    def test_key_passphrase_stripped(self):
        conn = _sftp_conn(key_path="/home/u/.ssh/id_rsa",
                          key_passphrase="mypass")
        result = export_connections([conn])
        data = json.loads(result)
        assert data["connections"][0].get("key_passphrase") is None

    def test_cloud_access_key_stripped(self):
        conn = _s3_conn()
        result = export_connections([conn])
        data = json.loads(result)
        cloud = data["connections"][0]["cloud"]
        assert cloud.get("access_key") in (None, "")

    def test_cloud_secret_key_stripped(self):
        conn = _s3_conn()
        result = export_connections([conn])
        data = json.loads(result)
        cloud = data["connections"][0]["cloud"]
        assert cloud.get("secret_key") in (None, "")


# ── 3. Export Preserves Non-Secret Fields ─────────────────────────────────────

class TestExportPreservesFields:
    def test_host_preserved(self):
        conn = _sftp_conn(host="myserver.io")
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["host"] == "myserver.io"

    def test_user_preserved(self):
        conn = _sftp_conn(user="admin")
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["user"] == "admin"

    def test_port_preserved(self):
        conn = _sftp_conn(port=2222)
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["port"] == 2222

    def test_key_path_preserved(self):
        conn = _sftp_conn(key_path="/home/u/.ssh/id_ed25519")
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["key_path"] == "/home/u/.ssh/id_ed25519"

    def test_group_preserved(self):
        conn = _sftp_conn(group="Production")
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["group"] == "Production"

    def test_favorite_preserved(self):
        conn = _sftp_conn(favorite=True)
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["favorite"] is True

    def test_tunnel_preserved(self):
        from sftp_ui.core.connection import TunnelConfig
        conn = _sftp_conn(tunnel=TunnelConfig(host="bastion.io", user="jump"))
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["tunnel"]["host"] == "bastion.io"

    def test_cloud_bucket_preserved(self):
        conn = _s3_conn()
        data = json.loads(export_connections([conn]))
        assert data["connections"][0]["cloud"]["bucket"] == "my-bucket"


# ── 4. Import Basics ─────────────────────────────────────────────────────────

class TestImportConnections:
    def test_import_single_connection(self, tmp_path):
        exported = export_connections([_sftp_conn()])
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(exported, store)
        assert result.added == 1
        assert len(store.all()) == 1

    def test_import_multiple_connections(self, tmp_path):
        conns = [_sftp_conn("A", "1.1.1.1"), _sftp_conn("B", "2.2.2.2")]
        exported = export_connections(conns)
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(exported, store)
        assert result.added == 2

    def test_imported_connection_has_new_id(self, tmp_path):
        original = _sftp_conn()
        original_id = original.id
        exported = export_connections([original])
        store = ConnectionStore(tmp_path / "connections.json")
        import_connections(exported, store)
        imported = store.all()[0]
        assert imported.id != original_id

    def test_imported_connection_has_correct_fields(self, tmp_path):
        exported = export_connections([_sftp_conn("Prod", "prod.io", "admin", 2222)])
        store = ConnectionStore(tmp_path / "connections.json")
        import_connections(exported, store)
        conn = store.all()[0]
        assert conn.name == "Prod"
        assert conn.host == "prod.io"
        assert conn.user == "admin"
        assert conn.port == 2222


# ── 5. Import Validation ─────────────────────────────────────────────────────

class TestImportValidation:
    def test_invalid_json_returns_error(self, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections("{bad json", store)
        assert result.added == 0
        assert len(result.errors) > 0

    def test_missing_connections_key_returns_error(self, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections('{"version": 1}', store)
        assert result.added == 0
        assert len(result.errors) > 0

    def test_invalid_connection_entry_skipped(self, tmp_path):
        data = json.dumps({
            "version": 1,
            "exported_at": int(time.time()),
            "connections": [
                {"name": "Good", "host": "1.1.1.1", "user": "u", "port": 22},
                {"name": ""},  # invalid: empty name
            ],
        })
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(data, store)
        assert result.added == 1
        assert result.skipped == 1

    def test_empty_connections_array(self, tmp_path):
        data = json.dumps({"version": 1, "exported_at": 0, "connections": []})
        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(data, store)
        assert result.added == 0
        assert len(result.errors) == 0


# ── 6. Import Duplicates ─────────────────────────────────────────────────────

class TestImportDuplicates:
    def test_duplicate_by_name_and_host_skipped(self, tmp_path):
        store = _store_with(tmp_path, _sftp_conn("Prod", "prod.io"))
        exported = export_connections([_sftp_conn("Prod", "prod.io")])
        result = import_connections(exported, store)
        assert result.added == 0
        assert result.skipped == 1
        assert len(store.all()) == 1

    def test_same_name_different_host_added(self, tmp_path):
        store = _store_with(tmp_path, _sftp_conn("Prod", "prod-1.io"))
        exported = export_connections([_sftp_conn("Prod", "prod-2.io")])
        result = import_connections(exported, store)
        assert result.added == 1
        assert len(store.all()) == 2

    def test_different_name_same_host_added(self, tmp_path):
        store = _store_with(tmp_path, _sftp_conn("Prod", "10.0.0.1"))
        exported = export_connections([_sftp_conn("Staging", "10.0.0.1")])
        result = import_connections(exported, store)
        assert result.added == 1


# ── 7. Import from File ──────────────────────────────────────────────────────

class TestImportFromFile:
    def test_import_from_file_path(self, tmp_path):
        exported = export_connections([_sftp_conn()])
        file_path = tmp_path / "team_profile.json"
        file_path.write_text(exported)

        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(file_path.read_text(), store)
        assert result.added == 1

    def test_import_file_with_multiple_profiles(self, tmp_path):
        conns = [_sftp_conn(f"Server-{i}", f"10.0.0.{i}") for i in range(5)]
        exported = export_connections(conns)
        file_path = tmp_path / "team.json"
        file_path.write_text(exported)

        store = ConnectionStore(tmp_path / "connections.json")
        result = import_connections(file_path.read_text(), store)
        assert result.added == 5


# ── 8. Pro Gate ───────────────────────────────────────────────────────────────

class TestTeamProfilesProGate:
    def test_export_blocked_for_free_user(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        pm = ProfileManager(mgr)
        result = pm.export([_sftp_conn()])
        assert result is None

    def test_export_allowed_for_pro_user(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "u@e.com")
        pm = ProfileManager(mgr)
        result = pm.export([_sftp_conn()])
        assert result is not None
        assert "connections" in json.loads(result)

    def test_import_blocked_for_free_user(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        pm = ProfileManager(mgr)
        store = ConnectionStore(tmp_path / "connections.json")
        result = pm.import_to(export_connections([_sftp_conn()]), store)
        assert result is None

    def test_import_allowed_for_pro_user(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "u@e.com")
        pm = ProfileManager(mgr)
        store = ConnectionStore(tmp_path / "connections.json")
        result = pm.import_to(export_connections([_sftp_conn()]), store)
        assert result is not None
        assert result.added == 1


# ── 9. Round-trip ─────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_export_import_roundtrip_preserves_data(self, tmp_path):
        original = _sftp_conn("Prod", "prod.io", "admin", 2222,
                              key_path="/home/u/.ssh/id", group="Production",
                              favorite=True)
        exported = export_connections([original])
        store = ConnectionStore(tmp_path / "connections.json")
        import_connections(exported, store)

        imported = store.all()[0]
        assert imported.name == original.name
        assert imported.host == original.host
        assert imported.user == original.user
        assert imported.port == original.port
        assert imported.key_path == original.key_path
        assert imported.group == original.group
        assert imported.favorite == original.favorite
        # Secrets are stripped
        assert imported.password is None

    def test_roundtrip_with_tunnel(self, tmp_path):
        from sftp_ui.core.connection import TunnelConfig
        original = _sftp_conn(tunnel=TunnelConfig(host="jump.io", user="bastion", port=2222))
        exported = export_connections([original])
        store = ConnectionStore(tmp_path / "connections.json")
        import_connections(exported, store)

        imported = store.all()[0]
        assert imported.tunnel is not None
        assert imported.tunnel.host == "jump.io"
        assert imported.tunnel.user == "bastion"
        assert imported.tunnel.port == 2222

    def test_roundtrip_with_s3(self, tmp_path):
        original = _s3_conn()
        exported = export_connections([original])
        store = ConnectionStore(tmp_path / "connections.json")
        import_connections(exported, store)

        imported = store.all()[0]
        assert imported.protocol == "s3"
        assert imported.cloud.bucket == "my-bucket"
        # Secrets stripped
        assert imported.cloud.access_key == ""
        assert imported.cloud.secret_key == ""


# ── 10. ProfileManager API ───────────────────────────────────────────────────

class TestProfileManager:
    @pytest.fixture
    def pro_pm(self, tmp_path):
        mgr = LicenseManager(tmp_path / "license.key")
        mgr.activate("SFTP-29A6B955-B054475D-B3D8E31A-963BE4E7", "u@e.com")
        return ProfileManager(mgr)

    def test_export_returns_json_string(self, pro_pm):
        result = pro_pm.export([_sftp_conn()])
        assert isinstance(result, str)
        json.loads(result)

    def test_import_returns_import_result(self, pro_pm, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        data = export_connections([_sftp_conn()])
        result = pro_pm.import_to(data, store)
        assert isinstance(result, ImportResult)
        assert result.added == 1

    def test_export_to_file(self, pro_pm, tmp_path):
        path = tmp_path / "export.json"
        pro_pm.export_to_file([_sftp_conn()], path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data["connections"]) == 1

    def test_import_from_file(self, pro_pm, tmp_path):
        # Create export file
        path = tmp_path / "export.json"
        path.write_text(export_connections([_sftp_conn()]))

        store = ConnectionStore(tmp_path / "connections.json")
        result = pro_pm.import_from_file(path, store)
        assert result.added == 1
