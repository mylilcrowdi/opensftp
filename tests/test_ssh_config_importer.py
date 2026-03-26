"""
Tests for SSHConfigImporter.

Covers:
  - Basic host entry (HostName, User, Port, IdentityFile)
  - Alias-only entry (no explicit HostName -> alias used as hostname)
  - Wildcard hosts are skipped
  - Missing User is skipped
  - ProxyJump: single hop parsed into TunnelConfig
  - ProxyJump: multi-hop uses only first jump
  - ProxyJump: "none" is ignored
  - ProxyJump: user@host:port notation
  - ProxyJump resolved via SSH config for jump host details
  - IdentityFile: ~ expansion, list form
  - Non-existent config path returns empty list
  - Malformed port falls back to 22
  - IPv6 host in ProxyJump bracket notation
  - _parse_host_string static method edge cases
  - Round-trip: Connection fields match parsed values
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from sftp_ui.core.connection import Connection, TunnelConfig
from sftp_ui.core.ssh_config_importer import SSHConfigImporter


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_config(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "ssh_config"
    cfg.write_text(textwrap.dedent(content), encoding="utf-8")
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# Basic host parsing
# ══════════════════════════════════════════════════════════════════════════════

class TestBasicHost:
    def test_simple_entry(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host myserver
                HostName 10.0.0.1
                User alice
                Port 2222
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert len(conns) == 1
        c = conns[0]
        assert c.name == "myserver"
        assert c.host == "10.0.0.1"
        assert c.user == "alice"
        assert c.port == 2222

    def test_alias_used_as_hostname_when_no_hostname(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host prod.example.com
                User deploy
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert len(conns) == 1
        assert conns[0].host == "prod.example.com"

    def test_default_port_22(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host mybox
                HostName 192.168.1.1
                User root
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].port == 22

    def test_malformed_port_falls_back_to_22(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host mybox
                HostName 192.168.1.1
                User root
                Port notanumber
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].port == 22

    def test_multiple_hosts(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host alpha
                HostName 1.2.3.4
                User alice

            Host beta
                HostName 5.6.7.8
                User bob
                Port 2200
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        names = {c.name for c in conns}
        assert names == {"alpha", "beta"}

    def test_returns_connection_instances(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host srv
                HostName 1.2.3.4
                User x
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert all(isinstance(c, Connection) for c in conns)


# ══════════════════════════════════════════════════════════════════════════════
# Skipped / invalid entries
# ══════════════════════════════════════════════════════════════════════════════

class TestSkippedEntries:
    def test_wildcard_host_skipped(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host *
                ServerAliveInterval 60

            Host mybox
                HostName 1.2.3.4
                User root
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert all(c.name != "*" for c in conns)
        assert len(conns) == 1

    def test_glob_pattern_host_skipped(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host *.example.com
                User admin

            Host real
                HostName 1.2.3.4
                User root
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert len(conns) == 1
        assert conns[0].name == "real"

    def test_entry_without_user_skipped(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host nouser
                HostName 1.2.3.4
        """)
        # Patch out USER env var to ensure no fallback
        with patch.dict(os.environ, {"USER": ""}):
            conns = SSHConfigImporter(cfg).import_connections()
        assert len(conns) == 0

    def test_nonexistent_config_returns_empty(self, tmp_path):
        conns = SSHConfigImporter(tmp_path / "nonexistent_config").import_connections()
        assert conns == []

    def test_empty_config_returns_empty(self, tmp_path):
        cfg = _write_config(tmp_path, "")
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns == []


# ══════════════════════════════════════════════════════════════════════════════
# IdentityFile
# ══════════════════════════════════════════════════════════════════════════════

class TestIdentityFile:
    def test_identity_file_resolved_to_absolute(self, tmp_path):
        key = tmp_path / "id_ed25519"
        key.write_bytes(b"dummy")
        cfg = _write_config(tmp_path, f"""
            Host srv
                HostName 1.2.3.4
                User alice
                IdentityFile {key}
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].key_path == str(key)

    def test_tilde_in_identity_file_expanded(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host srv
                HostName 1.2.3.4
                User alice
                IdentityFile ~/.ssh/id_rsa
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].key_path is not None
        assert "~" not in conns[0].key_path
        assert conns[0].key_path.startswith("/")

    def test_no_identity_file_gives_none(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host srv
                HostName 1.2.3.4
                User alice
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].key_path is None


# ══════════════════════════════════════════════════════════════════════════════
# ProxyJump -> TunnelConfig
# ══════════════════════════════════════════════════════════════════════════════

class TestProxyJump:
    def test_proxyjump_creates_tunnel(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.10.0.5
                User dev
                ProxyJump bastion.example.com

            Host bastion.example.com
                User ec2-user
                Port 22
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        target = next(c for c in conns if c.name == "target")
        assert target.tunnel is not None
        assert isinstance(target.tunnel, TunnelConfig)
        assert target.tunnel.host == "bastion.example.com"

    def test_proxyjump_user_at_host(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.0.0.1
                User admin
                ProxyJump jumpuser@jumphost.net
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        c = conns[0]
        assert c.tunnel is not None
        assert c.tunnel.user == "jumpuser"
        assert c.tunnel.host == "jumphost.net"

    def test_proxyjump_user_at_host_colon_port(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.0.0.1
                User admin
                ProxyJump jumpuser@jumphost.net:2222
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        c = conns[0]
        assert c.tunnel is not None
        assert c.tunnel.user == "jumpuser"
        assert c.tunnel.host == "jumphost.net"
        assert c.tunnel.port == 2222

    def test_proxyjump_multihop_uses_first(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.0.0.1
                User admin
                ProxyJump first@first.host,second@second.host
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        c = conns[0]
        assert c.tunnel is not None
        assert c.tunnel.host == "first.host"

    def test_proxyjump_none_ignored(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.0.0.1
                User admin
                ProxyJump none
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].tunnel is None

    def test_proxyjump_inherits_port_from_jump_config(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host target
                HostName 10.0.0.2
                User dev
                ProxyJump bastion

            Host bastion
                HostName bastion.internal
                User jumper
                Port 2222
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        target = next(c for c in conns if c.name == "target")
        assert target.tunnel is not None
        assert target.tunnel.port == 2222
        assert target.tunnel.user == "jumper"
        assert target.tunnel.host == "bastion.internal"

    def test_no_proxyjump_gives_no_tunnel(self, tmp_path):
        cfg = _write_config(tmp_path, """
            Host direct
                HostName 1.2.3.4
                User alice
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert conns[0].tunnel is None


# ══════════════════════════════════════════════════════════════════════════════
# _parse_host_string
# ══════════════════════════════════════════════════════════════════════════════

class TestParseHostString:
    def _parse(self, value):
        return SSHConfigImporter._parse_host_string(value)

    def test_plain_hostname(self):
        user, host, port = self._parse("myhost.com")
        assert user == ""
        assert host == "myhost.com"
        assert port == 22

    def test_user_at_host(self):
        user, host, port = self._parse("alice@myhost.com")
        assert user == "alice"
        assert host == "myhost.com"
        assert port == 22

    def test_host_colon_port(self):
        user, host, port = self._parse("myhost.com:2222")
        assert host == "myhost.com"
        assert port == 2222

    def test_user_at_host_colon_port(self):
        user, host, port = self._parse("alice@myhost.com:2222")
        assert user == "alice"
        assert host == "myhost.com"
        assert port == 2222

    def test_ipv6_bracket_notation(self):
        user, host, port = self._parse("[::1]:2222")
        assert host == "::1"
        assert port == 2222

    def test_ipv6_no_port(self):
        user, host, port = self._parse("[::1]")
        assert host == "::1"
        assert port == 22

    def test_invalid_port_ignored(self):
        user, host, port = self._parse("myhost.com:notaport")
        assert host == "myhost.com:notaport"
        assert port == 22


# ══════════════════════════════════════════════════════════════════════════════
# Integration: round-trip field values
# ══════════════════════════════════════════════════════════════════════════════

class TestRoundTrip:
    def test_all_fields_preserved(self, tmp_path):
        key = tmp_path / "id_rsa"
        key.write_bytes(b"fake")
        cfg = _write_config(tmp_path, f"""
            Host prod
                HostName prod.example.com
                User deploy
                Port 2200
                IdentityFile {key}
                ProxyJump gw@gateway.example.com:22
        """)
        conns = SSHConfigImporter(cfg).import_connections()
        assert len(conns) == 1
        c = conns[0]
        assert c.name == "prod"
        assert c.host == "prod.example.com"
        assert c.user == "deploy"
        assert c.port == 2200
        assert c.key_path == str(key)
        assert c.tunnel is not None
        assert c.tunnel.host == "gateway.example.com"
        assert c.tunnel.user == "gw"
        assert c.tunnel.port == 22
        assert c.protocol == "sftp"
        assert c.id  # uuid generated
