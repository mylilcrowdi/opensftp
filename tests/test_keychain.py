"""
Tests for keychain / secure credential storage — Feature 5.

Covers:
  - Passwords are stored in the keychain when keyring is available
  - JSON file contains __keychain__ sentinel instead of plaintext password
  - Reloading the store retrieves the password from keyring
  - When keyring raises, password falls back to plaintext
  - key_passphrase is also stored in keychain
"""
from __future__ import annotations

import json
import unittest.mock as mock
from pathlib import Path

import pytest

from sftp_ui.core.connection import Connection, ConnectionStore, _KEYCHAIN_SENTINEL


class TestKeychainSave:
    def test_password_replaced_with_sentinel_in_json(self, tmp_path):
        store_path = tmp_path / "c.json"

        fake_keyring = mock.MagicMock()
        fake_keyring.set_password = mock.MagicMock()
        fake_keyring.get_password = mock.MagicMock(return_value="s3cr3t")

        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", True), \
             mock.patch("sftp_ui.core.connection._keyring", fake_keyring):
            store = ConnectionStore(path=store_path)
            c = Connection(name="srv", host="h", user="u", password="s3cr3t")
            store.add(c)

        raw = json.loads(store_path.read_text())
        saved_passwords = [item.get("password") for item in raw]
        assert all(p == _KEYCHAIN_SENTINEL for p in saved_passwords), \
            f"Expected sentinel in JSON, got {saved_passwords}"

    def test_keychain_set_password_called(self, tmp_path):
        store_path = tmp_path / "c.json"
        fake_keyring = mock.MagicMock()

        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", True), \
             mock.patch("sftp_ui.core.connection._keyring", fake_keyring):
            store = ConnectionStore(path=store_path)
            c = Connection(name="srv", host="h", user="u", password="hunter2")
            store.add(c)

        fake_keyring.set_password.assert_called()
        call_args = fake_keyring.set_password.call_args_list
        assert any("password" in str(args) for args in call_args)

    def test_key_passphrase_also_stored_in_keychain(self, tmp_path):
        store_path = tmp_path / "c.json"
        fake_keyring = mock.MagicMock()

        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False) as f:
            key_path = f.name

        try:
            with mock.patch("sftp_ui.core.connection._HAS_KEYRING", True), \
                 mock.patch("sftp_ui.core.connection._keyring", fake_keyring):
                store = ConnectionStore(path=store_path)
                c = Connection(name="srv", host="h", user="u",
                               key_path=key_path, key_passphrase="mysecretpass")
                store.add(c)

            raw = json.loads(store_path.read_text())
            passphrases = [item.get("key_passphrase") for item in raw]
            assert all(p == _KEYCHAIN_SENTINEL for p in passphrases)
        finally:
            os.unlink(key_path)

    def test_password_fallback_to_plaintext_on_keyring_error(self, tmp_path):
        store_path = tmp_path / "c.json"
        fake_keyring = mock.MagicMock()
        fake_keyring.set_password.side_effect = RuntimeError("no keychain")

        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", True), \
             mock.patch("sftp_ui.core.connection._keyring", fake_keyring):
            store = ConnectionStore(path=store_path)
            c = Connection(name="srv", host="h", user="u", password="plain")
            store.add(c)

        raw = json.loads(store_path.read_text())
        # Should fall back to storing plaintext when keyring fails
        assert raw[0]["password"] == "plain"


class TestKeychainLoad:
    def test_sentinel_resolved_on_load(self, tmp_path):
        store_path = tmp_path / "c.json"
        fake_keyring = mock.MagicMock()
        fake_keyring.set_password = mock.MagicMock()
        fake_keyring.get_password = mock.MagicMock(return_value="loaded_secret")

        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", True), \
             mock.patch("sftp_ui.core.connection._keyring", fake_keyring):
            store = ConnectionStore(path=store_path)
            c = Connection(name="srv", host="h", user="u", password="original")
            store.add(c)

            # Reload
            store2 = ConnectionStore(path=store_path)
            loaded = store2.get(c.id)

        assert loaded.password == "loaded_secret"

    def test_no_keyring_keeps_plaintext(self, tmp_path):
        store_path = tmp_path / "c.json"

        # Save without keyring
        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", False):
            store = ConnectionStore(path=store_path)
            c = Connection(name="srv", host="h", user="u", password="plain")
            store.add(c)

        raw = json.loads(store_path.read_text())
        assert raw[0]["password"] == "plain"

        # Reload without keyring
        with mock.patch("sftp_ui.core.connection._HAS_KEYRING", False):
            store2 = ConnectionStore(path=store_path)
            loaded = store2.get(c.id)
        assert loaded.password == "plain"
