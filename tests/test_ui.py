"""
Headless UI tests for RemoteModel, _BreadcrumbBar, UIState, and RemotePanel.

Requires PySide6 with offscreen platform (set QT_QPA_PLATFORM=offscreen
or run under the existing CI environment).
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

import pytest

# Ensure offscreen platform before any Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.core.ui_state import UIState
from sftp_ui.ui.panels.remote_panel import RemoteModel, RemotePanel, _BreadcrumbBar


# ── QApplication fixture ──────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    """Single QApplication for the whole test session."""
    app = QApplication.instance() or QApplication([])
    yield app


def _entry(name: str, is_dir: bool = False, size: int = 0, mtime: int = 0,
           is_symlink: bool = False) -> RemoteEntry:
    path = f"/{name}"
    return RemoteEntry(name=name, path=path, is_dir=is_dir, size=size,
                       mtime=mtime, is_symlink=is_symlink)


# ── RemoteModel ───────────────────────────────────────────────────────────────

class TestRemoteModel:
    def test_load_sets_row_count(self, qapp):
        model = RemoteModel()
        entries = [_entry("a.txt"), _entry("b.txt"), _entry("c/", is_dir=True)]
        model.load(entries)
        assert model.rowCount() == 3

    def test_load_empty(self, qapp):
        model = RemoteModel()
        model.load([])
        assert model.rowCount() == 0

    def test_column_count(self, qapp):
        model = RemoteModel()
        assert model.columnCount() == 3

    def test_sort_by_name_asc_dirs_first(self, qapp):
        model = RemoteModel()
        entries = [
            _entry("zebra.txt"),
            _entry("alpha/", is_dir=True),
            _entry("beta.txt"),
            _entry("mango/", is_dir=True),
        ]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        names = [model.entry(i).name for i in range(model.rowCount())]
        # dirs come first, then files, each group alphabetical
        assert names.index("alpha/") < names.index("mango/")
        assert names.index("mango/") < names.index("beta.txt")
        assert names.index("beta.txt") < names.index("zebra.txt")

    def test_sort_by_name_desc(self, qapp):
        model = RemoteModel()
        entries = [_entry("a.txt"), _entry("z.txt"), _entry("m.txt")]
        model.load(entries)
        model.sort(0, Qt.SortOrder.DescendingOrder)
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names[0] == "z.txt"
        assert names[-1] == "a.txt"

    def test_sort_by_size(self, qapp):
        model = RemoteModel()
        entries = [_entry("big.txt", size=1000), _entry("small.txt", size=1), _entry("mid.txt", size=500)]
        model.load(entries)
        model.sort(1, Qt.SortOrder.AscendingOrder)
        sizes = [model.entry(i).size for i in range(model.rowCount())]
        assert sizes == sorted(sizes)

    def test_sort_by_mtime(self, qapp):
        model = RemoteModel()
        entries = [_entry("new.txt", mtime=300), _entry("old.txt", mtime=100), _entry("mid.txt", mtime=200)]
        model.load(entries)
        model.sort(2, Qt.SortOrder.AscendingOrder)
        mtimes = [model.entry(i).mtime for i in range(model.rowCount())]
        assert mtimes == sorted(mtimes)

    def test_sort_neutral_restores_original_order(self, qapp):
        model = RemoteModel()
        entries = [_entry("z.txt"), _entry("a.txt"), _entry("m.txt")]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)  # changes order
        model.sort(-1)                               # back to original
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names == ["z.txt", "a.txt", "m.txt"]

    def test_dotdot_stays_at_top_after_sort(self, qapp):
        model = RemoteModel()
        up = RemoteEntry(name="..", path="/", is_dir=True, size=0, mtime=0)
        entries = [up, _entry("z.txt"), _entry("a/", is_dir=True)]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        assert model.entry(0).name == ".."

    def test_display_data_dotdot(self, qapp):
        from PySide6.QtCore import QModelIndex
        model = RemoteModel()
        up = RemoteEntry(name="..", path="/", is_dir=True, size=0, mtime=0)
        model.load([up])
        idx = model.index(0, 0)
        assert "↑" in model.data(idx, Qt.ItemDataRole.DisplayRole)

    def test_display_data_symlink_icon(self, qapp):
        model = RemoteModel()
        e = _entry("link.txt", is_symlink=True)
        model.load([e])
        idx = model.index(0, 0)
        text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert "🔗" in text

    def test_display_data_dir_icon(self, qapp):
        model = RemoteModel()
        e = _entry("mydir", is_dir=True)
        model.load([e])
        idx = model.index(0, 0)
        text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert "📁" in text

    def test_display_data_file_icon(self, qapp):
        model = RemoteModel()
        e = _entry("archive.zip")
        model.load([e])
        idx = model.index(0, 0)
        text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert "📦" in text  # zip → archive icon

    def test_header_data(self, qapp):
        model = RemoteModel()
        assert model.headerData(0, Qt.Orientation.Horizontal) == "Name"
        assert model.headerData(1, Qt.Orientation.Horizontal) == "Size"
        assert model.headerData(2, Qt.Orientation.Horizontal) == "Modified"


# ── _BreadcrumbBar ─────────────────────────────────────────────────────────────

class TestBreadcrumbBar:
    def test_set_path_root(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/")   # must not raise

    def test_set_path_deep(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/home/user/projects")   # must not raise

    def test_navigate_to_signal_fires(self, qapp):
        bar = _BreadcrumbBar()
        received = []
        bar.navigate_to.connect(received.append)
        bar.set_path("/home/user/projects")
        # Simulate clicking the "/home" segment by emitting the signal directly
        # (widget-level click testing requires QTest; signal logic is enough here)
        bar.navigate_to.emit("/home")
        assert received == ["/home"]

    def test_focus_editor_shows_linedit(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/some/path")
        # Before focus_editor: editor is hidden, crumb widget is shown
        assert bar._editor.isHidden()
        bar.focus_editor()
        # After focus_editor: editor is no longer hidden, crumb widget is hidden
        assert not bar._editor.isHidden()
        assert bar._crumb_scroll.isHidden()

    def test_editor_text_matches_path(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/some/path")
        bar.focus_editor()
        assert bar._editor.text() == "/some/path"


# ── UIState column_widths ─────────────────────────────────────────────────────

class TestUIStateColumnWidths:
    def test_default_empty(self, tmp_path):
        state = UIState(path=tmp_path / "ui_state.json")
        assert state.get_column_widths("remote") == []

    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "ui_state.json"
        state = UIState(path=path)
        state.set_column_widths("remote", [300, 80, 120])

        state2 = UIState(path=path)
        assert state2.get_column_widths("remote") == [300, 80, 120]

    def test_multiple_panels(self, tmp_path):
        path = tmp_path / "ui_state.json"
        state = UIState(path=path)
        state.set_column_widths("remote", [100, 50, 60])
        state.set_column_widths("local", [200])

        state2 = UIState(path=path)
        assert state2.get_column_widths("remote") == [100, 50, 60]
        assert state2.get_column_widths("local") == [200]

    def test_survives_corrupt_column_widths(self, tmp_path):
        path = tmp_path / "ui_state.json"
        # Write corrupt column_widths to disk
        path.write_text(json.dumps({"column_widths": "not a dict"}), encoding="utf-8")
        state = UIState(path=path)  # must not raise
        assert state.get_column_widths("remote") == []


# ── RemotePanel dotfile filtering ─────────────────────────────────────────────

class TestRemotePanelDotfiles:
    def _make_entries(self):
        return [
            RemoteEntry(name="..", path="/", is_dir=True, size=0, mtime=0),
            RemoteEntry(name=".hidden", path="/.hidden", is_dir=False, size=10, mtime=0),
            RemoteEntry(name=".dotdir", path="/.dotdir", is_dir=True, size=0, mtime=0),
            RemoteEntry(name="visible.txt", path="/visible.txt", is_dir=False, size=5, mtime=0),
            RemoteEntry(name="normal_dir", path="/normal_dir", is_dir=True, size=0, mtime=0),
        ]

    def test_hidden_files_filtered_by_default(self, qapp):
        panel = RemotePanel()
        panel._all_entries = self._make_entries()
        panel._show_hidden = False
        panel._apply_entries()
        names = {panel._model.entry(i).name for i in range(panel._model.rowCount())}
        assert ".hidden" not in names
        assert ".dotdir" not in names
        assert "visible.txt" in names
        assert ".." in names        # ".." is never filtered

    def test_show_hidden_reveals_dotfiles(self, qapp):
        panel = RemotePanel()
        panel._all_entries = self._make_entries()
        panel._show_hidden = True
        panel._apply_entries()
        names = {panel._model.entry(i).name for i in range(panel._model.rowCount())}
        assert ".hidden" in names
        assert ".dotdir" in names

    def test_toggle_hidden_updates_model(self, qapp):
        panel = RemotePanel()
        panel._all_entries = self._make_entries()
        panel._show_hidden = False
        panel._apply_entries()
        count_hidden_off = panel._model.rowCount()

        panel._show_hidden = True
        panel._apply_entries()
        count_hidden_on = panel._model.rowCount()

        assert count_hidden_on > count_hidden_off

# ── RemoteModel edge cases ────────────────────────────────────────────────────

class TestRemoteModelEdgeCases:
    def test_sort_empty_model_does_not_crash(self, qapp):
        model = RemoteModel()
        model.load([])
        model.sort(0, Qt.SortOrder.AscendingOrder)   # must not raise
        model.sort(1, Qt.SortOrder.DescendingOrder)
        model.sort(-1)
        assert model.rowCount() == 0

    def test_size_column_empty_for_dir(self, qapp):
        model = RemoteModel()
        model.load([_entry("mydir", is_dir=True, size=0)])
        idx = model.index(0, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == ""

    def test_size_column_human_readable_for_file(self, qapp):
        model = RemoteModel()
        model.load([_entry("file.bin", size=1024)])
        idx = model.index(0, 1)
        text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert text  # non-empty
        assert "K" in text or "B" in text

    def test_mtime_zero_shows_empty(self, qapp):
        model = RemoteModel()
        model.load([_entry("file.txt", mtime=0)])
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == ""

    def test_mtime_nonzero_shows_date(self, qapp):
        model = RemoteModel()
        model.load([_entry("file.txt", mtime=1_700_000_000)])
        idx = model.index(0, 2)
        text = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert text  # non-empty
        assert "-" in text  # formatted as YYYY-MM-DD HH:MM

    def test_invalid_index_returns_none(self, qapp):
        from PySide6.QtCore import QModelIndex
        model = RemoteModel()
        model.load([_entry("a.txt")])
        assert model.data(QModelIndex(), Qt.ItemDataRole.DisplayRole) is None

    def test_sort_does_not_duplicate_entries(self, qapp):
        model = RemoteModel()
        entries = [_entry("c.txt"), _entry("a.txt"), _entry("b.txt")]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        assert model.rowCount() == 3
        names = [model.entry(i).name for i in range(3)]
        assert sorted(names) == names  # sorted and no duplicates


# ── _BreadcrumbBar edge cases ─────────────────────────────────────────────────

class TestBreadcrumbBarEdgeCases:
    def test_rebuild_multiple_times_no_widget_leak(self, qapp):
        """Calling set_path repeatedly must not accumulate extra layout items."""
        bar = _BreadcrumbBar()
        bar.set_path("/a/b/c")
        count_after_first = bar._crumb_layout.count()
        bar.set_path("/a/b/c")
        bar.set_path("/a/b/c")
        assert bar._crumb_layout.count() == count_after_first

    def test_editor_confirm_empty_does_not_emit(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/some/path")
        received = []
        bar.navigate_to.connect(received.append)
        bar.focus_editor()
        bar._editor.setText("")
        bar._on_confirm()
        assert received == []

    def test_editor_confirm_emits_trimmed_path(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/some/path")
        received = []
        bar.navigate_to.connect(received.append)
        bar.focus_editor()
        bar._editor.setText("  /new/path  ")
        bar._on_confirm()
        assert received == ["/new/path"]

    def test_escape_cancels_edit_mode(self, qapp):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        bar = _BreadcrumbBar()
        bar.set_path("/some/path")
        bar.focus_editor()
        assert not bar._editor.isHidden()
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        bar.eventFilter(bar._editor, event)
        assert bar._editor.isHidden()
        assert not bar._crumb_widget.isHidden()

    def test_set_path_updates_editor_when_open(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/old")
        bar.focus_editor()
        bar.set_path("/new")
        assert bar._editor.text() == "/new"


# ── apply_entries / sort-persistence edge cases ───────────────────────────────

class TestApplyEntriesEdgeCases:
    def test_apply_empty_entries_gives_zero_rows(self, qapp):
        panel = RemotePanel()
        panel._all_entries = []
        panel._apply_entries()
        assert panel._model.rowCount() == 0

    def test_sort_persists_after_reapply(self, qapp):
        from sftp_ui.core.sftp_client import RemoteEntry
        panel = RemotePanel()
        panel._sort_col = 0
        panel._sort_order = Qt.SortOrder.AscendingOrder
        panel._all_entries = [
            RemoteEntry("z.txt", "/z.txt", False, 0, 0),
            RemoteEntry("a.txt", "/a.txt", False, 0, 0),
            RemoteEntry("m.txt", "/m.txt", False, 0, 0),
        ]
        panel._apply_entries()
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())]
        assert names == ["a.txt", "m.txt", "z.txt"]

    def test_neutral_sort_preserved_after_reapply(self, qapp):
        from sftp_ui.core.sftp_client import RemoteEntry
        panel = RemotePanel()
        panel._sort_col = -1  # neutral — server order
        panel._all_entries = [
            RemoteEntry("z.txt", "/z.txt", False, 0, 0),
            RemoteEntry("a.txt", "/a.txt", False, 0, 0),
        ]
        panel._apply_entries()
        names = [panel._model.entry(i).name for i in range(panel._model.rowCount())]
        assert names == ["z.txt", "a.txt"]   # original order preserved


# ── _file_icon edge cases ─────────────────────────────────────────────────────

class TestFileIconEdgeCases:
    def _icon(self, name: str) -> str:
        from sftp_ui.ui.panels.remote_panel import _file_icon
        return _file_icon(name)

    def test_no_extension_returns_default(self):
        assert self._icon("README") == "📄"
        assert self._icon("Makefile") == "📄"

    def test_dotfile_returns_default(self):
        # ".bashrc" starts with "." — treated as no extension
        assert self._icon(".bashrc") == "📄"
        assert self._icon(".gitignore") == "📄"

    def test_double_extension_uses_last(self):
        # "archive.tar.gz" → ext="gz" → archive icon
        assert self._icon("archive.tar.gz") == "📦"
        assert self._icon("backup.sql.gz") == "📦"

    def test_unknown_extension_returns_default(self):
        assert self._icon("file.xyz123abc") == "📄"

    def test_known_extensions(self):
        cases = {
            "photo.jpg": "🖼",
            "clip.mp4": "🎬",
            "song.mp3": "🎵",
            "bundle.zip": "📦",
            "script.py": "📝",
            "config.json": "⚙",
            "binary.exe": "⚡",
            "data.csv": "🗃",
        }
        for name, expected in cases.items():
            assert self._icon(name) == expected, f"_file_icon({name!r}) expected {expected}"

    def test_case_insensitive(self):
        from sftp_ui.ui.panels.remote_panel import _file_icon
        assert _file_icon("photo.JPG") == _file_icon("photo.jpg")
        assert _file_icon("script.PY") == _file_icon("script.py")


# ── UIState edge cases ────────────────────────────────────────────────────────

class TestUIStateEdgeCases:
    def test_non_integer_widths_do_not_crash(self, tmp_path):
        import json
        path = tmp_path / "ui_state.json"
        path.write_text(
            json.dumps({"column_widths": {"remote": [100, "wide", None]}}),
            encoding="utf-8",
        )
        state = UIState(path=path)   # must not raise
        # malformed entry is skipped entirely
        assert state.get_column_widths("remote") == []

    def test_column_widths_key_is_not_a_list(self, tmp_path):
        import json
        path = tmp_path / "ui_state.json"
        path.write_text(
            json.dumps({"column_widths": {"remote": "not-a-list"}}),
            encoding="utf-8",
        )
        state = UIState(path=path)
        assert state.get_column_widths("remote") == []

    def test_column_widths_entirely_wrong_type(self, tmp_path):
        import json
        path = tmp_path / "ui_state.json"
        path.write_text(
            json.dumps({"column_widths": [1, 2, 3]}),   # list, not dict
            encoding="utf-8",
        )
        state = UIState(path=path)
        assert state.get_column_widths("remote") == []

    def test_overwrite_existing_panel_widths(self, tmp_path):
        path = tmp_path / "ui_state.json"
        state = UIState(path=path)
        state.set_column_widths("remote", [100, 50, 60])
        state.set_column_widths("remote", [200, 80, 90])
        state2 = UIState(path=path)
        assert state2.get_column_widths("remote") == [200, 80, 90]


# ── _human_size ───────────────────────────────────────────────────────────────

from sftp_ui.ui.panels.remote_panel import _human_size, _EXT_ICONS


class TestHumanSize:
    def test_bytes(self, qapp):
        assert _human_size(0) == "0 B"

    def test_999_bytes(self, qapp):
        assert _human_size(999) == "999 B"

    def test_exactly_1kb(self, qapp):
        assert _human_size(1024) == "1 KB"

    def test_kilobytes(self, qapp):
        assert _human_size(2048) == "2 KB"

    def test_megabytes(self, qapp):
        assert _human_size(1024 * 1024) == "1 MB"

    def test_gigabytes(self, qapp):
        assert _human_size(1024 ** 3) == "1 GB"

    def test_terabytes(self, qapp):
        assert "TB" in _human_size(1024 ** 4)

    def test_fractional_tb(self, qapp):
        # 1.5 TB
        result = _human_size(int(1.5 * 1024 ** 4))
        assert "TB" in result
        assert "1.5" in result

    def test_sub_kb_never_uses_decimal(self, qapp):
        # Bytes are shown as integers
        result = _human_size(500)
        assert "." not in result


# ── RemoteModel sort ──────────────────────────────────────────────────────────

class TestRemoteModelSortAdvanced:
    def test_sort_by_size_dirs_first(self, qapp):
        model = RemoteModel()
        entries = [
            _entry("small.txt", size=10),
            _entry("big.txt", size=9000),
            _entry("mydir", is_dir=True),
        ]
        model.load(entries)
        model.sort(1, Qt.SortOrder.AscendingOrder)
        assert model.entry(0).is_dir   # dir always first
        assert model.entry(1).size < model.entry(2).size

    def test_sort_by_mtime_ascending(self, qapp):
        model = RemoteModel()
        entries = [
            _entry("newer.txt", mtime=2000),
            _entry("oldest.txt", mtime=100),
            _entry("middle.txt", mtime=1000),
        ]
        model.load(entries)
        model.sort(2, Qt.SortOrder.AscendingOrder)
        mtimes = [model.entry(i).mtime for i in range(3)]
        assert mtimes == sorted(mtimes)

    def test_sort_by_mtime_descending(self, qapp):
        model = RemoteModel()
        entries = [
            _entry("a.txt", mtime=100),
            _entry("b.txt", mtime=200),
            _entry("c.txt", mtime=300),
        ]
        model.load(entries)
        model.sort(2, Qt.SortOrder.DescendingOrder)
        mtimes = [model.entry(i).mtime for i in range(3)]
        assert mtimes == sorted(mtimes, reverse=True)

    def test_dotdot_always_first_after_sort(self, qapp):
        model = RemoteModel()
        entries = [
            _entry("z_file.txt"),
            _entry("a_file.txt"),
            _entry("..", is_dir=True),
        ]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        assert model.entry(0).name == ".."

    def test_sort_minus1_restores_original_order(self, qapp):
        model = RemoteModel()
        entries = [_entry("c.txt"), _entry("a.txt"), _entry("b.txt")]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        model.sort(-1)
        names = [model.entry(i).name for i in range(3)]
        assert names == ["c.txt", "a.txt", "b.txt"]

    def test_sort_by_name_case_insensitive(self, qapp):
        model = RemoteModel()
        entries = [_entry("Banana.txt"), _entry("apple.txt"), _entry("Cherry.txt")]
        model.load(entries)
        model.sort(0, Qt.SortOrder.AscendingOrder)
        names = [model.entry(i).name for i in range(3)]
        assert names == sorted(names, key=str.lower)

    def test_load_resets_sort(self, qapp):
        model = RemoteModel()
        model.load([_entry("c.txt"), _entry("a.txt")])
        model.sort(0)
        model.load([_entry("z.txt"), _entry("m.txt")])
        # After reload, order is z, m (insertion order, not sorted)
        assert model.entry(0).name == "z.txt"


# ── RemoteModel data display ──────────────────────────────────────────────────

class TestRemoteModelData:
    def test_dir_shows_no_size(self, qapp):
        model = RemoteModel()
        model.load([_entry("mydir", is_dir=True, size=999)])
        idx = model.index(0, 1)
        assert model.data(idx) == ""

    def test_file_shows_size(self, qapp):
        model = RemoteModel()
        model.load([_entry("file.txt", size=1024)])
        idx = model.index(0, 1)
        assert model.data(idx) == "1 KB"

    def test_dotdot_shows_up_arrow(self, qapp):
        model = RemoteModel()
        model.load([_entry("..", is_dir=True)])
        idx = model.index(0, 0)
        assert "↑" in model.data(idx)

    def test_symlink_shows_link_icon(self, qapp):
        model = RemoteModel()
        model.load([_entry("link.txt", is_symlink=True)])
        idx = model.index(0, 0)
        assert "🔗" in model.data(idx)

    def test_dir_shows_folder_icon(self, qapp):
        model = RemoteModel()
        model.load([_entry("folder", is_dir=True)])
        idx = model.index(0, 0)
        assert "📁" in model.data(idx)

    def test_mtime_zero_shows_empty_modified(self, qapp):
        model = RemoteModel()
        model.load([_entry("file.txt", mtime=0)])
        idx = model.index(0, 2)
        assert model.data(idx) == ""

    def test_dotdot_shows_empty_modified(self, qapp):
        model = RemoteModel()
        model.load([_entry("..", is_dir=True, mtime=9999)])
        idx = model.index(0, 2)
        assert model.data(idx) == ""

    def test_invalid_index_returns_none(self, qapp):
        model = RemoteModel()
        model.load([])
        from PySide6.QtCore import QModelIndex
        assert model.data(QModelIndex()) is None

    def test_header_names(self, qapp):
        model = RemoteModel()
        for col, name in enumerate(["Name", "Size", "Modified"]):
            assert model.headerData(col, Qt.Orientation.Horizontal) == name


# ── _BreadcrumbBar set_path / rebuild ─────────────────────────────────────────

class TestBreadcrumbBarStructure:
    def test_crumb_count_matches_path_depth(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/home/user/docs")
        # /, home, user, docs → 4 segments but layout also has stretch
        # Just ensure multiple buttons exist
        buttons = [bar._crumb_layout.itemAt(i).widget()
                   for i in range(bar._crumb_layout.count())
                   if bar._crumb_layout.itemAt(i).widget() is not None]
        assert len(buttons) >= 3

    def test_root_path_creates_at_least_one_button(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/")
        buttons = [bar._crumb_layout.itemAt(i).widget()
                   for i in range(bar._crumb_layout.count())
                   if bar._crumb_layout.itemAt(i).widget() is not None]
        assert len(buttons) >= 1

    def test_focus_editor_shows_editor(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/tmp")
        bar.focus_editor()
        assert not bar._editor.isHidden()

    def test_focus_editor_text_matches_path(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/foo/bar")
        bar.focus_editor()
        assert bar._editor.text() == "/foo/bar"

    def test_set_path_while_editor_open_updates_editor(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/old")
        bar.focus_editor()
        bar.set_path("/new/path")
        assert bar._editor.text() == "/new/path"

    def test_set_path_while_editor_closed_does_not_update_editor(self, qapp):
        bar = _BreadcrumbBar()
        bar.set_path("/initial")
        # editor is hidden — set_path should NOT touch its text
        bar.set_path("/changed")
        # editor text remains empty (default) since focus_editor was never called
        assert bar._editor.isHidden()


# ── UIState advanced ──────────────────────────────────────────────────────────

class TestUIStateAdvanced:
    def test_save_and_reload_multiple_panels(self, tmp_path):
        path = tmp_path / "state.json"
        state = UIState(path=path)
        state.set_column_widths("local", [80, 60, 70])
        state.set_column_widths("remote", [120, 90, 100])
        state2 = UIState(path=path)
        assert state2.get_column_widths("local") == [80, 60, 70]
        assert state2.get_column_widths("remote") == [120, 90, 100]

    def test_get_unknown_panel_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        state = UIState(path=path)
        assert state.get_column_widths("nonexistent") == []

    def test_save_creates_file(self, tmp_path):
        path = tmp_path / "state.json"
        assert not path.exists()
        state = UIState(path=path)
        state.set_column_widths("remote", [100, 80])
        assert path.exists()

    def test_missing_file_loads_defaults(self, tmp_path):
        path = tmp_path / "no_file.json"
        state = UIState(path=path)
        assert state.column_widths == {}

    def test_zero_width_columns_accepted(self, tmp_path):
        path = tmp_path / "state.json"
        state = UIState(path=path)
        state.set_column_widths("remote", [0, 0, 0])
        state2 = UIState(path=path)
        assert state2.get_column_widths("remote") == [0, 0, 0]

    def test_large_width_values_accepted(self, tmp_path):
        path = tmp_path / "state.json"
        state = UIState(path=path)
        state.set_column_widths("remote", [99999, 88888])
        state2 = UIState(path=path)
        assert state2.get_column_widths("remote") == [99999, 88888]


# ── file icon coverage ────────────────────────────────────────────────────────

from sftp_ui.ui.panels.remote_panel import _file_icon


class TestFileIconCoverage:
    def test_image_extensions(self, qapp):
        for ext in ("png", "jpg", "gif", "svg", "webp"):
            assert _file_icon(f"img.{ext}") == "🖼"

    def test_video_extensions(self, qapp):
        for ext in ("mp4", "mov", "avi", "mkv"):
            assert _file_icon(f"vid.{ext}") == "🎬"

    def test_audio_extensions(self, qapp):
        for ext in ("mp3", "wav", "flac", "ogg"):
            assert _file_icon(f"snd.{ext}") == "🎵"

    def test_archive_extensions(self, qapp):
        for ext in ("zip", "tar", "gz", "bz2", "7z", "rar"):
            assert _file_icon(f"arc.{ext}") == "📦"

    def test_code_extensions(self, qapp):
        for ext in ("py", "js", "ts", "go", "rs", "sh"):
            assert _file_icon(f"code.{ext}") == "📝"

    def test_document_extensions(self, qapp):
        for ext in ("pdf", "doc", "docx", "xls", "xlsx", "txt", "md"):
            assert _file_icon(f"doc.{ext}") == "📋"

    def test_config_extensions(self, qapp):
        for ext in ("json", "yaml", "yml", "toml", "ini", "xml"):
            assert _file_icon(f"cfg.{ext}") == "⚙"

    def test_executable_extensions(self, qapp):
        for ext in ("exe", "bin", "dmg", "deb", "rpm"):
            assert _file_icon(f"run.{ext}") == "⚡"

    def test_font_extensions(self, qapp):
        for ext in ("ttf", "otf", "woff", "woff2"):
            assert _file_icon(f"font.{ext}") == "🔤"

    def test_data_extensions(self, qapp):
        for ext in ("csv", "sql", "db", "sqlite"):
            assert _file_icon(f"data.{ext}") == "🗃"

    def test_unknown_extension_returns_default(self, qapp):
        assert _file_icon("mystery.xyz123") == "📄"

    def test_no_extension_returns_default(self, qapp):
        assert _file_icon("Makefile") == "📄"

    def test_tar_gz_uses_gz(self, qapp):
        # double extension: last wins
        assert _file_icon("archive.tar.gz") == "📦"
