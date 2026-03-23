"""
Tests for Connection dataclass and ConnectionStore.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from sftp_ui.core.connection import Connection, ConnectionStore


# ── Connection dataclass ─────────────────────────────────────────────────────

class TestConnection:
    def test_basic_creation(self, tmp_key):
        c = Connection(name="srv", host="1.2.3.4", user="root", key_path=tmp_key)
        assert c.port == 22
        assert c.id  # auto-generated

    def test_unique_ids(self, tmp_key):
        a = Connection(name="a", host="h", user="u", key_path=tmp_key)
        b = Connection(name="b", host="h", user="u", key_path=tmp_key)
        assert a.id != b.id

    def test_rejects_empty_name(self, tmp_key):
        with pytest.raises(ValueError, match="name"):
            Connection(name="", host="h", user="u", key_path=tmp_key)

    def test_rejects_empty_host(self, tmp_key):
        with pytest.raises(ValueError, match="host"):
            Connection(name="n", host="", user="u", key_path=tmp_key)

    def test_rejects_empty_user(self, tmp_key):
        with pytest.raises(ValueError, match="user"):
            Connection(name="n", host="h", user="", key_path=tmp_key)

    def test_rejects_invalid_port_zero(self, tmp_key):
        with pytest.raises(ValueError, match="port"):
            Connection(name="n", host="h", user="u", port=0, key_path=tmp_key)

    def test_rejects_invalid_port_too_high(self, tmp_key):
        with pytest.raises(ValueError, match="port"):
            Connection(name="n", host="h", user="u", port=99999, key_path=tmp_key)

    def test_rejects_relative_key_path(self):
        with pytest.raises(ValueError, match="absolute"):
            Connection(name="n", host="h", user="u", key_path="relative/key")

    def test_allows_none_key_path(self):
        c = Connection(name="n", host="h", user="u", password="s3cr3t")
        assert c.key_path is None

    def test_roundtrip_dict(self, tmp_key):
        c = Connection(name="s", host="h", user="u", key_path=tmp_key, port=2222)
        restored = Connection.from_dict(c.to_dict())
        assert restored.name == c.name
        assert restored.host == c.host
        assert restored.user == c.user
        assert restored.port == c.port
        assert restored.key_path == c.key_path
        assert restored.id == c.id

    def test_custom_port(self, tmp_key):
        c = Connection(name="n", host="h", user="u", port=2222, key_path=tmp_key)
        assert c.port == 2222


# ── ConnectionStore ──────────────────────────────────────────────────────────

class TestConnectionStore:
    def test_empty_on_new_file(self, tmp_path):
        store = ConnectionStore(path=tmp_path / "new.json")
        assert store.all() == []

    def test_add_and_retrieve(self, store, basic_conn):
        store.add(basic_conn)
        assert store.get(basic_conn.id) == basic_conn

    def test_all_returns_all(self, store, tmp_key):
        c1 = Connection(name="a", host="h1", user="u", key_path=tmp_key)
        c2 = Connection(name="b", host="h2", user="u", key_path=tmp_key)
        store.add(c1)
        store.add(c2)
        assert len(store.all()) == 2

    def test_add_duplicate_id_raises(self, store, basic_conn):
        store.add(basic_conn)
        with pytest.raises(ValueError, match="already exists"):
            store.add(basic_conn)

    def test_get_missing_raises(self, store):
        with pytest.raises(KeyError):
            store.get("nonexistent-id")

    def test_update(self, store, basic_conn, tmp_key):
        store.add(basic_conn)
        updated = Connection(
            name="renamed",
            host=basic_conn.host,
            user=basic_conn.user,
            key_path=tmp_key,
            id=basic_conn.id,
        )
        store.update(updated)
        assert store.get(basic_conn.id).name == "renamed"

    def test_update_missing_raises(self, store, basic_conn):
        with pytest.raises(KeyError):
            store.update(basic_conn)

    def test_remove(self, store, basic_conn):
        store.add(basic_conn)
        store.remove(basic_conn.id)
        assert store.all() == []

    def test_remove_missing_raises(self, store):
        with pytest.raises(KeyError):
            store.remove("ghost")

    def test_persists_to_disk(self, tmp_path, basic_conn):
        path = tmp_path / "c.json"
        s1 = ConnectionStore(path=path)
        s1.add(basic_conn)

        s2 = ConnectionStore(path=path)
        assert s2.get(basic_conn.id).name == basic_conn.name

    def test_survives_corrupt_json(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("NOT JSON", encoding="utf-8")
        store = ConnectionStore(path=path)  # must not raise
        assert store.all() == []

    def test_survives_partial_corrupt_entries(self, tmp_path, basic_conn):
        path = tmp_path / "c.json"
        good = basic_conn.to_dict()
        bad = {"broken": True}
        path.write_text(json.dumps([good, bad]), encoding="utf-8")
        store = ConnectionStore(path=path)
        assert len(store.all()) == 1
        assert store.get(basic_conn.id).name == basic_conn.name

    def test_find_by_name_found(self, store, basic_conn):
        store.add(basic_conn)
        result = store.find_by_name("test-server")
        assert result is not None
        assert result.id == basic_conn.id

    def test_find_by_name_not_found(self, store):
        assert store.find_by_name("ghost") is None

    def test_persistence_preserves_port(self, tmp_path, tmp_key):
        path = tmp_path / "c.json"
        c = Connection(name="n", host="h", user="u", port=2222, key_path=tmp_key)
        s1 = ConnectionStore(path=path)
        s1.add(c)
        s2 = ConnectionStore(path=path)
        assert s2.get(c.id).port == 2222


class TestConnectionNewFields:
    def test_default_favorite_is_false(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key)
        assert c.favorite is False

    def test_default_group_is_empty(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key)
        assert c.group == ""

    def test_default_last_connected_is_zero(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key)
        assert c.last_connected == 0.0

    def test_favorite_can_be_set(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key, favorite=True)
        assert c.favorite is True

    def test_group_can_be_set(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key, group="Production")
        assert c.group == "Production"

    def test_new_fields_roundtrip(self, tmp_key):
        c = Connection(name="n", host="h", user="u", key_path=tmp_key,
                       favorite=True, group="Dev", last_connected=1234567890.0)
        restored = Connection.from_dict(c.to_dict())
        assert restored.favorite is True
        assert restored.group == "Dev"
        assert restored.last_connected == 1234567890.0

    def test_from_dict_ignores_unknown_keys(self, tmp_key):
        """Old connection dicts (missing new fields) must load without error."""
        old_dict = {
            "name": "n", "host": "h", "user": "u",
            "port": 22, "key_path": tmp_key,
            "key_passphrase": None, "password": None,
            "id": "test-id",
        }
        c = Connection.from_dict(old_dict)
        assert c.favorite is False
        assert c.group == ""
        assert c.last_connected == 0.0

    def test_from_dict_rejects_completely_unknown_keys(self, tmp_key):
        """Extra unknown keys from future versions are silently dropped."""
        future_dict = {
            "name": "n", "host": "h", "user": "u",
            "port": 22, "key_path": tmp_key,
            "key_passphrase": None, "password": None,
            "id": "test-id",
            "future_field_xyz": "ignored",
        }
        c = Connection.from_dict(future_dict)
        assert c.name == "n"


class TestConnectionStoreRecordConnected:
    def test_record_connected_updates_timestamp(self, store, basic_conn):
        import time
        store.add(basic_conn)
        before = time.time()
        store.record_connected(basic_conn.id)
        after = time.time()
        ts = store.get(basic_conn.id).last_connected
        assert before <= ts <= after

    def test_record_connected_persists(self, tmp_path, basic_conn):
        path = tmp_path / "c.json"
        s1 = ConnectionStore(path=path)
        s1.add(basic_conn)
        s1.record_connected(basic_conn.id)
        ts = s1.get(basic_conn.id).last_connected

        s2 = ConnectionStore(path=path)
        assert s2.get(basic_conn.id).last_connected == ts

    def test_record_connected_missing_raises(self, store):
        with pytest.raises(KeyError):
            store.record_connected("ghost-id")
