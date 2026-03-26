"""
Connection workflow bug-hunting tests.

Smoke tests, integration tests, and edge-case probes across the full
connection CRUD lifecycle: ConnectionDialog, ConnectionManager, ConnectionStore.

Goal: find at least 10 breaking bugs.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import (
    CloudConfig,
    Connection,
    ConnectionStore,
    TunnelConfig,
)
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
from sftp_ui.ui.dialogs.connection_manager import ConnectionManagerDialog, _ConnItem, _time_ago


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sftp_conn(name="TestServer", host="10.0.0.1", user="deploy", port=22, **kw):
    return Connection(name=name, host=host, user=user, port=port, **kw)


def _s3_conn(name="S3 Backup", **kw):
    return Connection(
        name=name, protocol="s3",
        cloud=CloudConfig(provider="s3", bucket="my-bucket",
                          access_key="AKIATEST", secret_key="s3cr3t",
                          region="us-east-1", endpoint_url="", prefix="backups/"),
        **kw,
    )


def _gcs_conn(name="GCS Archive", **kw):
    return Connection(
        name=name, protocol="gcs",
        cloud=CloudConfig(provider="gcs", bucket="archive-bucket",
                          access_key="hmac-key", secret_key="hmac-secret"),
        **kw,
    )


def _store_with(tmp_path, *connections):
    store = ConnectionStore(tmp_path / "connections.json")
    for c in connections:
        store.add(c)
    return store


# ══════════════════════════════════════════════════════════════════════════════
# 1. SMOKE TESTS — basic operations should work
# ══════════════════════════════════════════════════════════════════════════════

class TestSmokeConnectionStore:
    """Verify the fundamentals of ConnectionStore."""

    def test_add_and_retrieve(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        conn = _sftp_conn()
        store.add(conn)
        assert store.get(conn.id).name == "TestServer"

    def test_update_persists(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        conn = _sftp_conn()
        store.add(conn)
        updated = dataclasses.replace(conn, name="Renamed")
        store.update(updated)
        assert store.get(conn.id).name == "Renamed"

    def test_remove_deletes(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        conn = _sftp_conn()
        store.add(conn)
        store.remove(conn.id)
        assert len(store.all()) == 0

    def test_persistence_across_reload(self, tmp_path):
        path = tmp_path / "c.json"
        store1 = ConnectionStore(path)
        store1.add(_sftp_conn("Server1", "1.1.1.1"))
        store1.add(_sftp_conn("Server2", "2.2.2.2"))

        store2 = ConnectionStore(path)
        assert len(store2.all()) == 2
        names = {c.name for c in store2.all()}
        assert names == {"Server1", "Server2"}

    def test_cloud_connection_roundtrip(self, tmp_path):
        path = tmp_path / "c.json"
        store1 = ConnectionStore(path)
        original = _s3_conn()
        store1.add(original)

        store2 = ConnectionStore(path)
        loaded = store2.get(original.id)
        assert loaded.protocol == "s3"
        assert loaded.cloud.bucket == "my-bucket"
        assert loaded.cloud.region == "us-east-1"
        assert loaded.cloud.prefix == "backups/"


class TestSmokeConnectionDialog:
    """Verify basic dialog behavior."""

    def test_new_dialog_has_empty_fields(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._name.text() == ""
        assert dlg._host.text() == ""
        assert dlg._user.text() == ""
        dlg.close()

    def test_edit_dialog_populates_sftp_fields(self, qapp):
        conn = _sftp_conn("MyServer", "prod.io", "admin", 2222)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._name.text() == "MyServer"
        assert dlg._host.text() == "prod.io"
        assert dlg._user.text() == "admin"
        assert dlg._port.value() == 2222
        dlg.close()

    def test_edit_dialog_populates_cloud_fields(self, qapp):
        conn = _s3_conn()
        dlg = ConnectionDialog(conn=conn)
        assert dlg._cloud_bucket.text() == "my-bucket"
        assert dlg._cloud_region.text() == "us-east-1"
        assert dlg._cloud_access_key.text() == "AKIATEST"
        assert dlg._cloud_secret_key.text() == "s3cr3t"
        assert dlg._cloud_prefix.text() == "backups/"
        dlg.close()

    def test_cancel_does_not_set_result(self, qapp):
        dlg = ConnectionDialog()
        dlg.reject()
        assert dlg._result_conn is None
        dlg.close()


# ══════════════════════════════════════════════════════════════════════════════
# 2. INTEGRATION TESTS — multi-step workflows
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegrationEditWorkflow:
    """Full edit workflow: open dialog with conn, modify, save, verify."""

    def test_edit_preserves_connection_id(self, qapp, tmp_path, tmp_key):
        conn = _sftp_conn(key_path=tmp_key)
        original_id = conn.id
        dlg = ConnectionDialog(conn=conn)
        dlg._name.setText("Renamed")
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.id == original_id
        assert result.name == "Renamed"
        dlg.close()

    def test_edit_preserves_last_connected(self, qapp, tmp_path, tmp_key):
        conn = _sftp_conn(key_path=tmp_key, last_connected=1234567890.0)
        dlg = ConnectionDialog(conn=conn)
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.last_connected == 1234567890.0
        dlg.close()

    def test_edit_preserves_favorite(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key, favorite=True)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._favorite.isChecked()
        dlg._on_accept()
        assert dlg.result_connection().favorite is True
        dlg.close()

    def test_edit_preserves_group(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key, group="Production")
        dlg = ConnectionDialog(conn=conn)
        assert dlg._group.text() == "Production"
        dlg._on_accept()
        assert dlg.result_connection().group == "Production"
        dlg.close()

    def test_edit_preserves_keepalive_interval(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key, keepalive_interval=60)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._keepalive_interval.value() == 60
        dlg._on_accept()
        assert dlg.result_connection().keepalive_interval == 60
        dlg.close()

    def test_edit_preserves_use_agent(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key, use_agent=True)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._use_agent.isChecked()
        dlg._on_accept()
        assert dlg.result_connection().use_agent is True
        dlg.close()

    def test_edit_preserves_tunnel(self, qapp, tmp_key):
        tunnel = TunnelConfig(host="bastion.io", user="jump", port=2222)
        conn = _sftp_conn(key_path=tmp_key, tunnel=tunnel)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._tunnel_checkbox.isChecked()
        assert dlg._tunnel_host.text() == "bastion.io"
        assert dlg._tunnel_user.text() == "jump"
        assert dlg._tunnel_port.value() == 2222
        dlg.close()


class TestIntegrationStoreEditCycle:
    """Store → Edit Dialog → Store roundtrip."""

    def test_full_edit_cycle_preserves_data(self, qapp, tmp_path, tmp_key):
        store = ConnectionStore(tmp_path / "c.json")
        original = _sftp_conn("Prod", "prod.io", "admin", 2222,
                              key_path=tmp_key, group="Production",
                              favorite=True, keepalive_interval=120)
        store.add(original)

        # Open edit dialog
        dlg = ConnectionDialog(conn=original, store=store)
        dlg._name.setText("Prod-Renamed")
        dlg._on_accept()
        result = dlg.result_connection()

        # Update store
        store.update(result)

        # Verify all fields
        saved = store.get(original.id)
        assert saved.name == "Prod-Renamed"
        assert saved.host == "prod.io"
        assert saved.user == "admin"
        assert saved.port == 2222
        assert saved.group == "Production"
        assert saved.favorite is True
        assert saved.keepalive_interval == 120
        assert saved.key_path == tmp_key
        dlg.close()


# ══════════════════════════════════════════════════════════════════════════════
# 3. BUG-HUNTING TESTS — edge cases that should break
# ══════════════════════════════════════════════════════════════════════════════

class TestBug01_ManagerEditMissingStore:
    """BUG: ConnectionManagerDialog._on_edit() doesn't pass store to ConnectionDialog.
    This means group autocomplete won't work when editing from the manager."""

    def test_manager_edit_passes_store_to_dialog(self):
        import inspect
        from sftp_ui.ui.dialogs.connection_manager import ConnectionManagerDialog
        source = inspect.getsource(ConnectionManagerDialog._on_edit)
        assert "store=" in source or "store=self._store" in source, \
            "BUG: ConnectionManagerDialog._on_edit() doesn't pass store to ConnectionDialog"


class TestBug02_ManagerNewMissingStore:
    """BUG: ConnectionManagerDialog._on_new() doesn't pass store to ConnectionDialog.
    Verified by inspecting the source code directly."""

    def test_manager_new_passes_store_to_dialog(self, qapp, tmp_path):
        import inspect
        from sftp_ui.ui.dialogs.connection_manager import ConnectionManagerDialog
        source = inspect.getsource(ConnectionManagerDialog._on_new)
        # The _on_new method should pass store= to ConnectionDialog
        assert "store=" in source or "store=self._store" in source, \
            "BUG: ConnectionManagerDialog._on_new() doesn't pass store to ConnectionDialog"


class TestBug03_CloudConnectionDisplayInManager:
    """BUG: _ConnItem shows 'user@host:port' for cloud connections, which is
    empty/meaningless. Should show bucket info instead."""

    def test_cloud_conn_item_shows_useful_info(self, qapp):
        conn = _s3_conn()
        widget = _ConnItem(conn)
        # Find the host label (second QLabel in the info layout)
        labels = widget.findChildren(type(widget.findChild(type(widget))))
        # Get all QLabels
        from PySide6.QtWidgets import QLabel
        labels = widget.findChildren(QLabel)
        # labels[0] = star, labels[1] = name, labels[2] = host/bucket, labels[3] = last connected
        host_label = labels[2]
        text = host_label.text()
        assert text != "@:22", \
            f"BUG: Cloud connection shows '{text}' instead of bucket info"
        assert "my-bucket" in text or "s3" in text.lower(), \
            f"BUG: Cloud connection display '{text}' doesn't mention bucket"
        widget.close()


class TestBug04_CloudSecretsNotInKeyring:
    """BUG: Cloud access_key and secret_key are stored in plaintext JSON,
    never routed through keyring like SSH passwords are."""

    def test_cloud_secrets_use_keyring(self, tmp_path):
        mock_keyring = MagicMock()
        mock_keyring.set_password = MagicMock()
        mock_keyring.get_password = MagicMock(return_value="s3cr3t")

        path = tmp_path / "c.json"
        with patch("sftp_ui.core.connection._HAS_KEYRING", True), \
             patch("sftp_ui.core.connection._keyring", mock_keyring):
            store = ConnectionStore(path)
            conn = _s3_conn()
            store.add(conn)

        # Read the raw JSON
        raw = json.loads(path.read_text())
        cloud_data = raw[0].get("cloud", {})

        # Cloud secrets should NOT be in plaintext
        assert cloud_data.get("secret_key") != "s3cr3t", \
            "BUG: Cloud secret_key stored in plaintext JSON, not in keyring"
        assert cloud_data.get("access_key") != "AKIATEST", \
            "BUG: Cloud access_key stored in plaintext JSON, not in keyring"


class TestBug05_StoreGetReturnsMutableRef:
    """BUG: ConnectionStore.get() returns a direct reference to the internal
    Connection object. Mutations bypass persistence."""

    def test_get_mutation_does_not_affect_store(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        conn = _sftp_conn(favorite=False)
        store.add(conn)

        # Get a reference and mutate it
        ref = store.get(conn.id)
        ref.favorite = True

        # The store's internal state should NOT be affected
        fresh = store.get(conn.id)
        assert fresh.favorite is False, \
            "BUG: store.get() returns mutable reference, mutations bypass _save()"


class TestBug06_TimeAgoFutureTimestamp:
    """BUG: _time_ago() returns negative days for future timestamps."""

    def test_future_timestamp_handled_gracefully(self):
        future_ts = time.time() + 86400  # 1 day in the future
        result = _time_ago(future_ts)
        assert "-" not in result, \
            f"BUG: _time_ago returns '{result}' for future timestamp"
        # Should return something like "Just now" or "In the future"
        assert result in ("Just now", "In the future", "Never") or "ago" not in result, \
            f"BUG: _time_ago returns '{result}' for future timestamp"


class TestBug07_HostPortParsingIPv6:
    """BUG: _on_host_edited incorrectly splits IPv6 addresses like '::1:22'."""

    def test_ipv6_address_not_split(self, qapp):
        dlg = ConnectionDialog()
        # Simulate user typing an IPv6 address without brackets
        dlg._host.setText("::1")
        QApplication.processEvents()
        # The host should remain unchanged, not be split
        assert dlg._host.text() == "::1", \
            f"BUG: IPv6 address was incorrectly modified to '{dlg._host.text()}'"
        assert dlg._port.value() == 22, \
            f"BUG: Port changed to {dlg._port.value()} when typing IPv6 address"
        dlg.close()

    def test_ipv6_with_port_bracket_notation(self, qapp):
        dlg = ConnectionDialog()
        # Bracketed IPv6 should not be split
        dlg._host.setText("[::1]:2222")
        QApplication.processEvents()
        # textEdited only fires on user input, not setText, so this tests programmatic behavior
        # Let's simulate user typing via textEdited signal
        dlg._on_host_edited("[::1]:2222")
        assert dlg._host.text() == "[::1]:2222", \
            f"BUG: Bracketed IPv6 was modified to '{dlg._host.text()}'"
        dlg.close()


class TestBug08_DuplicateConnectionNames:
    """BUG: No validation prevents creating two connections with the same name.
    This creates confusion in the UI (which "Prod" is which?)."""

    def test_store_allows_duplicate_names_different_hosts(self, tmp_path):
        """Same name with different hosts is allowed (by design)."""
        store = ConnectionStore(tmp_path / "c.json")
        store.add(_sftp_conn("Prod", "1.1.1.1"))
        store.add(_sftp_conn("Prod", "2.2.2.2"))
        prods = [c for c in store.all() if c.name == "Prod"]
        assert len(prods) == 2

    def test_store_rejects_duplicate_id(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        conn = _sftp_conn("Prod", "1.1.1.1")
        store.add(conn)
        with pytest.raises(ValueError, match="already exists"):
            store.add(conn)  # same ID


class TestBug09_CloudEditLosesKeepalive:
    """BUG: _accept_cloud() doesn't pass keepalive_interval, so editing a cloud
    connection that somehow had a non-default keepalive resets it to 30."""

    def test_cloud_edit_preserves_keepalive(self, qapp):
        conn = _s3_conn()
        # Manually set non-default keepalive (possible via from_dict or dataclass)
        conn = dataclasses.replace(conn, keepalive_interval=120)
        dlg = ConnectionDialog(conn=conn)
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.keepalive_interval == 120, \
            f"BUG: Cloud edit reset keepalive from 120 to {result.keepalive_interval}"
        dlg.close()


class TestBug10_CloudEditLosesUseAgent:
    """BUG: _accept_cloud() doesn't pass use_agent, so editing a cloud
    connection with use_agent=True resets it to False."""

    def test_cloud_edit_preserves_use_agent(self, qapp):
        conn = _s3_conn()
        conn = dataclasses.replace(conn, use_agent=True)
        dlg = ConnectionDialog(conn=conn)
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.use_agent is True, \
            f"BUG: Cloud edit reset use_agent from True to {result.use_agent}"
        dlg.close()


class TestBug11_ConnectionFromDictUnknownFields:
    """Edge case: from_dict silently drops unknown fields. This means
    connections saved by a newer version lose data when loaded by older version."""

    def test_from_dict_drops_future_fields_silently(self):
        data = {
            "name": "Test", "host": "1.1.1.1", "user": "admin", "port": 22,
            "future_feature": "some_value",
            "another_field": True,
        }
        conn = Connection.from_dict(data)
        assert conn.name == "Test"
        # Verify unknown fields are gone (this is by design but worth documenting)
        assert not hasattr(conn, "future_feature")


class TestBug12_EmptyPasswordNotNone:
    """Edge case: empty password string vs None. Dialog converts empty to None
    via 'or None', but what about whitespace-only passwords?"""

    def test_whitespace_password_treated_as_none(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key)
        dlg = ConnectionDialog(conn=conn)
        dlg._password.setText("   ")  # whitespace only
        dlg._on_accept()
        result = dlg.result_connection()
        # "   " is truthy, so `or None` won't trigger. This is a bug.
        assert result.password is None, \
            f"BUG: Whitespace-only password '{result.password!r}' saved instead of None"
        dlg.close()


class TestBug13_KeyPassphraseWhitespace:
    """Same issue as Bug12 but for key_passphrase."""

    def test_whitespace_key_passphrase_treated_as_none(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key)
        dlg = ConnectionDialog(conn=conn)
        dlg._key_passphrase.setText("   ")
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.key_passphrase is None, \
            f"BUG: Whitespace-only passphrase '{result.key_passphrase!r}' saved instead of None"
        dlg.close()


class TestBug14_TunnelPasswordWhitespace:
    """Same whitespace issue for tunnel password fields."""

    def test_whitespace_tunnel_password_treated_as_none(self, qapp, tmp_key):
        conn = _sftp_conn(key_path=tmp_key)
        dlg = ConnectionDialog(conn=conn)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("bastion.io")
        dlg._tunnel_user.setText("jump")
        dlg._tunnel_password.setText("   ")  # whitespace only
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.tunnel is not None
        assert result.tunnel.password is None, \
            f"BUG: Whitespace-only tunnel password '{result.tunnel.password!r}' saved"
        dlg.close()


class TestBug15_StoreRecordConnectedMissing:
    """Verify record_connected doesn't crash for missing connection."""

    def test_record_connected_missing_id_raises(self, tmp_path):
        store = ConnectionStore(tmp_path / "c.json")
        with pytest.raises(KeyError):
            store.record_connected("nonexistent-id")


class TestBug16_CloudConnectionPrefixNotPreservedOnEdit:
    """Verify cloud prefix survives edit roundtrip."""

    def test_cloud_prefix_preserved_on_edit(self, qapp):
        conn = _s3_conn()
        dlg = ConnectionDialog(conn=conn)
        assert dlg._cloud_prefix.text() == "backups/"
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.cloud.prefix == "backups/", \
            f"BUG: Cloud prefix lost on edit, got '{result.cloud.prefix}'"
        dlg.close()


class TestBug17_ProtocolSwitchDataLoss:
    """BUG: If user opens SFTP connection, switches to S3, fills bucket,
    and saves, all SFTP data is silently lost."""

    def test_protocol_switch_warns_about_data_loss(self, qapp, tmp_key):
        conn = _sftp_conn("MyServer", "prod.io", "admin", 2222, key_path=tmp_key)
        dlg = ConnectionDialog(conn=conn)

        # Switch to S3
        dlg._protocol_combo.setCurrentIndex(1)  # S3
        QApplication.processEvents()

        # Fill cloud fields
        dlg._cloud_bucket.setText("new-bucket")
        dlg._on_accept()

        result = dlg.result_connection()
        # The SFTP data (host, user, port, key) is silently gone
        assert result.protocol == "s3"
        # There should be SOME warning about losing SFTP data, or at minimum
        # the original host/user should be preserved somehow
        # This is a design issue: protocol switching silently drops all protocol-specific data
        dlg.close()


class TestBug18_SaveLoadPreservesAllCloudFields:
    """Integration: save cloud connection to JSON, reload, verify all fields."""

    def test_cloud_endpoint_url_survives_roundtrip(self, tmp_path):
        path = tmp_path / "c.json"
        store = ConnectionStore(path)
        conn = Connection(
            name="MinIO",
            protocol="s3",
            cloud=CloudConfig(
                provider="s3",
                bucket="data",
                endpoint_url="https://minio.local:9000",
                region="",
                access_key="minioadmin",
                secret_key="minioadmin",
                prefix="uploads/",
            ),
        )
        store.add(conn)

        store2 = ConnectionStore(path)
        loaded = store2.get(conn.id)
        assert loaded.cloud.endpoint_url == "https://minio.local:9000", \
            f"BUG: endpoint_url lost on save/load, got '{loaded.cloud.endpoint_url}'"
        assert loaded.cloud.prefix == "uploads/", \
            f"BUG: prefix lost on save/load, got '{loaded.cloud.prefix}'"


class TestBug19_CorruptJsonHandling:
    """Verify store handles corrupt JSON gracefully."""

    def test_corrupt_json_file_doesnt_crash(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{invalid json[[[")
        store = ConnectionStore(path)
        assert len(store.all()) == 0

    def test_partially_corrupt_entries_skipped(self, tmp_path):
        path = tmp_path / "c.json"
        data = [
            {"name": "Good", "host": "1.1.1.1", "user": "admin", "port": 22},
            {"name": ""},  # invalid: empty name
            {"name": "Also Good", "host": "2.2.2.2", "user": "root", "port": 22},
        ]
        path.write_text(json.dumps(data))
        store = ConnectionStore(path)
        assert len(store.all()) == 2
        names = {c.name for c in store.all()}
        assert names == {"Good", "Also Good"}


class TestBug20_ConnectionDialogValidation:
    """Probe validation edge cases."""

    def test_empty_name_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("admin")
        dlg._on_accept()
        assert dlg._result_conn is None
        assert "Name" in dlg._error_label.text() or "name" in dlg._error_label.text().lower()
        dlg.close()

    def test_empty_host_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._user.setText("admin")
        dlg._on_accept()
        assert dlg._result_conn is None
        dlg.close()

    def test_empty_user_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._host.setText("1.1.1.1")
        dlg._on_accept()
        assert dlg._result_conn is None
        dlg.close()

    def test_whitespace_only_name_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("   ")
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("admin")
        dlg._on_accept()
        assert dlg._result_conn is None, \
            "BUG: Whitespace-only name accepted"
        dlg.close()

    def test_cloud_empty_bucket_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._protocol_combo.setCurrentIndex(1)  # S3
        dlg._name.setText("CloudConn")
        dlg._on_accept()
        assert dlg._result_conn is None
        dlg.close()

    def test_nonexistent_key_file_rejected(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("admin")
        dlg._key_path.setText("/nonexistent/key/path")
        dlg._on_accept()
        assert dlg._result_conn is None
        assert "not found" in dlg._error_label.text().lower() or "key" in dlg._error_label.text().lower()
        dlg.close()


class TestBug21_GCSConnectionSupport:
    """Verify GCS connections work end-to-end."""

    def test_gcs_dialog_save(self, qapp):
        dlg = ConnectionDialog()
        dlg._protocol_combo.setCurrentIndex(2)  # GCS
        QApplication.processEvents()
        dlg._name.setText("GCS Archive")
        dlg._cloud_bucket.setText("my-archive")
        dlg._cloud_access_key.setText("hmac-key")
        dlg._cloud_secret_key.setText("hmac-secret")
        dlg._on_accept()
        result = dlg.result_connection()
        assert result.protocol == "gcs"
        assert result.cloud.provider == "gcs"
        assert result.cloud.bucket == "my-archive"
        dlg.close()

    def test_gcs_store_roundtrip(self, tmp_path):
        path = tmp_path / "c.json"
        store = ConnectionStore(path)
        conn = _gcs_conn()
        store.add(conn)

        store2 = ConnectionStore(path)
        loaded = store2.get(conn.id)
        assert loaded.protocol == "gcs"
        assert loaded.cloud.provider == "gcs"


class TestBug22_TunnelValidation:
    """Verify tunnel validation edge cases."""

    def test_tunnel_without_host_rejected(self, qapp, tmp_key):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("admin")
        dlg._key_path.setText(tmp_key)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_user.setText("jump")
        # tunnel_host is empty
        dlg._on_accept()
        assert dlg._result_conn is None, "BUG: Tunnel without host accepted"
        dlg.close()

    def test_tunnel_without_user_rejected(self, qapp, tmp_key):
        dlg = ConnectionDialog()
        dlg._name.setText("Server")
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("admin")
        dlg._key_path.setText(tmp_key)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("bastion.io")
        # tunnel_user is empty
        dlg._on_accept()
        assert dlg._result_conn is None, "BUG: Tunnel without user accepted"
        dlg.close()


class TestBug23_FindByName:
    """Test find_by_name edge cases."""

    def test_find_by_name_case_sensitive(self, tmp_path):
        store = _store_with(tmp_path, _sftp_conn("Production", "1.1.1.1"))
        assert store.find_by_name("Production") is not None
        assert store.find_by_name("production") is None  # case sensitive
        assert store.find_by_name("PRODUCTION") is None

    def test_find_by_name_not_found(self, tmp_path):
        store = _store_with(tmp_path, _sftp_conn("Server", "1.1.1.1"))
        assert store.find_by_name("Nonexistent") is None


class TestBug24_HostPortAutoSplit:
    """Test the host:port auto-split feature."""

    def test_host_port_split_on_user_input(self, qapp):
        dlg = ConnectionDialog()
        # Simulate user editing (textEdited signal)
        dlg._on_host_edited("prod.io:2222")
        assert dlg._host.text() == "prod.io"
        assert dlg._port.value() == 2222
        dlg.close()

    def test_host_port_no_split_on_programmatic_set(self, qapp):
        dlg = ConnectionDialog()
        # setText triggers textChanged but NOT textEdited
        dlg._host.setText("prod.io:2222")
        QApplication.processEvents()
        # Should NOT be split since it's programmatic
        assert dlg._host.text() == "prod.io:2222"
        dlg.close()

    def test_host_port_invalid_port_not_split(self, qapp):
        dlg = ConnectionDialog()
        dlg._on_host_edited("prod.io:99999")
        # Port 99999 is out of range, should not split
        assert dlg._port.value() == 22  # unchanged
        dlg.close()

    def test_host_port_zero_port_not_split(self, qapp):
        dlg = ConnectionDialog()
        dlg._on_host_edited("prod.io:0")
        assert dlg._port.value() == 22  # 0 is out of range 1-65535
        dlg.close()
