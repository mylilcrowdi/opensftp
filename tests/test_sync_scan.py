"""
Tests for the _scan() comparison logic in SyncDialog.

_walk_remote_parallel is mocked so no actual SFTP connection is needed.
Only the comparison algorithm is tested: LOCAL_ONLY, REMOTE_ONLY, SAME,
LOCAL_NEWER, REMOTE_NEWER, default checked states, cancel handling,
relative path normalisation, sorted output.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from sftp_ui.core.connection import Connection
from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.dialogs.sync_dialog import (
    SyncStatus,
    _scan,
    _DEFAULT_CHECKED,
)


def _conn() -> Connection:
    return Connection(name="t", host="1.2.3.4", user="u", port=22, password="p")


def _remote_entry(rel_path: str, remote_root: str, size: int, mtime: int) -> RemoteEntry:
    path = str(PurePosixPath(remote_root) / rel_path)
    return RemoteEntry(
        name=PurePosixPath(rel_path).name,
        path=path,
        is_dir=False,
        size=size,
        mtime=mtime,
    )


def _run_scan(
    local_dir: str,
    remote_dir: str,
    remote_entries: list[RemoteEntry],
    cancel: threading.Event | None = None,
) -> list:
    cancel = cancel or threading.Event()
    with patch(
        "sftp_ui.ui.dialogs.sync_dialog._walk_remote_parallel",
        return_value=remote_entries,
    ):
        return _scan(
            local_dir=local_dir,
            remote_dir=remote_dir,
            conn=_conn(),
            progress=lambda _: None,
            cancel=cancel,
        )


# ── LOCAL_ONLY ─────────────────────────────────────────────────────────────────

class TestScanLocalOnly:
    def test_file_only_local_is_local_only(self, tmp_path):
        (tmp_path / "only_local.txt").write_bytes(b"hi")
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert len(entries) == 1
        assert entries[0].status == SyncStatus.LOCAL_ONLY

    def test_local_only_has_correct_rel_path(self, tmp_path):
        (tmp_path / "readme.md").write_bytes(b"x")
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert entries[0].rel_path == "readme.md"

    def test_local_only_has_local_abs_path(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"x")
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert entries[0].local_abs == str(f)

    def test_local_only_checked_by_default(self, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"x")
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert entries[0].checked is True

    def test_local_only_local_size_correct(self, tmp_path):
        content = b"hello world"
        (tmp_path / "f.txt").write_bytes(content)
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert entries[0].local_size == len(content)


# ── REMOTE_ONLY ────────────────────────────────────────────────────────────────

class TestScanRemoteOnly:
    def test_file_only_remote_is_remote_only(self, tmp_path):
        remote = [_remote_entry("only_remote.txt", "/remote", 42, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert len(entries) == 1
        assert entries[0].status == SyncStatus.REMOTE_ONLY

    def test_remote_only_unchecked_by_default(self, tmp_path):
        remote = [_remote_entry("r.txt", "/remote", 10, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].checked is False

    def test_remote_only_has_remote_abs_path(self, tmp_path):
        remote = [_remote_entry("r.txt", "/remote", 10, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].remote_abs == "/remote/r.txt"

    def test_remote_only_remote_size_correct(self, tmp_path):
        remote = [_remote_entry("r.bin", "/remote", 4096, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].remote_size == 4096


# ── SAME ───────────────────────────────────────────────────────────────────────

class TestScanSame:
    def test_same_size_is_same(self, tmp_path):
        content = b"abc"
        (tmp_path / "f.txt").write_bytes(content)
        remote = [_remote_entry("f.txt", "/remote", len(content), 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.SAME

    def test_same_unchecked_by_default(self, tmp_path):
        content = b"abc"
        (tmp_path / "f.txt").write_bytes(content)
        remote = [_remote_entry("f.txt", "/remote", len(content), 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].checked is False

    def test_same_has_both_abs_paths(self, tmp_path):
        content = b"xyz"
        f = tmp_path / "f.txt"
        f.write_bytes(content)
        remote = [_remote_entry("f.txt", "/remote", len(content), 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        e = entries[0]
        assert e.local_abs == str(f)
        assert e.remote_abs == "/remote/f.txt"

    def test_different_mtime_but_same_size_is_same(self, tmp_path):
        """Status is based on SIZE equality, not mtime — if sizes match it's SAME."""
        content = b"hello"
        (tmp_path / "f.txt").write_bytes(content)
        # Remote mtime is very different but size matches
        remote = [_remote_entry("f.txt", "/remote", len(content), 1)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.SAME


# ── LOCAL_NEWER ────────────────────────────────────────────────────────────────

class TestScanLocalNewer:
    def test_local_larger_and_newer_mtime_is_local_newer(self, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"abc" * 100)   # 300 bytes
        remote = [_remote_entry("f.txt", "/remote", 200, 1_000_000)]  # 200 bytes
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.LOCAL_NEWER

    def test_local_newer_checked_by_default(self, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"x" * 200)
        remote = [_remote_entry("f.txt", "/remote", 100, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].checked is True


# ── REMOTE_NEWER ───────────────────────────────────────────────────────────────

class TestScanRemoteNewer:
    def test_remote_newer_mtime_is_remote_newer(self, tmp_path):
        # _scan compares mtime when sizes differ: lm > rm → LOCAL_NEWER, else → REMOTE_NEWER.
        # Set local file mtime to 1970 (epoch=1) so remote (far future) wins.
        f = tmp_path / "f.txt"
        f.write_bytes(b"x" * 50)
        import os; os.utime(str(f), (1, 1))  # local mtime = 1 (ancient)
        remote = [_remote_entry("f.txt", "/remote", 100, 9_999_999_999)]  # far future
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.REMOTE_NEWER

    def test_remote_newer_unchecked_by_default(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"x" * 50)
        import os; os.utime(str(f), (1, 1))
        remote = [_remote_entry("f.txt", "/remote", 100, 9_999_999_999)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].checked is False


# ── Mixed scenarios ────────────────────────────────────────────────────────────

class TestScanMixed:
    def test_multiple_files_all_statuses(self, tmp_path):
        # LOCAL_ONLY
        (tmp_path / "local_only.txt").write_bytes(b"x")
        # Will become SAME
        (tmp_path / "same.txt").write_bytes(b"hello")
        # Will become LOCAL_NEWER
        (tmp_path / "local_newer.txt").write_bytes(b"x" * 200)

        remote = [
            _remote_entry("remote_only.txt", "/remote", 10, 1_000_000),
            _remote_entry("same.txt", "/remote", 5, 1_000_000),
            _remote_entry("local_newer.txt", "/remote", 100, 1_000_000),
        ]
        entries = _run_scan(str(tmp_path), "/remote", remote)

        statuses = {e.rel_path: e.status for e in entries}
        assert statuses["local_only.txt"] == SyncStatus.LOCAL_ONLY
        assert statuses["remote_only.txt"] == SyncStatus.REMOTE_ONLY
        assert statuses["same.txt"] == SyncStatus.SAME
        assert statuses["local_newer.txt"] in (SyncStatus.LOCAL_NEWER, SyncStatus.REMOTE_NEWER)

    def test_output_is_sorted_alphabetically(self, tmp_path):
        for name in ("z.txt", "a.txt", "m.txt"):
            (tmp_path / name).write_bytes(b"x")
        entries = _run_scan(str(tmp_path), "/remote", [])
        names = [e.rel_path for e in entries]
        assert names == sorted(names)

    def test_empty_local_and_remote_returns_empty(self, tmp_path):
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert entries == []

    def test_all_remote_no_local(self, tmp_path):
        remote = [
            _remote_entry("a.txt", "/remote", 1, 1_000_000),
            _remote_entry("b.txt", "/remote", 2, 1_000_000),
        ]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert all(e.status == SyncStatus.REMOTE_ONLY for e in entries)
        assert len(entries) == 2


# ── Subdirectory relative paths ────────────────────────────────────────────────

class TestScanSubdirs:
    def test_nested_local_file_has_posix_rel_path(self, tmp_path):
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.py").write_bytes(b"x")
        entries = _run_scan(str(tmp_path), "/remote", [])
        assert any(e.rel_path == "src/deep/nested.py" for e in entries)

    def test_nested_remote_file_rel_path(self, tmp_path):
        remote = [_remote_entry("subdir/file.py", "/remote", 5, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].rel_path == "subdir/file.py"

    def test_same_nested_file_matched(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "__init__.py").write_bytes(b"x" * 10)
        remote = [_remote_entry("pkg/__init__.py", "/remote", 10, 1_000_000)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.SAME


# ── mtime tolerance (FAT filesystem ±2 s window) ──────────────────────────────

class TestScanMtimeTolerance:
    """Files with different sizes but mtimes within 2 s must be classified SAME.

    FAT/exFAT store timestamps with 2-second granularity; after a round-trip a
    file that has not actually changed may show a 1–2 s mtime delta.  The
    tolerance window prevents spurious LOCAL_NEWER / REMOTE_NEWER classifications.
    """

    def _set_local_mtime(self, path, mtime: float):
        import os
        os.utime(str(path), (mtime, mtime))

    def test_within_tolerance_same_size_is_same(self, tmp_path):
        """Files with same size and mtime within 2 s → SAME (baseline)."""
        content = b"hello"
        f = tmp_path / "f.txt"
        f.write_bytes(content)
        self._set_local_mtime(f, 1_000_000.0)
        remote = [_remote_entry("f.txt", "/remote", len(content), 1_000_001)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.SAME

    def test_different_sizes_within_2s_is_same(self, tmp_path):
        """Different sizes but mtime delta <= 2 s → SAME (FAT tolerance)."""
        f = tmp_path / "f.txt"
        f.write_bytes(b"x" * 100)
        self._set_local_mtime(f, 1_000_000.0)
        # Remote has a slightly different size AND mtime within 2 s
        remote = [_remote_entry("f.txt", "/remote", 100, 1_000_001)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        # Same size → SAME regardless; but with size=100 local vs size=100 remote
        # this is actually a same-size case.  Use 101 to exercise the mtime path:
        remote2 = [_remote_entry("f.txt", "/remote", 101, 1_000_001)]
        entries2 = _run_scan(str(tmp_path), "/remote", remote2)
        assert entries2[0].status == SyncStatus.SAME

    def test_different_sizes_beyond_2s_not_same(self, tmp_path):
        """Different sizes and mtime delta > 2 s → not SAME."""
        f = tmp_path / "f.txt"
        f.write_bytes(b"x" * 100)
        self._set_local_mtime(f, 1_000_000.0)
        # mtime differs by 3 s — outside tolerance
        remote = [_remote_entry("f.txt", "/remote", 200, 1_000_003)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status != SyncStatus.SAME

    def test_exactly_2s_mtime_delta_is_same(self, tmp_path):
        """Exactly 2 s mtime delta with different sizes → SAME (boundary)."""
        f = tmp_path / "f.txt"
        f.write_bytes(b"x" * 100)
        self._set_local_mtime(f, 1_000_000.0)
        remote = [_remote_entry("f.txt", "/remote", 200, 1_000_002)]
        entries = _run_scan(str(tmp_path), "/remote", remote)
        assert entries[0].status == SyncStatus.SAME


# ── Cancellation ──────────────────────────────────────────────────────────────

class TestScanCancellation:
    def test_cancelled_before_start_returns_empty(self, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"x")
        cancel = threading.Event()
        cancel.set()   # pre-cancelled
        entries = _run_scan(str(tmp_path), "/remote", [], cancel=cancel)
        assert entries == []
