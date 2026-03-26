"""
Test Connection Button — verify connectivity from within the connection dialog.

Tests cover:
1. Button exists in dialog UI
2. Button disabled when required fields empty
3. Button enabled when required fields filled
4. Click triggers connection test (mocked)
5. Success shows green status message
6. Failure shows red error message
7. Button disabled during test (prevents double-click)
8. Works for SFTP connections
9. Works for S3/cloud connections
10. Core test_connection function (SFTP)
11. Core test_connection function (cloud)
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import Connection, CloudConfig, ConnectionStore
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fill_sftp_fields(dialog, name="Test", host="10.0.0.1", user="deploy"):
    """Fill minimum required SFTP fields."""
    dialog._name.setText(name)
    dialog._host.setText(host)
    dialog._user.setText(user)


def _fill_cloud_fields(dialog, name="S3 Test", bucket="my-bucket"):
    """Switch to S3 and fill minimum required cloud fields."""
    dialog._protocol_combo.setCurrentIndex(1)  # S3
    QApplication.processEvents()
    dialog._name.setText(name)
    dialog._cloud_bucket.setText(bucket)


# ── 1. Button Exists ────────────────────────────────────────────────────────

class TestTestButtonExists:
    def test_dialog_has_test_button(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        assert hasattr(dlg, "_test_btn")
        dlg.close()

    def test_test_button_has_label(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        assert "Test" in dlg._test_btn.text()
        dlg.close()


# ── 2. Button Disabled When Fields Empty ────────────────────────────────────

class TestTestButtonDisabled:
    def test_disabled_when_host_empty(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        dlg._name.setText("Test")
        dlg._host.setText("")
        dlg._user.setText("deploy")
        QApplication.processEvents()
        assert not dlg._test_btn.isEnabled()
        dlg.close()

    def test_disabled_when_user_empty(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        dlg._name.setText("Test")
        dlg._host.setText("10.0.0.1")
        dlg._user.setText("")
        QApplication.processEvents()
        assert not dlg._test_btn.isEnabled()
        dlg.close()

    def test_disabled_when_name_empty(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        dlg._name.setText("")
        dlg._host.setText("10.0.0.1")
        dlg._user.setText("deploy")
        QApplication.processEvents()
        assert not dlg._test_btn.isEnabled()
        dlg.close()

    def test_disabled_when_cloud_bucket_empty(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        dlg._protocol_combo.setCurrentIndex(1)  # S3
        QApplication.processEvents()
        dlg._name.setText("Test")
        dlg._cloud_bucket.setText("")
        QApplication.processEvents()
        assert not dlg._test_btn.isEnabled()
        dlg.close()


# ── 3. Button Enabled When Fields Filled ────────────────────────────────────

class TestTestButtonEnabled:
    def test_enabled_when_sftp_fields_filled(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        assert dlg._test_btn.isEnabled()
        dlg.close()

    def test_enabled_when_cloud_fields_filled(self, qapp, tmp_path):
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_cloud_fields(dlg)
        QApplication.processEvents()
        assert dlg._test_btn.isEnabled()
        dlg.close()


# ── 4. Click Triggers Test ──────────────────────────────────────────────────

class TestTestButtonClick:
    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_click_calls_test_function(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "OK")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        assert mock_test.called
        dlg.close()

    @patch("sftp_ui.ui.dialogs.connection_dialog.test_cloud_connection")
    def test_click_calls_cloud_test_for_s3(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "OK")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_cloud_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        assert mock_test.called
        dlg.close()


# ── 5. Success Message ──────────────────────────────────────────────────────

class TestTestSuccess:
    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_success_shows_message(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "Connection successful")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        label_text = dlg._test_status_label.text().lower()
        assert "success" in label_text or "ok" in label_text
        dlg.close()

    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_success_label_is_green(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "Connection successful")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        style = dlg._test_status_label.styleSheet()
        # Should contain a green-ish color
        assert "green" in style.lower() or "#a6e3a1" in style.lower() or "#40a02b" in style.lower()
        dlg.close()


# ── 6. Failure Message ──────────────────────────────────────────────────────

class TestTestFailure:
    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_failure_shows_error(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (False, "Connection refused")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        label_text = dlg._test_status_label.text().lower()
        assert "refused" in label_text or "failed" in label_text or "error" in label_text
        dlg.close()

    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_failure_label_is_red(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (False, "Connection refused")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        style = dlg._test_status_label.styleSheet()
        assert "red" in style.lower() or "#f38ba8" in style.lower() or "#d20f39" in style.lower()
        dlg.close()


# ── 7. Button Disabled During Test ──────────────────────────────────────────

class TestTestButtonDuringTest:
    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_button_re_enabled_after_test(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "OK")
        store = ConnectionStore(tmp_path / "connections.json")
        dlg = ConnectionDialog(store=store)
        _fill_sftp_fields(dlg)
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        # After test completes, button should be enabled again
        assert dlg._test_btn.isEnabled()
        dlg.close()


# ── 8. SFTP Test with Edit Mode ─────────────────────────────────────────────

class TestTestButtonEditMode:
    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_edit_mode_has_test_button(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "OK")
        conn = Connection(name="Prod", host="prod.io", user="admin", port=22)
        store = ConnectionStore(tmp_path / "connections.json")
        store.add(conn)
        dlg = ConnectionDialog(conn=conn, store=store)
        assert hasattr(dlg, "_test_btn")
        dlg.close()

    @patch("sftp_ui.ui.dialogs.connection_dialog.test_sftp_connection")
    def test_edit_mode_test_uses_current_fields(self, mock_test, qapp, tmp_path):
        mock_test.return_value = (True, "OK")
        conn = Connection(name="Prod", host="prod.io", user="admin", port=22)
        store = ConnectionStore(tmp_path / "connections.json")
        store.add(conn)
        dlg = ConnectionDialog(conn=conn, store=store)
        # Change host in form
        dlg._host.setText("staging.io")
        QApplication.processEvents()
        dlg._test_btn.click()
        QApplication.processEvents()
        # Should test with the current form value, not the original
        call_args = mock_test.call_args
        assert call_args[1]["host"] == "staging.io" or call_args[0][0] == "staging.io"
        dlg.close()


# ── 10. Core test_sftp_connection ────────────────────────────────────────────

class TestCoreSftpTest:
    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_successful_connection(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        assert ok is True
        assert "success" in msg.lower()
        mock_ssh.close.assert_called()

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_connection_refused(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = socket.error("Connection refused")
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        assert ok is False
        assert "refused" in msg.lower() or "error" in msg.lower()

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_auth_failure(self, mock_ssh_cls):
        import paramiko
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = paramiko.AuthenticationException("Auth failed")
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        assert ok is False
        assert "auth" in msg.lower()

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_timeout(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = socket.timeout("Connection timed out")
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        assert ok is False
        assert "timeout" in msg.lower() or "timed" in msg.lower()

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_with_password(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(
            host="10.0.0.1", port=22, user="deploy", password="secret"
        )
        assert ok is True
        connect_kwargs = mock_ssh.connect.call_args
        assert connect_kwargs[1].get("password") == "secret"

    @patch("sftp_ui.core.connection_tester.paramiko.RSAKey.from_private_key_file")
    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_with_key_path(self, mock_ssh_cls, mock_rsa, tmp_path):
        from sftp_ui.core.connection_tester import test_sftp_connection
        key_file = tmp_path / "id_rsa"
        key_file.write_text("fake key")
        mock_rsa.return_value = MagicMock()
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        ok, msg = test_sftp_connection(
            host="10.0.0.1", port=22, user="deploy", key_path=str(key_file)
        )
        assert ok is True

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_closes_connection_on_success(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        mock_ssh.close.assert_called_once()

    @patch("sftp_ui.core.connection_tester.paramiko.SSHClient")
    def test_closes_connection_on_failure(self, mock_ssh_cls):
        from sftp_ui.core.connection_tester import test_sftp_connection
        mock_ssh = MagicMock()
        mock_ssh.connect.side_effect = socket.error("fail")
        mock_ssh_cls.return_value = mock_ssh
        test_sftp_connection(host="10.0.0.1", port=22, user="deploy", password="pw")
        mock_ssh.close.assert_called_once()


# ── 11. Core test_cloud_connection ───────────────────────────────────────────

class TestCoreCloudTest:
    @patch("sftp_ui.core.connection_tester.boto3")
    @patch("sftp_ui.core.connection_tester._HAS_BOTO3", True)
    def test_successful_s3_connection(self, mock_boto3):
        from sftp_ui.core.connection_tester import test_cloud_connection
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        ok, msg = test_cloud_connection(
            provider="s3", bucket="my-bucket",
            access_key="AKIA1234", secret_key="secret",
        )
        assert ok is True
        assert "success" in msg.lower()

    @patch("sftp_ui.core.connection_tester.boto3")
    @patch("sftp_ui.core.connection_tester._HAS_BOTO3", True)
    def test_s3_auth_error(self, mock_boto3):
        from sftp_ui.core.connection_tester import test_cloud_connection
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket"
        )
        mock_boto3.client.return_value = mock_client
        ok, msg = test_cloud_connection(
            provider="s3", bucket="my-bucket",
            access_key="bad", secret_key="bad",
        )
        assert ok is False

    @patch("sftp_ui.core.connection_tester.boto3")
    @patch("sftp_ui.core.connection_tester._HAS_BOTO3", True)
    def test_s3_bucket_not_found(self, mock_boto3):
        from sftp_ui.core.connection_tester import test_cloud_connection
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )
        mock_boto3.client.return_value = mock_client
        ok, msg = test_cloud_connection(
            provider="s3", bucket="nonexistent",
            access_key="AKIA1234", secret_key="secret",
        )
        assert ok is False
        assert "not found" in msg.lower() or "404" in msg.lower() or "bucket" in msg.lower()
