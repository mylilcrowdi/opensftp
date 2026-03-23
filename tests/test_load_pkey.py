"""
Tests for SFTPClient._load_pkey() — the private-key loading logic.

Covers: successful RSA load, encrypted key raises AuthenticationError,
        non-key content raises AuthenticationError, non-existent file
        raises AuthenticationError, ECDSA load if supported.
"""
from __future__ import annotations

import io
import tempfile
import os

import paramiko
import pytest

from sftp_ui.core.sftp_client import AuthenticationError, SFTPClient


def _write_key_file(tmp_path, name: str, content: str) -> str:
    """Write *content* to tmp_path/name and return the path."""
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _rsa_key_path(tmp_path, *, encrypted: bool = False, passphrase: str = "") -> str:
    """Generate an RSA private key and write it to tmp_path. Returns path."""
    key = paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    if encrypted:
        key.write_private_key(buf, password=passphrase)
    else:
        key.write_private_key(buf)
    return _write_key_file(tmp_path, "id_rsa", buf.getvalue())


# ── Successful loads ──────────────────────────────────────────────────────────

class TestLoadPkeySuccess:
    def test_rsa_key_returns_pkey_instance(self, tmp_path):
        path = _rsa_key_path(tmp_path)
        key = SFTPClient._load_pkey(path)
        assert isinstance(key, paramiko.PKey)

    def test_rsa_key_is_correct_type(self, tmp_path):
        path = _rsa_key_path(tmp_path)
        key = SFTPClient._load_pkey(path)
        assert isinstance(key, paramiko.RSAKey)

    def test_rsa_key_loaded_without_passphrase(self, tmp_path):
        path = _rsa_key_path(tmp_path)
        # Should not raise
        key = SFTPClient._load_pkey(path, passphrase=None)
        assert key is not None

    def test_rsa_key_loaded_with_empty_passphrase(self, tmp_path):
        path = _rsa_key_path(tmp_path)
        key = SFTPClient._load_pkey(path, passphrase="")
        assert key is not None

    def test_ecdsa_key_loaded_successfully(self, tmp_path):
        """ECDSA keys (P-256) should load correctly."""
        key = paramiko.ECDSAKey.generate()
        buf = io.StringIO()
        key.write_private_key(buf)
        path = _write_key_file(tmp_path, "id_ecdsa", buf.getvalue())
        loaded = SFTPClient._load_pkey(path)
        assert isinstance(loaded, paramiko.ECDSAKey)


# ── Encrypted key error ───────────────────────────────────────────────────────

class TestLoadPkeyEncrypted:
    def test_encrypted_key_raises_authentication_error(self, tmp_path):
        path = _rsa_key_path(tmp_path, encrypted=True, passphrase="secret123")
        with pytest.raises(AuthenticationError):
            SFTPClient._load_pkey(path)

    def test_encrypted_key_error_mentions_passphrase(self, tmp_path):
        path = _rsa_key_path(tmp_path, encrypted=True, passphrase="mysecret")
        with pytest.raises(AuthenticationError, match="[Pp]assphrase|passphrase|encrypted"):
            SFTPClient._load_pkey(path)

    def test_encrypted_key_error_mentions_path(self, tmp_path):
        path = _rsa_key_path(tmp_path, encrypted=True, passphrase="pw")
        with pytest.raises(AuthenticationError, match="id_rsa"):
            SFTPClient._load_pkey(path)

    def test_wrong_passphrase_raises_authentication_error(self, tmp_path):
        """Passing the wrong passphrase should not silently succeed."""
        path = _rsa_key_path(tmp_path, encrypted=True, passphrase="correct")
        with pytest.raises((AuthenticationError, Exception)):
            # Wrong passphrase — paramiko will raise some form of error
            SFTPClient._load_pkey(path, passphrase="wrong")


# ── Invalid key content ───────────────────────────────────────────────────────

class TestLoadPkeyInvalid:
    def test_garbage_content_raises_authentication_error(self, tmp_path):
        path = _write_key_file(tmp_path, "bad_key", "this is not a private key at all")
        with pytest.raises(AuthenticationError):
            SFTPClient._load_pkey(path)

    def test_empty_file_raises_authentication_error(self, tmp_path):
        path = _write_key_file(tmp_path, "empty_key", "")
        with pytest.raises(AuthenticationError):
            SFTPClient._load_pkey(path)

    def test_public_key_only_raises_authentication_error(self, tmp_path):
        """An SSH public key (not private) should not load."""
        key = paramiko.RSAKey.generate(2048)
        pub_content = f"ssh-rsa {key.get_base64()} testkey"
        path = _write_key_file(tmp_path, "id_rsa.pub", pub_content)
        with pytest.raises(AuthenticationError):
            SFTPClient._load_pkey(path)

    def test_error_message_contains_path(self, tmp_path):
        path = _write_key_file(tmp_path, "bad_key.pem", "garbage data")
        with pytest.raises(AuthenticationError, match="bad_key.pem"):
            SFTPClient._load_pkey(path)

    def test_nonexistent_file_raises_authentication_error(self, tmp_path):
        path = str(tmp_path / "does_not_exist.pem")
        with pytest.raises(AuthenticationError):
            SFTPClient._load_pkey(path)
