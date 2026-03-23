"""
Tests for SSH Tunneling support.

Covers:
  - TunnelConfig dataclass validation and serialization
  - Connection.tunnel field: to_dict / from_dict round-trip
  - ConnectionStore: persist + reload connections with tunnels
  - ConnectionDialog: tunnel checkbox toggle, populate from conn, build tunnel
  - SFTPClient: _open_tunnel_channel called when tunnel is set (mocked)
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sftp_ui.core.connection import Connection, TunnelConfig, ConnectionStore
from sftp_ui.core.sftp_client import SFTPClient, AuthenticationError, ConnectionError
from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def tmp_key(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"fake key content")
    return str(key)


def _conn(**kw) -> Connection:
    defaults = dict(name="My Server", host="10.0.0.1", user="admin", password="secret")
    defaults.update(kw)
    return Connection(**defaults)


def _tunnel(**kw) -> TunnelConfig:
    defaults = dict(host="bastion.example.com", user="ec2-user", password="jump")
    defaults.update(kw)
    return TunnelConfig(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# TunnelConfig dataclass
# ══════════════════════════════════════════════════════════════════════════════

class TestTunnelConfig:
    def test_basic_creation(self):
        t = TunnelConfig(host="jump.example.com", user="admin", password="pw")
        assert t.host == "jump.example.com"
        assert t.user == "admin"
        assert t.port == 22

    def test_custom_port(self):
        t = TunnelConfig(host="h", user="u", port=2222, password="pw")
        assert t.port == 2222

    def test_rejects_empty_host(self):
        with pytest.raises(ValueError, match="tunnel host"):
            TunnelConfig(host="", user="u", password="pw")

    def test_rejects_empty_user(self):
        with pytest.raises(ValueError, match="tunnel user"):
            TunnelConfig(host="h", user="", password="pw")

    def test_rejects_invalid_port_zero(self):
        with pytest.raises(ValueError, match="tunnel port"):
            TunnelConfig(host="h", user="u", port=0, password="pw")

    def test_rejects_invalid_port_too_high(self):
        with pytest.raises(ValueError, match="tunnel port"):
            TunnelConfig(host="h", user="u", port=99999, password="pw")

    def test_rejects_relative_key_path(self):
        with pytest.raises(ValueError, match="absolute"):
            TunnelConfig(host="h", user="u", key_path="relative/key")

    def test_allows_none_key_path(self):
        t = TunnelConfig(host="h", user="u", password="pw")
        assert t.key_path is None

    def test_roundtrip_dict(self):
        t = TunnelConfig(host="jump.example.com", user="admin", port=2222, password="secret")
        restored = TunnelConfig.from_dict(t.to_dict())
        assert restored.host == t.host
        assert restored.user == t.user
        assert restored.port == t.port
        assert restored.password == t.password

    def test_roundtrip_with_key(self, tmp_key):
        t = TunnelConfig(host="h", user="u", key_path=tmp_key, key_passphrase="pass")
        restored = TunnelConfig.from_dict(t.to_dict())
        assert restored.key_path == tmp_key
        assert restored.key_passphrase == "pass"


# ══════════════════════════════════════════════════════════════════════════════
# Connection.tunnel field
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectionTunnel:
    def test_connection_without_tunnel(self):
        c = _conn()
        assert c.tunnel is None

    def test_connection_with_tunnel(self):
        t = _tunnel()
        c = _conn(tunnel=t)
        assert c.tunnel is t

    def test_to_dict_includes_tunnel(self):
        t = _tunnel()
        c = _conn(tunnel=t)
        d = c.to_dict()
        assert d["tunnel"] is not None
        assert d["tunnel"]["host"] == "bastion.example.com"

    def test_to_dict_tunnel_none(self):
        c = _conn()
        d = c.to_dict()
        assert d["tunnel"] is None

    def test_from_dict_roundtrip_with_tunnel(self):
        t = _tunnel()
        c = _conn(tunnel=t)
        c2 = Connection.from_dict(c.to_dict())
        assert c2.tunnel is not None
        assert c2.tunnel.host == "bastion.example.com"
        assert c2.tunnel.user == "ec2-user"
        assert c2.tunnel.port == 22

    def test_from_dict_roundtrip_without_tunnel(self):
        c = _conn()
        c2 = Connection.from_dict(c.to_dict())
        assert c2.tunnel is None

    def test_from_dict_tunnel_missing_key(self):
        """from_dict should handle dicts that predate the tunnel field."""
        d = {"name": "s", "host": "h", "user": "u", "password": "p",
             "id": str(uuid.uuid4())}
        c = Connection.from_dict(d)
        assert c.tunnel is None

    def test_id_preserved_with_tunnel(self):
        t = _tunnel()
        c = _conn(tunnel=t)
        c2 = Connection.from_dict(c.to_dict())
        assert c2.id == c.id


# ══════════════════════════════════════════════════════════════════════════════
# ConnectionStore: tunnel persistence
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectionStoreTunnel:
    def test_persist_and_reload_with_tunnel(self, tmp_path):
        path = tmp_path / "conns.json"
        t = _tunnel(host="jump.host", port=2222)
        c = _conn(tunnel=t)
        s1 = ConnectionStore(path=path)
        s1.add(c)

        s2 = ConnectionStore(path=path)
        loaded = s2.get(c.id)
        assert loaded.tunnel is not None
        assert loaded.tunnel.host == "jump.host"
        assert loaded.tunnel.port == 2222

    def test_persist_and_reload_without_tunnel(self, tmp_path):
        path = tmp_path / "conns.json"
        c = _conn()
        s1 = ConnectionStore(path=path)
        s1.add(c)

        s2 = ConnectionStore(path=path)
        loaded = s2.get(c.id)
        assert loaded.tunnel is None

    def test_update_adds_tunnel(self, tmp_path):
        path = tmp_path / "conns.json"
        c = _conn()
        s = ConnectionStore(path=path)
        s.add(c)

        c_with_tunnel = Connection(
            name=c.name, host=c.host, user=c.user, password=c.password,
            id=c.id, tunnel=_tunnel(),
        )
        s.update(c_with_tunnel)

        s2 = ConnectionStore(path=path)
        loaded = s2.get(c.id)
        assert loaded.tunnel is not None

    def test_update_removes_tunnel(self, tmp_path):
        path = tmp_path / "conns.json"
        c = _conn(tunnel=_tunnel())
        s = ConnectionStore(path=path)
        s.add(c)

        c_no_tunnel = Connection(
            name=c.name, host=c.host, user=c.user, password=c.password,
            id=c.id, tunnel=None,
        )
        s.update(c_no_tunnel)

        s2 = ConnectionStore(path=path)
        loaded = s2.get(c.id)
        assert loaded.tunnel is None


# ══════════════════════════════════════════════════════════════════════════════
# ConnectionDialog: tunnel UI
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectionDialogTunnel:
    """Tests for the SSH tunnel section in ConnectionDialog."""

    def _fill_main(self, dlg, name="srv", host="1.2.3.4", user="root", port=22):
        dlg._name.setText(name)
        dlg._host.setText(host)
        dlg._user.setText(user)
        dlg._port.setValue(port)
        dlg._password.setText("pw")

    # Checkbox visibility — use isHidden() since the dialog itself is not shown
    def test_tunnel_group_hidden_by_default(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._tunnel_group.isHidden()

    def test_tunnel_group_shown_when_checkbox_checked(self, qapp):
        dlg = ConnectionDialog()
        dlg._tunnel_checkbox.setChecked(True)
        assert not dlg._tunnel_group.isHidden()

    def test_tunnel_group_hidden_when_checkbox_unchecked(self, qapp):
        dlg = ConnectionDialog()
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_checkbox.setChecked(False)
        assert dlg._tunnel_group.isHidden()

    # Populate from existing connection WITH tunnel
    def test_populate_tunnel_fields(self, qapp, tmp_key):
        t = TunnelConfig(host="jump.example.com", user="jumper", port=2222,
                         key_path=tmp_key, password=None)
        c = _conn(tunnel=t)
        dlg = ConnectionDialog(conn=c)
        assert dlg._tunnel_checkbox.isChecked()
        assert not dlg._tunnel_group.isHidden()
        assert dlg._tunnel_host.text() == "jump.example.com"
        assert dlg._tunnel_user.text() == "jumper"
        assert dlg._tunnel_port.value() == 2222
        assert dlg._tunnel_key_path.text() == tmp_key

    def test_populate_no_tunnel(self, qapp):
        c = _conn()
        dlg = ConnectionDialog(conn=c)
        assert not dlg._tunnel_checkbox.isChecked()
        assert dlg._tunnel_group.isHidden()

    # Accept — tunnel builds correctly
    def test_accept_with_tunnel_creates_tunnel_config(self, qapp):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("bastion.example.com")
        dlg._tunnel_user.setText("ec2-user")
        dlg._tunnel_port.setValue(22)
        dlg._tunnel_password.setText("jump-pw")
        dlg._on_accept()
        conn = dlg.result_connection()
        assert conn.tunnel is not None
        assert conn.tunnel.host == "bastion.example.com"
        assert conn.tunnel.user == "ec2-user"
        assert conn.tunnel.password == "jump-pw"

    def test_accept_without_tunnel_checked_gives_none(self, qapp):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(False)
        dlg._on_accept()
        assert dlg.result_connection().tunnel is None

    def test_accept_empty_tunnel_host_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("")          # required
        dlg._tunnel_user.setText("u")
        dlg._tunnel_password.setText("pw")
        dlg._on_accept()
        assert dlg._error_label.text() != ""

    def test_accept_empty_tunnel_user_shows_error(self, qapp):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("h")
        dlg._tunnel_user.setText("")          # required
        dlg._tunnel_password.setText("pw")
        dlg._on_accept()
        assert dlg._error_label.text() != ""

    def test_tunnel_port_default_is_22(self, qapp):
        dlg = ConnectionDialog()
        assert dlg._tunnel_port.value() == 22

    def test_tunnel_key_path_empty_becomes_none(self, qapp):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("h")
        dlg._tunnel_user.setText("u")
        dlg._tunnel_key_path.setText("")
        dlg._tunnel_password.setText("pw")
        dlg._on_accept()
        conn = dlg.result_connection()
        assert conn.tunnel is not None
        assert conn.tunnel.key_path is None

    def test_tunnel_password_empty_becomes_none(self, qapp, tmp_key):
        dlg = ConnectionDialog()
        self._fill_main(dlg)
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("h")
        dlg._tunnel_user.setText("u")
        # Use a real file path so the new key-existence check passes
        dlg._tunnel_key_path.setText(tmp_key)
        dlg._tunnel_password.setText("")
        dlg._on_accept()
        conn = dlg.result_connection()
        assert conn.tunnel is not None
        assert conn.tunnel.password is None

    def test_edit_mode_preserves_id_with_tunnel(self, qapp):
        t = _tunnel()
        c = _conn(tunnel=t)
        orig_id = c.id
        dlg = ConnectionDialog(conn=c)
        dlg._on_accept()
        assert dlg.result_connection().id == orig_id

    def test_tunnel_passphrase_populated(self, qapp, tmp_key):
        t = TunnelConfig(host="h", user="u", key_path=tmp_key, key_passphrase="passme")
        c = _conn(tunnel=t)
        dlg = ConnectionDialog(conn=c)
        assert dlg._tunnel_key_passphrase.text() == "passme"


# ══════════════════════════════════════════════════════════════════════════════
# SFTPClient: tunnel integration (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestSFTPClientTunnel:
    """Verify SFTPClient opens a tunnel channel when conn.tunnel is set."""

    def _make_conn(self, tmp_key) -> Connection:
        return Connection(
            name="srv", host="10.0.0.1", user="admin", port=22,
            key_path=tmp_key,
        )

    def _make_tunnel(self) -> TunnelConfig:
        return TunnelConfig(host="jump.example.com", user="ec2-user", password="jmpw")

    def _mock_ssh_client(self, mock_class) -> MagicMock:
        """Return a preconfigured SSHClient mock."""
        inst = MagicMock()
        transport = MagicMock()
        transport.window_size = 0
        packetizer = MagicMock()
        packetizer.REKEY_BYTES = 0
        packetizer.REKEY_VOLUME = 0
        transport.packetizer = packetizer
        channel = MagicMock()
        transport.open_channel.return_value = channel
        inst.get_transport.return_value = transport
        mock_class.return_value = inst
        return inst

    @patch("sftp_ui.core.sftp_client.paramiko.SFTPClient")
    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_no_tunnel_does_not_open_channel(self, mock_ssh_cls, mock_sftp_cls, tmp_key):
        inst = self._mock_ssh_client(mock_ssh_cls)
        sftp_inst = MagicMock()
        mock_sftp_cls.from_transport.return_value = sftp_inst

        conn = self._make_conn(tmp_key)
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            client.connect(conn)

        transport = inst.get_transport.return_value
        transport.open_channel.assert_not_called()
        assert client._tunnel_ssh is None

    @patch("sftp_ui.core.sftp_client.paramiko.SFTPClient")
    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_tunnel_opens_direct_tcpip_channel(self, mock_ssh_cls, mock_sftp_cls, tmp_key):
        """When conn.tunnel is set, open_channel("direct-tcpip", ...) must be called."""
        call_count = [0]
        jump_inst = MagicMock()
        target_inst = MagicMock()

        def ssh_factory():
            call_count[0] += 1
            if call_count[0] == 1:
                return jump_inst
            return target_inst

        mock_ssh_cls.side_effect = lambda: ssh_factory()

        # Jump transport
        jump_transport = MagicMock()
        channel = MagicMock()
        jump_transport.open_channel.return_value = channel
        jump_inst.get_transport.return_value = jump_transport

        # Target transport
        target_transport = MagicMock()
        target_transport.window_size = 0
        pkt = MagicMock()
        pkt.REKEY_BYTES = 0
        pkt.REKEY_VOLUME = 0
        target_transport.packetizer = pkt
        target_inst.get_transport.return_value = target_transport

        sftp_inst = MagicMock()
        mock_sftp_cls.from_transport.return_value = sftp_inst

        conn = self._make_conn(tmp_key)
        conn.tunnel = self._make_tunnel()
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            client.connect(conn)

        jump_transport.open_channel.assert_called_once_with(
            "direct-tcpip",
            ("10.0.0.1", 22),
            ("127.0.0.1", 0),
        )
        assert client._tunnel_ssh is jump_inst

    @patch("sftp_ui.core.sftp_client.paramiko.SFTPClient")
    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_close_also_closes_tunnel_ssh(self, mock_ssh_cls, mock_sftp_cls, tmp_key):
        """close() must call close() on the tunnel SSH client too."""
        call_count = [0]
        jump_inst = MagicMock()
        target_inst = MagicMock()

        def ssh_factory():
            call_count[0] += 1
            return jump_inst if call_count[0] == 1 else target_inst

        mock_ssh_cls.side_effect = lambda: ssh_factory()

        jump_transport = MagicMock()
        channel = MagicMock()
        jump_transport.open_channel.return_value = channel
        jump_inst.get_transport.return_value = jump_transport

        target_transport = MagicMock()
        target_transport.window_size = 0
        pkt = MagicMock()
        pkt.REKEY_BYTES = 0
        pkt.REKEY_VOLUME = 0
        target_transport.packetizer = pkt
        target_inst.get_transport.return_value = target_transport

        sftp_inst = MagicMock()
        mock_sftp_cls.from_transport.return_value = sftp_inst

        conn = self._make_conn(tmp_key)
        conn.tunnel = self._make_tunnel()
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            client.connect(conn)

        client.close()
        jump_inst.close.assert_called()
        assert client._tunnel_ssh is None

    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_tunnel_auth_failure_raises_authentication_error(self, mock_ssh_cls, tmp_key):
        """Jump-host auth failure → AuthenticationError, not ConnectionError."""
        import paramiko as _p

        jump_inst = MagicMock()
        mock_ssh_cls.return_value = jump_inst
        jump_inst.connect.side_effect = _p.AuthenticationException("bad credentials")

        conn = self._make_conn(tmp_key)
        conn.tunnel = self._make_tunnel()
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            with pytest.raises(AuthenticationError, match="Tunnel authentication"):
                client.connect(conn)

    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_tunnel_connection_failure_raises_connection_error(self, mock_ssh_cls, tmp_key):
        """Jump-host network failure → ConnectionError."""
        jump_inst = MagicMock()
        mock_ssh_cls.return_value = jump_inst
        jump_inst.connect.side_effect = OSError("connection refused")

        conn = self._make_conn(tmp_key)
        conn.tunnel = self._make_tunnel()
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            with pytest.raises(ConnectionError, match="Could not connect to tunnel host"):
                client.connect(conn)

    @patch("sftp_ui.core.sftp_client.paramiko.SFTPClient")
    @patch("sftp_ui.core.sftp_client.paramiko.SSHClient")
    def test_target_auth_failure_closes_tunnel(self, mock_ssh_cls, mock_sftp_cls, tmp_key):
        """If the target SSH auth fails, the jump-host session must also be closed."""
        import paramiko as _p
        call_count = [0]
        jump_inst = MagicMock()
        target_inst = MagicMock()

        def ssh_factory():
            call_count[0] += 1
            return jump_inst if call_count[0] == 1 else target_inst

        mock_ssh_cls.side_effect = lambda: ssh_factory()

        jump_transport = MagicMock()
        jump_transport.open_channel.return_value = MagicMock()
        jump_inst.get_transport.return_value = jump_transport

        target_inst.connect.side_effect = _p.AuthenticationException("bad key")

        conn = self._make_conn(tmp_key)
        conn.tunnel = self._make_tunnel()
        client = SFTPClient()

        with patch.object(client, "_load_pkey", return_value=MagicMock()):
            with pytest.raises(AuthenticationError):
                client.connect(conn)

        # Jump host must have been closed on rollback
        jump_inst.close.assert_called()
