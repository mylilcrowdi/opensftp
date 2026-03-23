"""
Tests for ConnectionDialog — SFTP connection add/edit form.

Covers: window title (new vs edit), field population from existing Connection,
        _on_accept validation (missing required fields), result_connection(),
        port default, empty key_path → None, passphrase/password optional,
        id preserved on edit.
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import Connection
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _conn(**kw) -> Connection:
    defaults = dict(
        name="My Server",
        host="10.0.0.1",
        user="admin",
        port=22,
        key_path=None,
        password="secret",
    )
    defaults.update(kw)
    return Connection(**defaults)


# ── Title ──────────────────────────────────────────────────────────────────────

class TestConnectionDialogTitle:
    def test_new_connection_title(self, qapp):
        dlg = ConnectionDialog()
        assert "New" in dlg.windowTitle()

    def test_edit_connection_title(self, qapp):
        dlg = ConnectionDialog(conn=_conn())
        assert "Edit" in dlg.windowTitle()

    def test_new_connection_form_title_label(self, qapp):
        # The large title QLabel also says "New Connection"
        dlg = ConnectionDialog()
        # Find the title label (first QLabel in layout)
        from PySide6.QtWidgets import QLabel
        labels = dlg.findChildren(QLabel)
        texts = [l.text() for l in labels]
        assert any("New" in t for t in texts)


# ── Field population ───────────────────────────────────────────────────────────

class TestConnectionDialogPopulate:
    def test_populate_name(self, qapp):
        dlg = ConnectionDialog(conn=_conn(name="Pi Server"))
        assert dlg._name.text() == "Pi Server"

    def test_populate_host(self, qapp):
        dlg = ConnectionDialog(conn=_conn(host="192.168.99.1"))
        assert dlg._host.text() == "192.168.99.1"

    def test_populate_user(self, qapp):
        dlg = ConnectionDialog(conn=_conn(user="deploy"))
        assert dlg._user.text() == "deploy"

    def test_populate_port(self, qapp):
        dlg = ConnectionDialog(conn=_conn(port=2222))
        assert dlg._port.value() == 2222

    def test_populate_key_path(self, qapp, tmp_path):
        key = tmp_path / "id_ed25519"
        key.write_bytes(b"fake")
        dlg = ConnectionDialog(conn=_conn(key_path=str(key), password=None))
        assert dlg._key_path.text() == str(key)

    def test_populate_password(self, qapp):
        dlg = ConnectionDialog(conn=_conn(password="hunter2"))
        assert dlg._password.text() == "hunter2"

    def test_populate_key_passphrase(self, qapp, tmp_path):
        key = tmp_path / "id"
        key.write_bytes(b"k")
        dlg = ConnectionDialog(conn=_conn(key_path=str(key), key_passphrase="p@ss", password=None))
        assert dlg._key_passphrase.text() == "p@ss"

    def test_new_dialog_fields_empty(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._name.text() == ""
        assert dlg._host.text() == ""
        assert dlg._user.text() == ""

    def test_new_dialog_port_default_22(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._port.value() == 22


# ── _on_accept / result_connection ────────────────────────────────────────────

class TestConnectionDialogAccept:
    def _fill(self, dlg, name="srv", host="1.2.3.4", user="root", port=22):
        dlg._name.setText(name)
        dlg._host.setText(host)
        dlg._user.setText(user)
        dlg._port.setValue(port)

    def test_accept_with_valid_fields_creates_connection(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg)
        dlg._password.setText("pw")
        dlg._on_accept()
        conn = dlg.result_connection()
        assert conn.name == "srv"
        assert conn.host == "1.2.3.4"
        assert conn.user == "root"

    def test_accept_missing_name_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg, name="")
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg._error_label.text() != ""

    def test_accept_missing_host_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg, host="")
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg._error_label.text() != ""

    def test_accept_missing_user_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg, user="")
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg._error_label.text() != ""

    def test_accept_clears_previous_error(self, qapp):
        dlg = ConnectionDialog()
        dlg._error_label.setText("old error")
        self._fill(dlg)
        dlg._password.setText("pw")
        dlg._on_accept()
        # Error cleared before validation
        assert dlg._error_label.text() == ""

    def test_empty_key_path_becomes_none(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg)
        dlg._key_path.setText("")
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg.result_connection().key_path is None

    def test_empty_password_becomes_none(self, qapp, tmp_key):
        dlg = ConnectionDialog()
        self._fill(dlg)
        dlg._password.setText("")
        # Use a real file path so the new key-existence check passes
        dlg._key_path.setText(tmp_key)
        dlg._on_accept()
        assert dlg.result_connection().password is None

    def test_empty_passphrase_becomes_none(self, qapp, tmp_key):
        dlg = ConnectionDialog()
        self._fill(dlg)
        dlg._key_path.setText(tmp_key)
        dlg._key_passphrase.setText("")
        dlg._on_accept()
        assert dlg.result_connection().key_passphrase is None

    def test_port_value_preserved(self, qapp):
        dlg = ConnectionDialog()
        self._fill(dlg, port=2222)
        dlg._password.setText("pw")
        dlg._on_accept()
        assert dlg.result_connection().port == 2222


# ── Edit mode — id preservation ───────────────────────────────────────────────

class TestConnectionDialogEditMode:
    def test_id_preserved_on_edit(self, qapp):
        original = _conn()
        original_id = original.id
        dlg = ConnectionDialog(conn=original)
        dlg._password.setText("newpw")
        dlg._on_accept()
        assert dlg.result_connection().id == original_id

    def test_new_dialog_gets_fresh_id(self, qapp):
        dlg = ConnectionDialog()
        dlg._name.setText("x")
        dlg._host.setText("1.1.1.1")
        dlg._user.setText("u")
        dlg._password.setText("p")
        dlg._on_accept()
        new_id = dlg.result_connection().id
        # Should be a valid UUID
        uuid.UUID(new_id)  # raises if invalid

    def test_edit_fields_pre_populated(self, qapp):
        conn = _conn(name="Original Name", host="5.5.5.5")
        dlg = ConnectionDialog(conn=conn)
        assert dlg._name.text() == "Original Name"
        assert dlg._host.text() == "5.5.5.5"

    def test_edit_can_change_name(self, qapp):
        conn = _conn(name="Old")
        dlg = ConnectionDialog(conn=conn)
        dlg._name.setText("New Name")
        dlg._on_accept()
        assert dlg.result_connection().name == "New Name"

    def test_result_connection_none_before_accept(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._result_conn is None


# ── Host:port auto-split ───────────────────────────────────────────────────────

class TestHostPortSplit:
    """Pasting host:port into the Host field should split host and port."""

    def test_host_colon_port_splits(self, qapp):
        dlg = ConnectionDialog()
        dlg._on_host_edited("example.com:2222")
        assert dlg._host.text() == "example.com"
        assert dlg._port.value() == 2222

    def test_ip_colon_port_splits(self, qapp):
        dlg = ConnectionDialog()
        dlg._on_host_edited("192.168.1.10:8022")
        assert dlg._host.text() == "192.168.1.10"
        assert dlg._port.value() == 8022

    def test_plain_host_no_colon_unchanged(self, qapp):
        dlg = ConnectionDialog()
        dlg._host.setText("example.com")
        dlg._on_host_edited("example.com")
        # No colon — should leave host and port unchanged
        assert dlg._host.text() == "example.com"
        assert dlg._port.value() == 22

    def test_port_out_of_range_not_split(self, qapp):
        dlg = ConnectionDialog()
        dlg._host.setText("example.com:99999")
        dlg._on_host_edited("example.com:99999")
        # 99999 > 65535 — should not overwrite host or port
        assert dlg._host.text() == "example.com:99999"
        assert dlg._port.value() == 22

    def test_non_numeric_port_not_split(self, qapp):
        dlg = ConnectionDialog()
        dlg._host.setText("example.com:abc")
        dlg._on_host_edited("example.com:abc")
        # Non-numeric port — no split
        assert dlg._host.text() == "example.com:abc"
        assert dlg._port.value() == 22

    def test_ipv6_literal_not_split(self, qapp):
        """[::1]:22 should not be split — IPv6 brackets guard."""
        dlg = ConnectionDialog()
        dlg._host.setText("[::1]:22")
        dlg._on_host_edited("[::1]:22")
        # Starts with '[' → skip
        assert dlg._host.text() == "[::1]:22"
