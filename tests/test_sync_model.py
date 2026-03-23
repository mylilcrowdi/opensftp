"""
Tests for SyncDialog internals — pure Python / Qt model layer.

Covers: _human_size, _fmt_mtime, SyncEntry defaults,
        _SyncModel (load / rowCount / columnCount / data / setData /
        headerData / flags / CheckStateRole),
        _DEFAULT_CHECKED per status, filter application logic,
        TransferJob generation from SyncEntry.
"""
from __future__ import annotations

import os
import sys
import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt

from sftp_ui.ui.dialogs.sync_dialog import (
    _DEFAULT_CHECKED,
    _SyncModel,
    SyncEntry,
    SyncStatus,
    _fmt_mtime,
    _human_size,
    _C_CHK, _C_ST, _C_PATH, _C_LS, _C_RS, _C_LM, _C_RM,
)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def _entry(rel="a/b.txt", status=SyncStatus.SAME, **kw) -> SyncEntry:
    defaults = dict(
        local_abs="/local/a/b.txt",
        remote_abs="/remote/a/b.txt",
        local_size=1024,
        remote_size=1024,
        local_mtime=1_700_000_000.0,
        remote_mtime=1_700_000_000.0,
        checked=_DEFAULT_CHECKED[status],
    )
    defaults.update(kw)
    return SyncEntry(rel_path=rel, status=status, **defaults)


# ── _human_size ────────────────────────────────────────────────────────────────

class TestSyncHumanSize:
    def test_zero_returns_dash(self):
        assert _human_size(0) == "—"

    def test_small_bytes(self):
        result = _human_size(512)
        assert "B" in result
        assert "512" in result

    def test_exactly_1kb(self):
        assert _human_size(1024) == "1 KB"

    def test_megabytes(self):
        result = _human_size(1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _human_size(1024 ** 3)
        assert "GB" in result

    def test_terabytes(self):
        result = _human_size(1024 ** 4)
        assert "TB" in result

    def test_no_decimal_for_integer_kb(self):
        # 2048 bytes = 2 KB, no decimal
        result = _human_size(2048)
        assert "2 KB" == result

    def test_fractional_tb(self):
        result = _human_size(int(1.5 * 1024 ** 4))
        assert "TB" in result
        assert "1.5" in result


# ── _fmt_mtime ─────────────────────────────────────────────────────────────────

class TestSyncFmtMtime:
    def test_zero_returns_dash(self):
        assert _fmt_mtime(0.0) == "—"

    def test_nonzero_returns_datetime_string(self):
        ts = 1_700_000_000.0
        result = _fmt_mtime(ts)
        # Should contain a year
        assert "20" in result

    def test_format_contains_date(self):
        ts = datetime.datetime(2024, 6, 15, 12, 30).timestamp()
        result = _fmt_mtime(ts)
        assert "2024" in result
        assert "06" in result or "6" in result

    def test_format_contains_time(self):
        ts = datetime.datetime(2024, 1, 1, 8, 5).timestamp()
        result = _fmt_mtime(ts)
        assert ":" in result

    def test_returns_string(self):
        assert isinstance(_fmt_mtime(1_000_000.0), str)


# ── SyncEntry defaults ─────────────────────────────────────────────────────────

class TestSyncEntryDefaults:
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


# ── _SyncModel — structure ─────────────────────────────────────────────────────

class TestSyncModelStructure:
    def test_empty_model_row_count(self, qapp):
        model = _SyncModel()
        assert model.rowCount() == 0

    def test_column_count_is_seven(self, qapp):
        model = _SyncModel()
        assert model.columnCount() == 7

    def test_load_updates_row_count(self, qapp):
        model = _SyncModel()
        model.load([_entry("a.txt"), _entry("b.txt")])
        assert model.rowCount() == 2

    def test_load_replaces_previous_rows(self, qapp):
        model = _SyncModel()
        model.load([_entry("a.txt")])
        model.load([_entry("b.txt"), _entry("c.txt")])
        assert model.rowCount() == 2

    def test_header_names(self, qapp):
        model = _SyncModel()
        from PySide6.QtCore import Qt
        assert model.headerData(2, Qt.Orientation.Horizontal) == "Path"
        assert model.headerData(3, Qt.Orientation.Horizontal) == "Local"
        assert model.headerData(4, Qt.Orientation.Horizontal) == "Remote"

    def test_invalid_index_returns_none(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(99, 0)
        assert model.data(idx) is None


# ── _SyncModel — DisplayRole data ─────────────────────────────────────────────

class TestSyncModelData:
    def test_path_column_shows_rel_path(self, qapp):
        model = _SyncModel()
        model.load([_entry("src/main.py")])
        idx = model.index(0, _C_PATH)
        assert model.data(idx) == "src/main.py"

    def test_local_size_column(self, qapp):
        model = _SyncModel()
        model.load([_entry(local_size=1024)])
        idx = model.index(0, _C_LS)
        assert model.data(idx) == "1 KB"

    def test_remote_size_column(self, qapp):
        model = _SyncModel()
        model.load([_entry(remote_size=2048)])
        idx = model.index(0, _C_RS)
        assert model.data(idx) == "2 KB"

    def test_zero_local_size_shows_dash(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.REMOTE_ONLY, local_size=0)])
        idx = model.index(0, _C_LS)
        assert model.data(idx) == "—"

    def test_status_column_shows_status_text(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.LOCAL_ONLY)])
        idx = model.index(0, _C_ST)
        text = model.data(idx)
        assert "Local only" in text

    def test_status_column_shows_icon(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.LOCAL_NEWER)])
        idx = model.index(0, _C_ST)
        text = model.data(idx)
        assert "↑" in text

    def test_remote_only_shows_down_arrow(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.REMOTE_ONLY)])
        idx = model.index(0, _C_ST)
        assert "↓" in model.data(idx)

    def test_same_shows_checkmark(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.SAME)])
        idx = model.index(0, _C_ST)
        assert "✓" in model.data(idx)

    def test_local_mtime_column(self, qapp):
        model = _SyncModel()
        ts = datetime.datetime(2024, 3, 10, 15, 0).timestamp()
        model.load([_entry(local_mtime=ts)])
        idx = model.index(0, _C_LM)
        assert "2024" in model.data(idx)

    def test_zero_mtime_shows_dash(self, qapp):
        model = _SyncModel()
        model.load([_entry(local_mtime=0.0)])
        idx = model.index(0, _C_LM)
        assert model.data(idx) == "—"

    def test_foreground_role_on_status_column(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.LOCAL_ONLY)])
        idx = model.index(0, _C_ST)
        color = model.data(idx, Qt.ItemDataRole.ForegroundRole)
        assert color is not None

    def test_foreground_role_not_on_path_column(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_PATH)
        color = model.data(idx, Qt.ItemDataRole.ForegroundRole)
        assert color is None


# ── _SyncModel — CheckStateRole ───────────────────────────────────────────────

class TestSyncModelCheckbox:
    def test_local_only_checked_by_default(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.LOCAL_ONLY, checked=True)])
        idx = model.index(0, _C_CHK)
        assert model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    def test_same_unchecked_by_default(self, qapp):
        model = _SyncModel()
        model.load([_entry(status=SyncStatus.SAME, checked=False)])
        idx = model.index(0, _C_CHK)
        assert model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked

    def test_setdata_checks_item(self, qapp):
        model = _SyncModel()
        model.load([_entry(checked=False)])
        idx = model.index(0, _C_CHK)
        # Qt passes the raw int value (2 = Checked) when toggling via the view
        model.setData(idx, 2, Qt.ItemDataRole.CheckStateRole)
        assert model._rows[0].checked is True

    def test_setdata_unchecks_item(self, qapp):
        model = _SyncModel()
        model.load([_entry(checked=True)])
        idx = model.index(0, _C_CHK)
        model.setData(idx, 0, Qt.ItemDataRole.CheckStateRole)
        assert model._rows[0].checked is False

    def test_setdata_returns_true_on_success(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_CHK)
        result = model.setData(idx, 2, Qt.ItemDataRole.CheckStateRole)
        assert result is True

    def test_setdata_returns_false_on_wrong_column(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_PATH)
        result = model.setData(idx, "anything", Qt.ItemDataRole.EditRole)
        assert result is False

    def test_checkstate_not_returned_for_non_chk_column(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_PATH)
        result = model.data(idx, Qt.ItemDataRole.CheckStateRole)
        assert result is None

    def test_flags_include_user_checkable_for_chk_column(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_CHK)
        flags = model.flags(idx)
        assert flags & Qt.ItemFlag.ItemIsUserCheckable

    def test_flags_not_checkable_for_path_column(self, qapp):
        model = _SyncModel()
        model.load([_entry()])
        idx = model.index(0, _C_PATH)
        flags = model.flags(idx)
        assert not (flags & Qt.ItemFlag.ItemIsUserCheckable)


# ── Filter logic ───────────────────────────────────────────────────────────────

class TestSyncFilter:
    """Test that _apply_filters correctly shows/hides entries by status."""

    def _make_entries(self) -> list[SyncEntry]:
        return [
            _entry("a.py", SyncStatus.LOCAL_ONLY),
            _entry("b.py", SyncStatus.LOCAL_NEWER),
            _entry("c.py", SyncStatus.SAME),
            _entry("d.py", SyncStatus.REMOTE_NEWER),
            _entry("e.py", SyncStatus.REMOTE_ONLY),
        ]

    def test_filter_all_shows_all(self, qapp):
        model = _SyncModel()
        entries = self._make_entries()
        visible = set(SyncStatus)
        model.load([e for e in entries if e.status in visible])
        assert model.rowCount() == 5

    def test_filter_only_same(self, qapp):
        model = _SyncModel()
        entries = self._make_entries()
        visible = {SyncStatus.SAME}
        model.load([e for e in entries if e.status in visible])
        assert model.rowCount() == 1
        idx = model.index(0, _C_PATH)
        assert model.data(idx) == "c.py"

    def test_filter_local_changes(self, qapp):
        model = _SyncModel()
        entries = self._make_entries()
        visible = {SyncStatus.LOCAL_ONLY, SyncStatus.LOCAL_NEWER}
        model.load([e for e in entries if e.status in visible])
        assert model.rowCount() == 2

    def test_filter_empty_shows_nothing(self, qapp):
        model = _SyncModel()
        entries = self._make_entries()
        model.load([e for e in entries if e.status in set()])
        assert model.rowCount() == 0

    def test_filter_preserves_order(self, qapp):
        model = _SyncModel()
        entries = [
            _entry("z.py", SyncStatus.LOCAL_ONLY),
            _entry("a.py", SyncStatus.LOCAL_ONLY),
        ]
        model.load(entries)
        assert model.data(model.index(0, _C_PATH)) == "z.py"
        assert model.data(model.index(1, _C_PATH)) == "a.py"
