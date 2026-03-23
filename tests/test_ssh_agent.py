"""
Tests for SSH Agent support — Feature 1.

Covers:
  - Connection.use_agent field defaults to False
  - use_agent serialises/deserialises correctly
  - SFTPClient passes allow_agent=True when use_agent=True
  - SFTPClient no longer raises AuthenticationError when use_agent=True
    and neither key_path nor password is set
  - ConnectionDialog exposes _use_agent checkbox
"""
from __future__ import annotations

import sys
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pathlib import Path
from unittest import mock

from sftp_ui.core.connection import Connection, ConnectionStore


# ── Connection dataclass ──────────────────────────────────────────────────────

class TestUseAgentField:
    def test_default_is_false(self):
        c = Connection(name="s", host="h", user="u", password="pw")
        assert c.use_agent is False

    def test_explicit_true(self):
        c = Connection(name="s", host="h", user="u", use_agent=True)
        assert c.use_agent is True

    def test_serialise_round_trip(self):
        c = Connection(name="s", host="h", user="u", use_agent=True)
        d = c.to_dict()
        c2 = Connection.from_dict(d)
        assert c2.use_agent is True

    def test_false_round_trip(self):
        c = Connection(name="s", host="h", user="u", password="pw")
        d = c.to_dict()
        c2 = Connection.from_dict(d)
        assert c2.use_agent is False

    def test_old_json_without_field_defaults_false(self):
        """Connections saved before this feature existed should still load."""
        d = {"name": "s", "host": "h", "user": "u", "password": "pw", "id": "abc"}
        c = Connection.from_dict(d)
        assert c.use_agent is False

    def test_store_persists_use_agent(self, tmp_path):
        store_path = tmp_path / "conns.json"
        store = ConnectionStore(path=store_path)
        c = Connection(name="ag", host="h", user="u", use_agent=True)
        store.add(c)

        store2 = ConnectionStore(path=store_path)
        loaded = store2.get(c.id)
        assert loaded.use_agent is True


# ── SFTPClient — allow_agent propagation ──────────────────────────────────────

class TestSFTPClientAgentFlag:
    def test_use_agent_true_passes_allow_agent_true(self):
        """When use_agent=True, paramiko.SSHClient.connect should receive allow_agent=True."""
        from sftp_ui.core.sftp_client import SFTPClient

        conn = Connection(name="s", host="localhost", user="u", use_agent=True)
        client = SFTPClient()

        captured: dict = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            raise Exception("stop here")

        with mock.patch("paramiko.SSHClient.connect", side_effect=fake_connect):
            with mock.patch("paramiko.SSHClient.set_missing_host_key_policy"):
                with mock.patch("paramiko.SSHClient.close"):
                    try:
                        client.connect(conn)
                    except Exception:
                        pass

        assert captured.get("allow_agent") is True

    def test_use_agent_false_passes_allow_agent_false(self):
        """When use_agent=False, paramiko.SSHClient.connect should receive allow_agent=False."""
        from sftp_ui.core.sftp_client import SFTPClient

        conn = Connection(name="s", host="localhost", user="u", password="pw")
        client = SFTPClient()
        captured: dict = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            raise Exception("stop here")

        with mock.patch("paramiko.SSHClient.connect", side_effect=fake_connect):
            with mock.patch("paramiko.SSHClient.set_missing_host_key_policy"):
                with mock.patch("paramiko.SSHClient.close"):
                    try:
                        client.connect(conn)
                    except Exception:
                        pass

        assert captured.get("allow_agent") is False

    def test_no_key_no_password_use_agent_true_does_not_raise_auth_error(self):
        """use_agent=True without key/password should not raise AuthenticationError."""
        from sftp_ui.core.sftp_client import SFTPClient, AuthenticationError

        conn = Connection(name="s", host="localhost", user="u", use_agent=True)
        client = SFTPClient()

        def fake_connect(**kwargs):
            raise Exception("network error — not an auth error")

        with mock.patch("paramiko.SSHClient.connect", side_effect=fake_connect):
            with mock.patch("paramiko.SSHClient.set_missing_host_key_policy"):
                with mock.patch("paramiko.SSHClient.close"):
                    from sftp_ui.core.sftp_client import ConnectionError as CE
                    with pytest.raises(CE):
                        client.connect(conn)
                    # Must NOT be AuthenticationError

    def test_no_key_no_password_use_agent_false_raises_auth_error(self):
        """Without key/password and agent disabled → AuthenticationError."""
        from sftp_ui.core.sftp_client import SFTPClient, AuthenticationError

        conn = Connection(name="s", host="localhost", user="u")
        client = SFTPClient()

        with mock.patch("paramiko.SSHClient.set_missing_host_key_policy"):
            with mock.patch("paramiko.SSHClient.close"):
                with pytest.raises(AuthenticationError):
                    client.connect(conn)


# ── ConnectionDialog ──────────────────────────────────────────────────────────

class TestConnectionDialogUseAgent:
    @pytest.fixture(autouse=True)
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication(sys.argv)

    def test_use_agent_checkbox_exists(self):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert hasattr(dlg, "_use_agent")
        assert dlg._use_agent.isCheckable()

    def test_use_agent_unchecked_by_default(self):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        assert not dlg._use_agent.isChecked()

    def test_populate_sets_use_agent(self):
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        conn = Connection(name="s", host="h", user="u", use_agent=True)
        dlg = ConnectionDialog(conn=conn)
        assert dlg._use_agent.isChecked()
