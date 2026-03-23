"""
Tests for UIState — persistent lightweight UI state.

Covers: default values, save/load round-trip, local_path() fallback chain,
        remote_path() per-connection defaults, column_widths, was_connected,
        corrupt/missing file handling, OSError on save.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sftp_ui.core.ui_state import UIState


# ── helpers ───────────────────────────────────────────────────────────────────

def _state(tmp_path) -> UIState:
    return UIState(path=tmp_path / "ui_state.json")


# ── defaults ──────────────────────────────────────────────────────────────────

class TestUIStateDefaults:
    def test_last_local_path_defaults_to_home(self, tmp_path):
        state = _state(tmp_path)
        assert state.last_local_path == str(Path.home())

    def test_last_connection_id_defaults_to_none(self, tmp_path):
        state = _state(tmp_path)
        assert state.last_connection_id is None

    def test_last_remote_paths_defaults_empty(self, tmp_path):
        state = _state(tmp_path)
        assert state.last_remote_paths == {}

    def test_was_connected_defaults_false(self, tmp_path):
        state = _state(tmp_path)
        assert state.was_connected is False

    def test_column_widths_defaults_empty(self, tmp_path):
        state = _state(tmp_path)
        assert state.column_widths == {}


# ── save / load round-trip ────────────────────────────────────────────────────

class TestUIStatePersistence:
    def test_save_and_reload_local_path(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        state.last_local_path = str(tmp_path)
        state.save()
        loaded = UIState(path=p)
        assert loaded.last_local_path == str(tmp_path)

    def test_save_and_reload_connection_id(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        state.last_connection_id = "abc-123"
        state.save()
        loaded = UIState(path=p)
        assert loaded.last_connection_id == "abc-123"

    def test_save_and_reload_remote_paths(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        state.last_remote_paths = {"conn1": "/data", "conn2": "/home/user"}
        state.save()
        loaded = UIState(path=p)
        assert loaded.last_remote_paths == {"conn1": "/data", "conn2": "/home/user"}

    def test_save_and_reload_was_connected(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        state.was_connected = True
        state.save()
        loaded = UIState(path=p)
        assert loaded.was_connected is True

    def test_save_and_reload_column_widths(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        state.column_widths = {"local": [200, 80, 120], "remote": [250, 100]}
        state.save()
        loaded = UIState(path=p)
        assert loaded.column_widths == {"local": [200, 80, 120], "remote": [250, 100]}

    def test_missing_file_uses_defaults(self, tmp_path):
        """Loading from a non-existent file gives default state."""
        state = UIState(path=tmp_path / "nonexistent.json")
        assert state.last_connection_id is None
        assert state.was_connected is False

    def test_corrupt_json_uses_defaults(self, tmp_path):
        p = tmp_path / "ui_state.json"
        p.write_text("{ not valid json !!!", encoding="utf-8")
        state = UIState(path=p)  # must not raise
        assert state.was_connected is False

    def test_oserror_on_save_does_not_crash(self, tmp_path):
        p = tmp_path / "ui_state.json"
        state = UIState(path=p)
        with patch.object(Path, "write_text", side_effect=OSError("no space")):
            state.save()  # must not raise

    def test_save_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "state.json"
        state = UIState(path=nested)
        state.save()
        assert nested.exists()


# ── mutators trigger save ─────────────────────────────────────────────────────

class TestUIStateMutators:
    def test_set_local_path_persists(self, tmp_path):
        p = tmp_path / "state.json"
        state = UIState(path=p)
        state.set_local_path(str(tmp_path))
        loaded = UIState(path=p)
        assert loaded.last_local_path == str(tmp_path)

    def test_set_remote_path_persists(self, tmp_path):
        p = tmp_path / "state.json"
        state = UIState(path=p)
        state.set_remote_path("conn-42", "/remote/dir")
        loaded = UIState(path=p)
        assert loaded.remote_path("conn-42") == "/remote/dir"

    def test_set_last_connection_persists(self, tmp_path):
        p = tmp_path / "state.json"
        state = UIState(path=p)
        state.set_last_connection("my-id")
        loaded = UIState(path=p)
        assert loaded.last_connection_id == "my-id"

    def test_set_was_connected_persists(self, tmp_path):
        p = tmp_path / "state.json"
        state = UIState(path=p)
        state.set_was_connected(True)
        loaded = UIState(path=p)
        assert loaded.was_connected is True

    def test_set_column_widths_persists(self, tmp_path):
        p = tmp_path / "state.json"
        state = UIState(path=p)
        state.set_column_widths("remote", [150, 60])
        loaded = UIState(path=p)
        assert loaded.get_column_widths("remote") == [150, 60]

    def test_get_column_widths_missing_panel_returns_empty(self, tmp_path):
        state = _state(tmp_path)
        assert state.get_column_widths("unknown_panel") == []


# ── local_path() fallback chain ───────────────────────────────────────────────

class TestLocalPathFallback:
    def test_existing_dir_returned_as_is(self, tmp_path):
        state = _state(tmp_path)
        state.last_local_path = str(tmp_path)
        assert state.local_path() == str(tmp_path)

    def test_missing_path_falls_back_to_existing_ancestor(self, tmp_path):
        sub = tmp_path / "gone" / "deeper"
        state = _state(tmp_path)
        state.last_local_path = str(sub)
        # sub doesn't exist, but tmp_path does — should return tmp_path
        result = state.local_path()
        assert Path(result).is_dir()
        # tmp_path should be an ancestor of sub
        assert str(tmp_path) in result or result == str(tmp_path)

    def test_file_path_treated_as_missing(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        state = _state(tmp_path)
        state.last_local_path = str(f)
        # f is a file not a dir — should fall back to tmp_path
        result = state.local_path()
        assert Path(result).is_dir()

    def test_returns_home_when_nothing_exists(self, tmp_path):
        state = _state(tmp_path)
        # Use an impossible path that definitely doesn't exist
        state.last_local_path = "/nonexistent_path_xyz_12345_abc"
        result = state.local_path()
        assert Path(result).is_dir()


# ── remote_path() ─────────────────────────────────────────────────────────────

class TestRemotePath:
    def test_unknown_connection_returns_slash(self, tmp_path):
        state = _state(tmp_path)
        assert state.remote_path("unknown") == "/"

    def test_known_connection_returns_saved_path(self, tmp_path):
        state = _state(tmp_path)
        state.last_remote_paths["server1"] = "/data/files"
        assert state.remote_path("server1") == "/data/files"

    def test_multiple_connections_independent(self, tmp_path):
        state = _state(tmp_path)
        state.set_remote_path("c1", "/path/a")
        state.set_remote_path("c2", "/path/b")
        assert state.remote_path("c1") == "/path/a"
        assert state.remote_path("c2") == "/path/b"


# ── load robustness ───────────────────────────────────────────────────────────

class TestUIStateLoadRobustness:
    def test_non_dict_remote_paths_ignored(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"last_remote_paths": "not-a-dict"}))
        state = UIState(path=p)
        assert state.last_remote_paths == {}

    def test_column_widths_with_non_numeric_entries_skipped(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({
            "column_widths": {"good": [100, 200], "bad": ["x", "y"]}
        }))
        state = UIState(path=p)
        assert state.column_widths.get("good") == [100, 200]
        assert "bad" not in state.column_widths

    def test_extra_fields_in_json_ignored(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"unknown_future_field": 42, "was_connected": True}))
        state = UIState(path=p)
        assert state.was_connected is True
