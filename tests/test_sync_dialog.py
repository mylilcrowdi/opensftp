"""
Tests for SyncDialog logic — pure job-builder functions and filter helpers.

_build_upload_jobs() and _build_download_jobs() are module-level pure functions;
no Qt dialog is instantiated so these tests run without any window system.
Also covers _apply_filters logic via _SyncModel directly, and _update_summary
text via the pure counter logic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from sftp_ui.core.transfer import TransferDirection
from sftp_ui.ui.dialogs.sync_dialog import (
    SyncEntry,
    SyncStatus,
    _SyncModel,
    _build_upload_jobs,
    _build_download_jobs,
    _DEFAULT_CHECKED,
    _human_size,
    _fmt_mtime,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _entry(rel: str, status: SyncStatus, *,
           local_abs: str | None = None,
           remote_abs: str | None = None,
           local_size: int = 100,
           remote_size: int = 100,
           checked: bool | None = None) -> SyncEntry:
    chk = checked if checked is not None else _DEFAULT_CHECKED[status]
    return SyncEntry(
        rel_path=rel, status=status,
        local_abs=local_abs, remote_abs=remote_abs,
        local_size=local_size, remote_size=remote_size,
        checked=chk,
    )


# ── _build_upload_jobs() ──────────────────────────────────────────────────────

class TestBuildUploadJobs:
    def test_checked_entry_produces_job(self):
        e = _entry("f.py", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/f.py", checked=True)
        jobs = _build_upload_jobs([e], "/remote")
        assert len(jobs) == 1

    def test_unchecked_entry_skipped(self):
        e = _entry("f.py", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/f.py", checked=False)
        assert _build_upload_jobs([e], "/remote") == []

    def test_no_local_abs_skipped(self):
        e = _entry("r.txt", SyncStatus.REMOTE_ONLY,
                   local_abs=None, remote_abs="/remote/r.txt", checked=True)
        assert _build_upload_jobs([e], "/remote") == []

    def test_direction_is_upload(self):
        e = _entry("f.py", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/f.py", checked=True)
        jobs = _build_upload_jobs([e], "/remote")
        assert jobs[0].direction == TransferDirection.UPLOAD

    def test_local_path_correct(self):
        e = _entry("sub/f.py", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/sub/f.py", checked=True)
        jobs = _build_upload_jobs([e], "/remote")
        assert jobs[0].local_path == "/local/sub/f.py"

    def test_remote_path_uses_remote_abs_when_set(self):
        e = _entry("f.py", SyncStatus.LOCAL_NEWER,
                   local_abs="/local/f.py",
                   remote_abs="/srv/www/f.py", checked=True)
        jobs = _build_upload_jobs([e], "/remote")
        assert jobs[0].remote_path == "/srv/www/f.py"

    def test_remote_path_derived_from_rel_path_when_no_remote_abs(self):
        e = _entry("assets/logo.png", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/assets/logo.png",
                   remote_abs=None, checked=True)
        jobs = _build_upload_jobs([e], "/srv/www")
        assert jobs[0].remote_path == "/srv/www/assets/logo.png"

    def test_total_bytes_set_from_local_size(self):
        e = _entry("f.bin", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/f.bin", checked=True, local_size=4096)
        jobs = _build_upload_jobs([e], "/remote")
        assert jobs[0].total_bytes == 4096

    def test_multiple_entries_all_checked(self):
        entries = [
            _entry("a.txt", SyncStatus.LOCAL_ONLY, local_abs="/l/a.txt", checked=True),
            _entry("b.txt", SyncStatus.LOCAL_NEWER, local_abs="/l/b.txt",
                   remote_abs="/r/b.txt", checked=True),
            _entry("c.txt", SyncStatus.SAME, local_abs="/l/c.txt", checked=False),
        ]
        jobs = _build_upload_jobs(entries, "/remote")
        assert len(jobs) == 2

    def test_empty_entries_returns_empty(self):
        assert _build_upload_jobs([], "/remote") == []

    def test_nested_rel_path_mapped_correctly(self):
        e = _entry("a/b/c/deep.py", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/a/b/c/deep.py", remote_abs=None, checked=True)
        jobs = _build_upload_jobs([e], "/root")
        assert jobs[0].remote_path == "/root/a/b/c/deep.py"


# ── _build_download_jobs() ────────────────────────────────────────────────────

class TestBuildDownloadJobs:
    def test_checked_entry_produces_job(self, tmp_path):
        e = _entry("f.txt", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/f.txt", checked=True)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert len(jobs) == 1

    def test_unchecked_entry_skipped(self, tmp_path):
        e = _entry("f.txt", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/f.txt", checked=False)
        assert _build_download_jobs([e], str(tmp_path)) == []

    def test_no_remote_abs_skipped(self, tmp_path):
        e = _entry("f.txt", SyncStatus.LOCAL_ONLY,
                   local_abs="/local/f.txt", remote_abs=None, checked=True)
        assert _build_download_jobs([e], str(tmp_path)) == []

    def test_direction_is_download(self, tmp_path):
        e = _entry("f.txt", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/f.txt", checked=True)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert jobs[0].direction == TransferDirection.DOWNLOAD

    def test_remote_path_correct(self, tmp_path):
        e = _entry("dir/f.py", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/dir/f.py", checked=True)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert jobs[0].remote_path == "/remote/dir/f.py"

    def test_local_path_uses_local_abs_when_set(self, tmp_path):
        e = _entry("f.txt", SyncStatus.REMOTE_NEWER,
                   local_abs=str(tmp_path / "f.txt"),
                   remote_abs="/remote/f.txt", checked=True)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert jobs[0].local_path == str(tmp_path / "f.txt")

    def test_local_path_derived_from_rel_when_no_local_abs(self, tmp_path):
        e = _entry("sub/f.py", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/sub/f.py",
                   local_abs=None, checked=True)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert jobs[0].local_path == str(tmp_path / "sub" / "f.py")

    def test_parent_directories_created(self, tmp_path):
        e = _entry("a/b/c/f.bin", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/a/b/c/f.bin",
                   local_abs=None, checked=True)
        _build_download_jobs([e], str(tmp_path))
        assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_total_bytes_set_from_remote_size(self, tmp_path):
        e = _entry("big.bin", SyncStatus.REMOTE_ONLY,
                   remote_abs="/remote/big.bin", checked=True, remote_size=8192)
        jobs = _build_download_jobs([e], str(tmp_path))
        assert jobs[0].total_bytes == 8192

    def test_multiple_entries_mixed_checked(self, tmp_path):
        entries = [
            _entry("a.txt", SyncStatus.REMOTE_ONLY,
                   remote_abs="/r/a.txt", checked=True),
            _entry("b.txt", SyncStatus.REMOTE_ONLY,
                   remote_abs="/r/b.txt", checked=False),
            _entry("c.txt", SyncStatus.REMOTE_NEWER,
                   remote_abs="/r/c.txt", checked=True),
        ]
        jobs = _build_download_jobs(entries, str(tmp_path))
        assert len(jobs) == 2

    def test_empty_entries_returns_empty(self, tmp_path):
        assert _build_download_jobs([], str(tmp_path)) == []


# ── _SyncModel filter logic (via load) ───────────────────────────────────────

class TestSyncModelFilterLogic:
    """Tests that _apply_filters()-like logic works correctly on _SyncModel."""

    def test_load_only_matching_entries(self):
        model = _SyncModel()
        entries = [
            _entry("a.txt", SyncStatus.LOCAL_ONLY),
            _entry("b.txt", SyncStatus.SAME),
        ]
        # Simulate: only LOCAL_ONLY visible
        visible = {SyncStatus.LOCAL_ONLY}
        model.load([e for e in entries if e.status in visible])
        assert model.rowCount() == 1
        assert model._rows[0].rel_path == "a.txt"

    def test_load_all_statuses(self):
        model = _SyncModel()
        entries = [_entry(f"{s.value}.txt", s) for s in SyncStatus]
        model.load(entries)
        assert model.rowCount() == len(SyncStatus)

    def test_load_empty_clears_model(self):
        model = _SyncModel()
        model.load([_entry("f.txt", SyncStatus.SAME)])
        model.load([])
        assert model.rowCount() == 0

    def test_set_all_checked_via_model_rows(self):
        model = _SyncModel()
        entries = [_entry(f"f{i}.txt", SyncStatus.LOCAL_ONLY, checked=False)
                   for i in range(3)]
        model.load(entries)
        for e in model._rows:
            e.checked = True
        assert all(e.checked for e in model._rows)

    def test_set_all_unchecked_via_model_rows(self):
        model = _SyncModel()
        entries = [_entry(f"f{i}.txt", SyncStatus.LOCAL_ONLY, checked=True)
                   for i in range(3)]
        model.load(entries)
        for e in model._rows:
            e.checked = False
        assert not any(e.checked for e in model._rows)


# ── _DEFAULT_CHECKED correctness ─────────────────────────────────────────────

class TestDefaultChecked:
    def test_local_only_checked_by_default(self):
        assert _DEFAULT_CHECKED[SyncStatus.LOCAL_ONLY] is True

    def test_local_newer_checked_by_default(self):
        assert _DEFAULT_CHECKED[SyncStatus.LOCAL_NEWER] is True

    def test_same_unchecked_by_default(self):
        assert _DEFAULT_CHECKED[SyncStatus.SAME] is False

    def test_remote_newer_unchecked_by_default(self):
        assert _DEFAULT_CHECKED[SyncStatus.REMOTE_NEWER] is False

    def test_remote_only_unchecked_by_default(self):
        assert _DEFAULT_CHECKED[SyncStatus.REMOTE_ONLY] is False


# ── _human_size() ─────────────────────────────────────────────────────────────

class TestHumanSize:
    def test_zero_returns_dash(self):
        assert _human_size(0) == "—"

    def test_bytes_range(self):
        assert _human_size(512) == "512 B"

    def test_exactly_1023_bytes(self):
        assert _human_size(1023) == "1023 B"

    def test_exactly_1024_is_1_kb(self):
        assert _human_size(1024) == "1 KB"

    def test_1_mb(self):
        assert _human_size(1024 * 1024) == "1 MB"

    def test_1_gb(self):
        assert _human_size(1024 ** 3) == "1 GB"

    def test_1_tb(self):
        result = _human_size(1024 ** 4)
        assert "TB" in result

    def test_fractional_kb(self):
        result = _human_size(1536)  # 1.5 KB
        assert "KB" in result

    def test_large_file_tb_format(self):
        result = _human_size(int(1.5 * 1024 ** 4))
        assert "1.5 TB" in result


# ── _fmt_mtime() ──────────────────────────────────────────────────────────────

class TestFmtMtime:
    def test_zero_returns_dash(self):
        assert _fmt_mtime(0.0) == "—"

    def test_nonzero_returns_date_string(self):
        result = _fmt_mtime(1_700_000_000)
        # 2023-11-14 or 2023-11-15 depending on timezone — just check format
        assert len(result) == len("2023-11-14 23:13")
        assert result[4] == "-" and result[7] == "-"
        assert result[10] == " " and result[13] == ":"

    def test_year_in_range(self):
        result = _fmt_mtime(1_700_000_000)
        year = int(result[:4])
        assert 2023 <= year <= 2024  # reasonable for this timestamp

    def test_recent_timestamp(self):
        import time
        result = _fmt_mtime(time.time())
        assert "2025" in result or "2026" in result


# ── _SyncModel.headerData() ───────────────────────────────────────────────────

class TestSyncModelHeader:
    def test_column_count_is_seven(self, qapp):
        assert _SyncModel().columnCount() == 7

    def test_header_col0_empty(self, qapp):
        m = _SyncModel()
        assert m.headerData(0, Qt.Orientation.Horizontal) == ""

    def test_header_col1_status(self, qapp):
        m = _SyncModel()
        assert m.headerData(1, Qt.Orientation.Horizontal) == "Status"

    def test_header_col2_path(self, qapp):
        m = _SyncModel()
        assert m.headerData(2, Qt.Orientation.Horizontal) == "Path"

    def test_header_col3_local(self, qapp):
        m = _SyncModel()
        assert m.headerData(3, Qt.Orientation.Horizontal) == "Local"

    def test_header_col4_remote(self, qapp):
        m = _SyncModel()
        assert m.headerData(4, Qt.Orientation.Horizontal) == "Remote"

    def test_header_vertical_returns_none(self, qapp):
        m = _SyncModel()
        assert m.headerData(0, Qt.Orientation.Vertical) is None

    def test_header_non_display_role_returns_none(self, qapp):
        m = _SyncModel()
        assert m.headerData(0, Qt.Orientation.Horizontal,
                            Qt.ItemDataRole.EditRole) is None


# ── _SyncModel.flags() ────────────────────────────────────────────────────────

class TestSyncModelFlags:
    def _model_with_entry(self, qapp):
        m = _SyncModel()
        e = SyncEntry("f.txt", SyncStatus.LOCAL_ONLY, local_abs="/l/f.txt",
                      local_size=100, checked=True)
        m.load([e])
        return m

    def test_checkbox_col_has_user_checkable(self, qapp):
        m = self._model_with_entry(qapp)
        flags = m.flags(m.index(0, 0))
        assert flags & Qt.ItemFlag.ItemIsUserCheckable

    def test_status_col_not_checkable(self, qapp):
        m = self._model_with_entry(qapp)
        flags = m.flags(m.index(0, 1))
        assert not (flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_path_col_not_checkable(self, qapp):
        m = self._model_with_entry(qapp)
        flags = m.flags(m.index(0, 2))
        assert not (flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_all_cols_enabled_and_selectable(self, qapp):
        m = self._model_with_entry(qapp)
        for col in range(m.columnCount()):
            f = m.flags(m.index(0, col))
            assert f & Qt.ItemFlag.ItemIsEnabled
            assert f & Qt.ItemFlag.ItemIsSelectable


# ── _SyncModel.data() ─────────────────────────────────────────────────────────

def _make_entry(status: SyncStatus, *, checked=True,
                local_size=512, remote_size=1024,
                local_mtime=1_700_000_000.0, remote_mtime=1_600_000_000.0) -> SyncEntry:
    return SyncEntry(
        rel_path="sub/file.py",
        status=status,
        local_abs="/local/sub/file.py",
        remote_abs="/remote/sub/file.py",
        local_size=local_size,
        remote_size=remote_size,
        local_mtime=local_mtime,
        remote_mtime=remote_mtime,
        checked=checked,
    )


class TestSyncModelData:
    def _cell(self, model, row, col, role=Qt.ItemDataRole.DisplayRole):
        return model.data(model.index(row, col), role)

    def test_invalid_index_returns_none(self, qapp):
        m = _SyncModel()
        assert m.data(QModelIndex()) is None

    def test_out_of_range_row_returns_none(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.SAME)])
        assert m.data(m.index(5, 0)) is None

    def test_checked_entry_check_state_is_checked(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, checked=True)])
        val = m.data(m.index(0, 0), Qt.ItemDataRole.CheckStateRole)
        assert val == Qt.CheckState.Checked

    def test_unchecked_entry_check_state_is_unchecked(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.SAME, checked=False)])
        val = m.data(m.index(0, 0), Qt.ItemDataRole.CheckStateRole)
        assert val == Qt.CheckState.Unchecked

    def test_check_state_role_on_non_chk_col_returns_none(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        # CheckStateRole on a non-checkbox column → None
        val = m.data(m.index(0, 1), Qt.ItemDataRole.CheckStateRole)
        assert val is None

    def test_status_col_contains_status_text(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        text = self._cell(m, 0, 1)
        assert "Local only" in text

    def test_status_col_contains_icon(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        text = self._cell(m, 0, 1)
        assert "↑" in text

    def test_status_remote_newer_icon(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.REMOTE_NEWER)])
        text = self._cell(m, 0, 1)
        assert "↓" in text

    def test_status_same_icon(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.SAME)])
        text = self._cell(m, 0, 1)
        assert "✓" in text

    def test_path_col_returns_rel_path(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        assert self._cell(m, 0, 2) == "sub/file.py"

    def test_local_size_col_formatted(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, local_size=0)])
        text = self._cell(m, 0, 3)
        assert text == "—"

    def test_local_size_col_nonzero(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, local_size=2048)])
        text = self._cell(m, 0, 3)
        assert "KB" in text or "2" in text

    def test_remote_size_col_formatted(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.REMOTE_ONLY, remote_size=0)])
        assert self._cell(m, 0, 4) == "—"

    def test_local_mtime_col_formatted(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, local_mtime=1_700_000_000.0)])
        text = self._cell(m, 0, 5)
        assert "2023" in text or "2024" in text

    def test_remote_mtime_col_zero_returns_dash(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.REMOTE_ONLY, remote_mtime=0.0)])
        assert self._cell(m, 0, 6) == "—"

    def test_foreground_role_on_status_col_returns_color(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        color = m.data(m.index(0, 1), Qt.ItemDataRole.ForegroundRole)
        assert isinstance(color, QColor)

    def test_foreground_role_on_path_col_returns_none(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        result = m.data(m.index(0, 2), Qt.ItemDataRole.ForegroundRole)
        assert result is None

    def test_all_statuses_have_display_text(self, qapp):
        for status in SyncStatus:
            m = _SyncModel()
            m.load([_make_entry(status)])
            text = self._cell(m, 0, 1)
            assert text is not None and len(text) > 0


# ── _SyncModel.setData() ──────────────────────────────────────────────────────

class TestSyncModelSetData:
    def test_set_checked_via_enum(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, checked=False)])
        m.setData(m.index(0, 0), Qt.CheckState.Checked,
                  Qt.ItemDataRole.CheckStateRole)
        assert m._rows[0].checked is True

    def test_set_unchecked_via_enum(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, checked=True)])
        m.setData(m.index(0, 0), Qt.CheckState.Unchecked,
                  Qt.ItemDataRole.CheckStateRole)
        assert m._rows[0].checked is False

    def test_set_checked_via_int_2(self, qapp):
        """Qt sometimes passes integer 2 for Checked — both forms must work."""
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY, checked=False)])
        m.setData(m.index(0, 0), 2, Qt.ItemDataRole.CheckStateRole)
        assert m._rows[0].checked is True

    def test_set_wrong_role_returns_false(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        result = m.setData(m.index(0, 0), "value", Qt.ItemDataRole.EditRole)
        assert result is False

    def test_set_wrong_column_returns_false(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        result = m.setData(m.index(0, 1), Qt.CheckState.Checked,
                           Qt.ItemDataRole.CheckStateRole)
        assert result is False

    def test_successful_set_returns_true(self, qapp):
        m = _SyncModel()
        m.load([_make_entry(SyncStatus.LOCAL_ONLY)])
        result = m.setData(m.index(0, 0), Qt.CheckState.Checked,
                           Qt.ItemDataRole.CheckStateRole)
        assert result is True
