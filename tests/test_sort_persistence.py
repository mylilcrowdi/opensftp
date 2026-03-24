"""
Tests for column sort persistence (roadmap item #7).

Covers:
- UIState.set_sort_state() stores col + order correctly
- UIState.get_sort_state() returns saved values
- UIState.get_sort_state() returns (-1, 0) for unknown panels
- sort_state round-trips through save/load
- Malformed sort_state entries in JSON are silently skipped
- UIState.save() includes sort_state in the written file
- RemotePanel emits sort_state_changed after header click (new col)
- RemotePanel emits sort_state_changed after cycling same col (asc → desc)
- RemotePanel emits sort_state_changed after third click resets to neutral
- RemotePanel.restore_sort_state() updates internal _sort_col/_sort_order
- RemotePanel.restore_sort_state() with col=-1 resets to neutral
- LocalPanel emits sort_state_changed after header click
- LocalPanel.restore_sort_state() applies the sort and updates header indicator
- LocalPanel.restore_sort_state() with col=-1 resets indicator
- _apply_entries() re-applies the active sort after a new listing
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.core.ui_state import UIState
from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.panels.remote_panel import RemotePanel
from sftp_ui.ui.panels.local_panel import LocalPanel


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def tmp_state_path(tmp_path):
    return tmp_path / "ui_state.json"


@pytest.fixture
def state(tmp_state_path):
    return UIState(path=tmp_state_path)


@pytest.fixture
def remote_panel(qapp):
    panel = RemotePanel()
    yield panel
    panel.close()


@pytest.fixture
def local_panel(qapp, tmp_path):
    panel = LocalPanel(initial_path=str(tmp_path))
    yield panel
    panel.close()


# ── UIState: set/get sort state ────────────────────────────────────────────────

class TestUISortState:
    def test_default_returns_neutral(self, state):
        col, order = state.get_sort_state("remote")
        assert col == -1
        assert order == 0

    def test_default_unknown_panel(self, state):
        col, order = state.get_sort_state("nonexistent")
        assert col == -1
        assert order == 0

    def test_set_and_get_ascending(self, state):
        state.set_sort_state("remote", 1, 0)
        col, order = state.get_sort_state("remote")
        assert col == 1
        assert order == 0

    def test_set_and_get_descending(self, state):
        state.set_sort_state("remote", 2, 1)
        col, order = state.get_sort_state("remote")
        assert col == 2
        assert order == 1

    def test_set_neutral(self, state):
        state.set_sort_state("remote", 0, 0)   # set first
        state.set_sort_state("remote", -1, 0)  # reset to neutral
        col, order = state.get_sort_state("remote")
        assert col == -1

    def test_local_and_remote_independent(self, state):
        state.set_sort_state("remote", 0, 1)
        state.set_sort_state("local",  2, 0)
        r_col, r_order = state.get_sort_state("remote")
        l_col, l_order = state.get_sort_state("local")
        assert r_col == 0 and r_order == 1
        assert l_col == 2 and l_order == 0

    def test_persists_through_save_load(self, tmp_state_path):
        s1 = UIState(path=tmp_state_path)
        s1.set_sort_state("remote", 1, 1)
        s1.set_sort_state("local",  0, 0)

        s2 = UIState(path=tmp_state_path)
        assert s2.get_sort_state("remote") == (1, 1)
        assert s2.get_sort_state("local")  == (0, 0)

    def test_save_writes_sort_state_key(self, tmp_state_path):
        s = UIState(path=tmp_state_path)
        s.set_sort_state("remote", 2, 0)
        data = json.loads(tmp_state_path.read_text())
        assert "sort_state" in data
        assert data["sort_state"]["remote"] == {"col": 2, "order": 0}

    def test_malformed_entry_skipped(self, tmp_state_path):
        """A sort_state entry with non-numeric values is ignored on load."""
        raw = {
            "sort_state": {
                "remote": {"col": "bad", "order": "data"},
                "local":  {"col": 1,     "order": 0},
            }
        }
        tmp_state_path.write_text(json.dumps(raw))
        s = UIState(path=tmp_state_path)
        # "remote" had malformed data — falls back to default
        assert s.get_sort_state("remote") == (-1, 0)
        # "local" was fine
        assert s.get_sort_state("local") == (1, 0)

    def test_missing_sort_state_key_in_file(self, tmp_state_path):
        """Files written before sort_state was added load cleanly."""
        raw = {"last_local_path": str(Path.home()), "was_connected": False}
        tmp_state_path.write_text(json.dumps(raw))
        s = UIState(path=tmp_state_path)
        assert s.get_sort_state("remote") == (-1, 0)


# ── RemotePanel: sort_state_changed signal ─────────────────────────────────────

class TestRemotePanelSortSignal:
    def test_signal_emitted_on_new_column_click(self, remote_panel):
        received: list[tuple[int, int]] = []
        remote_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        remote_panel._on_header_click(0)   # new col → ascending

        assert len(received) == 1
        col, order = received[0]
        assert col == 0
        assert order == Qt.SortOrder.AscendingOrder.value

    def test_signal_emitted_on_second_click_descending(self, remote_panel):
        received: list[tuple[int, int]] = []
        remote_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        remote_panel._on_header_click(1)   # first click — asc
        remote_panel._on_header_click(1)   # second click — desc

        assert received[-1] == (1, Qt.SortOrder.DescendingOrder.value)

    def test_signal_emitted_on_neutral_reset(self, remote_panel):
        received: list[tuple[int, int]] = []
        remote_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        remote_panel._on_header_click(2)   # asc
        remote_panel._on_header_click(2)   # desc
        remote_panel._on_header_click(2)   # neutral (-1)

        assert received[-1][0] == -1  # col=-1 means neutral

    def test_signal_carries_correct_order_int(self, remote_panel):
        """order must be an int (0/1), not a Qt.SortOrder enum."""
        orders: list = []
        remote_panel.sort_state_changed.connect(lambda c, o: orders.append(o))

        remote_panel._on_header_click(0)  # asc → 0
        remote_panel._on_header_click(0)  # desc → 1

        assert orders[0] == Qt.SortOrder.AscendingOrder.value   # 0
        assert orders[1] == Qt.SortOrder.DescendingOrder.value  # 1


# ── RemotePanel: restore_sort_state ───────────────────────────────────────────

class TestRemotePanelRestore:
    def test_restore_sets_internal_state(self, remote_panel):
        remote_panel.restore_sort_state(col=1, order=0)
        assert remote_panel._sort_col == 1
        assert remote_panel._sort_order == Qt.SortOrder.AscendingOrder

    def test_restore_descending(self, remote_panel):
        remote_panel.restore_sort_state(col=2, order=1)
        assert remote_panel._sort_col == 2
        assert remote_panel._sort_order == Qt.SortOrder.DescendingOrder

    def test_restore_neutral(self, remote_panel):
        remote_panel.restore_sort_state(col=-1, order=0)
        assert remote_panel._sort_col == -1

    def test_restore_updates_header_indicator(self, remote_panel):
        remote_panel.restore_sort_state(col=0, order=0)
        hdr = remote_panel._table.horizontalHeader()
        assert hdr.sortIndicatorSection() == 0
        assert hdr.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    def test_restore_neutral_clears_header_indicator(self, remote_panel):
        # First set a sort, then restore to neutral
        remote_panel.restore_sort_state(col=1, order=0)
        remote_panel.restore_sort_state(col=-1, order=0)
        hdr = remote_panel._table.horizontalHeader()
        assert hdr.sortIndicatorSection() == -1


# ── RemotePanel: sort persists across navigate (_apply_entries) ───────────────

class TestRemotePanelSortPersistsOnNavigate:
    def test_sort_reapplied_after_apply_entries(self, remote_panel):
        """Sorting stays active after a directory listing is loaded."""
        remote_panel._sort_col   = 2   # Modified
        remote_panel._sort_order = Qt.SortOrder.DescendingOrder

        entries = [
            RemoteEntry(name="b.txt", path="/b.txt", is_dir=False, size=100, mtime=200),
            RemoteEntry(name="a.txt", path="/a.txt", is_dir=False, size=50,  mtime=300),
            RemoteEntry(name="c.txt", path="/c.txt", is_dir=False, size=10,  mtime=100),
        ]
        remote_panel._all_entries = entries
        remote_panel._apply_entries()

        # With descending mtime sort: a.txt (300), b.txt (200), c.txt (100)
        assert remote_panel._model.entry(0).name == "a.txt"
        assert remote_panel._model.entry(1).name == "b.txt"
        assert remote_panel._model.entry(2).name == "c.txt"


# ── LocalPanel: sort_state_changed signal ─────────────────────────────────────

class TestLocalPanelSortSignal:
    def test_signal_emitted_on_header_click(self, local_panel):
        received: list[tuple[int, int]] = []
        local_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        local_panel._on_header_click(0)   # new col → ascending

        assert len(received) == 1
        assert received[0][0] == 0
        assert received[0][1] == Qt.SortOrder.AscendingOrder.value

    def test_signal_descending_on_second_click(self, local_panel):
        received: list[tuple[int, int]] = []
        local_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        local_panel._on_header_click(1)
        local_panel._on_header_click(1)

        assert received[-1] == (1, Qt.SortOrder.DescendingOrder.value)

    def test_signal_neutral_on_third_click(self, local_panel):
        received: list[tuple[int, int]] = []
        local_panel.sort_state_changed.connect(lambda c, o: received.append((c, o)))

        local_panel._on_header_click(0)
        local_panel._on_header_click(0)
        local_panel._on_header_click(0)

        assert received[-1][0] == -1


# ── LocalPanel: restore_sort_state ────────────────────────────────────────────

class TestLocalPanelRestore:
    def test_restore_sets_internal_col_order(self, local_panel):
        local_panel.restore_sort_state(col=1, order=0)
        assert local_panel._sort_col == 1
        assert local_panel._sort_order == Qt.SortOrder.AscendingOrder

    def test_restore_descending(self, local_panel):
        local_panel.restore_sort_state(col=2, order=1)
        assert local_panel._sort_col == 2
        assert local_panel._sort_order == Qt.SortOrder.DescendingOrder

    def test_restore_neutral_resets_col(self, local_panel):
        local_panel.restore_sort_state(col=1, order=0)
        local_panel.restore_sort_state(col=-1, order=0)
        assert local_panel._sort_col == -1

    def test_restore_updates_header_indicator(self, local_panel):
        local_panel.restore_sort_state(col=0, order=1)
        hdr = local_panel._tree.header()
        assert hdr.sortIndicatorSection() == 0
        assert hdr.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    def test_restore_neutral_clears_header_indicator(self, local_panel):
        local_panel.restore_sort_state(col=2, order=0)
        local_panel.restore_sort_state(col=-1, order=0)
        hdr = local_panel._tree.header()
        assert hdr.sortIndicatorSection() == -1
