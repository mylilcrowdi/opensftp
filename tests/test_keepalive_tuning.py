"""
Tests for connection keepalive tuning (roadmap item #8).

Covers:
- Connection.keepalive_interval default is 30
- Connection.keepalive_interval is persisted in to_dict / from_dict
- Connection.keepalive_interval round-trips through ConnectionStore save/load
- keepalive_interval=0 is valid (disabled)
- keepalive_interval=3600 is valid (upper bound)
- keepalive_interval=-1 raises ValueError
- keepalive_interval=3601 raises ValueError
- from_dict with non-numeric keepalive_interval falls back to 30
- from_dict without keepalive_interval key defaults to 30
- SFTPClient.connect() calls transport.set_keepalive(interval) when > 0
- SFTPClient.connect() does NOT call set_keepalive when interval == 0
- ConnectionDialog has _keepalive_interval spinbox defaulting to 30
- ConnectionDialog._populate() sets spinbox to conn.keepalive_interval
- ConnectionDialog result_connection() returns correct keepalive_interval
- ConnectionDialog keepalive spinbox range is 0-3600
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import Connection, ConnectionStore


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def tmp_key(tmp_path) -> str:
    """Create a temporary dummy key file (path validation only checks existence)."""
    key = tmp_path / "id_rsa"
    key.write_text("dummy")
    return str(key)


def _conn(keepalive_interval: int = 30, **kw) -> Connection:
    defaults = dict(name="srv", host="h", user="u", password="pw")
    defaults.update(kw)
    return Connection(keepalive_interval=keepalive_interval, **defaults)


# ── Connection dataclass ───────────────────────────────────────────────────────

class TestConnectionKeepaliveField:
    def test_default_is_30(self):
        c = _conn()
        assert c.keepalive_interval == 30

    def test_explicit_value(self):
        c = _conn(keepalive_interval=60)
        assert c.keepalive_interval == 60

    def test_zero_is_valid(self):
        c = _conn(keepalive_interval=0)
        assert c.keepalive_interval == 0

    def test_upper_bound_valid(self):
        c = _conn(keepalive_interval=3600)
        assert c.keepalive_interval == 3600

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="keepalive_interval"):
            _conn(keepalive_interval=-1)

    def test_exceeds_upper_bound_raises(self):
        with pytest.raises(ValueError, match="keepalive_interval"):
            _conn(keepalive_interval=3601)

    def test_to_dict_includes_keepalive(self):
        c = _conn(keepalive_interval=45)
        d = c.to_dict()
        assert d["keepalive_interval"] == 45

    def test_from_dict_round_trip(self):
        c = _conn(keepalive_interval=120)
        d = c.to_dict()
        c2 = Connection.from_dict(d)
        assert c2.keepalive_interval == 120

    def test_from_dict_missing_key_defaults_to_30(self):
        """Older connections.json files without the field load cleanly."""
        d = {"name": "srv", "host": "h", "user": "u", "password": "pw"}
        c = Connection.from_dict(d)
        assert c.keepalive_interval == 30

    def test_from_dict_non_numeric_fallback(self):
        d = {
            "name": "srv", "host": "h", "user": "u", "password": "pw",
            "keepalive_interval": "bad",
        }
        c = Connection.from_dict(d)
        assert c.keepalive_interval == 30

    def test_from_dict_float_coerced_to_int(self):
        """JSON may store integers as floats; from_dict must coerce."""
        d = {
            "name": "srv", "host": "h", "user": "u", "password": "pw",
            "keepalive_interval": 60.0,
        }
        c = Connection.from_dict(d)
        assert c.keepalive_interval == 60
        assert isinstance(c.keepalive_interval, int)

    def test_zero_round_trip(self):
        c = _conn(keepalive_interval=0)
        c2 = Connection.from_dict(c.to_dict())
        assert c2.keepalive_interval == 0


# ── ConnectionStore persistence ────────────────────────────────────────────────

class TestConnectionStoreKeepalive:
    def test_keepalive_persisted_through_store(self, tmp_path):
        store_path = tmp_path / "connections.json"
        store = ConnectionStore(path=store_path)
        c = _conn(keepalive_interval=90)
        store.add(c)

        store2 = ConnectionStore(path=store_path)
        loaded = store2.get(c.id)
        assert loaded.keepalive_interval == 90

    def test_zero_keepalive_persisted(self, tmp_path):
        store_path = tmp_path / "connections.json"
        store = ConnectionStore(path=store_path)
        c = _conn(keepalive_interval=0)
        store.add(c)

        store2 = ConnectionStore(path=store_path)
        loaded = store2.get(c.id)
        assert loaded.keepalive_interval == 0

    def test_update_keepalive(self, tmp_path):
        store_path = tmp_path / "connections.json"
        store = ConnectionStore(path=store_path)
        c = _conn(keepalive_interval=30)
        store.add(c)

        import dataclasses
        updated = dataclasses.replace(c, keepalive_interval=120)
        store.update(updated)

        store2 = ConnectionStore(path=store_path)
        loaded = store2.get(c.id)
        assert loaded.keepalive_interval == 120

    def test_legacy_file_without_field_loads_default(self, tmp_path):
        """A connections.json written before keepalive_interval existed loads cleanly."""
        store_path = tmp_path / "connections.json"
        legacy = [{"name": "old", "host": "h", "user": "u", "password": "pw"}]
        store_path.write_text(json.dumps(legacy))

        store = ConnectionStore(path=store_path)
        conns = store.all()
        assert len(conns) == 1
        assert conns[0].keepalive_interval == 30


# ── SFTPClient keepalive behaviour ────────────────────────────────────────────

class TestSFTPClientKeepalive:
    """Verify that SFTPClient.connect() calls set_keepalive correctly."""

    def _make_transport_mock(self) -> MagicMock:
        transport = MagicMock()
        transport.window_size = 0
        transport.packetizer = MagicMock()
        transport.packetizer.REKEY_BYTES = 0
        transport.packetizer.REKEY_VOLUME = 0
        return transport

    def _connect_with_interval(self, keepalive_interval: int) -> MagicMock:
        """Call SFTPClient.connect() with a patched paramiko and return the transport mock."""
        from sftp_ui.core.sftp_client import SFTPClient

        transport = self._make_transport_mock()
        ssh_mock = MagicMock()
        ssh_mock.get_transport.return_value = transport

        conn = _conn(keepalive_interval=keepalive_interval)

        with patch("paramiko.SSHClient", return_value=ssh_mock), \
             patch("paramiko.SFTPClient.from_transport", return_value=MagicMock()):
            client = SFTPClient()
            client.connect(conn)

        return transport

    def test_set_keepalive_called_with_custom_interval(self):
        transport = self._connect_with_interval(60)
        transport.set_keepalive.assert_called_once_with(60)

    def test_set_keepalive_called_with_default_30(self):
        transport = self._connect_with_interval(30)
        transport.set_keepalive.assert_called_once_with(30)

    def test_set_keepalive_not_called_when_zero(self):
        """keepalive_interval=0 means disabled — set_keepalive must NOT be called."""
        transport = self._connect_with_interval(0)
        transport.set_keepalive.assert_not_called()

    def test_set_keepalive_called_with_large_interval(self):
        transport = self._connect_with_interval(3600)
        transport.set_keepalive.assert_called_once_with(3600)


# ── ConnectionDialog UI ────────────────────────────────────────────────────────

class TestConnectionDialogKeepalive:
    def test_dialog_has_keepalive_spinbox(self, qapp):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert hasattr(dlg, "_keepalive_interval")
        dlg.close()

    def test_spinbox_default_is_30(self, qapp):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert dlg._keepalive_interval.value() == 30
        dlg.close()

    def test_spinbox_range_min_is_0(self, qapp):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert dlg._keepalive_interval.minimum() == 0
        dlg.close()

    def test_spinbox_range_max_is_3600(self, qapp):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert dlg._keepalive_interval.maximum() == 3600
        dlg.close()

    def test_populate_sets_keepalive_value(self, qapp, tmp_key):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        conn = Connection(
            name="my-srv", host="h.example.com", user="admin",
            key_path=tmp_key, keepalive_interval=120,
        )
        dlg = ConnectionDialog(conn=conn)
        assert dlg._keepalive_interval.value() == 120
        dlg.close()

    def test_populate_zero_keepalive(self, qapp, tmp_key):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        conn = Connection(
            name="my-srv", host="h.example.com", user="admin",
            key_path=tmp_key, keepalive_interval=0,
        )
        dlg = ConnectionDialog(conn=conn)
        assert dlg._keepalive_interval.value() == 0
        dlg.close()

    def test_result_connection_reflects_spinbox(self, qapp, tmp_key):
        """Changing the spinbox value is reflected in result_connection()."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        dlg._name.setText("test-server")
        dlg._host.setText("10.0.0.1")
        dlg._user.setText("user")
        dlg._key_path.setText(tmp_key)
        dlg._keepalive_interval.setValue(90)

        # Simulate accept without calling exec() — call _on_accept directly
        dlg._on_accept()

        result = dlg.result_connection()
        assert result is not None
        assert result.keepalive_interval == 90
        dlg.close()

    def test_result_connection_zero_keepalive(self, qapp, tmp_key):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        dlg._name.setText("no-keepalive")
        dlg._host.setText("10.0.0.2")
        dlg._user.setText("admin")
        dlg._key_path.setText(tmp_key)
        dlg._keepalive_interval.setValue(0)

        dlg._on_accept()

        result = dlg.result_connection()
        assert result is not None
        assert result.keepalive_interval == 0
        dlg.close()

    def test_edit_dialog_preserves_keepalive(self, qapp, tmp_key):
        """Editing an existing connection preserves its keepalive_interval."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        conn = Connection(
            name="edit-srv", host="10.0.0.3", user="root",
            key_path=tmp_key, keepalive_interval=45,
        )
        dlg = ConnectionDialog(conn=conn)
        assert dlg._keepalive_interval.value() == 45

        # Save without modification
        dlg._on_accept()
        result = dlg.result_connection()
        assert result is not None
        assert result.keepalive_interval == 45
        dlg.close()
