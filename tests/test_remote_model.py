"""
Tests for RemoteModel, _file_icon, and _human_size in remote_panel.

Covers: _file_icon (known exts, unknown ext, dotfiles, case insensitivity),
        _human_size (byte ranges, boundaries), RemoteModel.sort() (by name/
        size/mtime, dirs-before-files, ".." always pinned, descending,
        col=-1 restores original), RemoteModel.data() (display text for each
        column, dotdot, dir, symlink, file, size "", mtime "").
"""
from __future__ import annotations

import sys
import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtWidgets import QApplication

from sftp_ui.core.sftp_client import RemoteEntry
from sftp_ui.ui.panels.remote_panel import RemoteModel, _file_icon, _human_size


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _e(name, *, is_dir=False, size=0, mtime=1_700_000_000,
       path=None, is_symlink=False) -> RemoteEntry:
    return RemoteEntry(
        name=name,
        path=path or f"/remote/{name}",
        is_dir=is_dir,
        size=size,
        mtime=mtime,
        is_symlink=is_symlink,
    )


def _dotdot() -> RemoteEntry:
    return RemoteEntry(name="..", path="/remote", is_dir=True, size=0, mtime=0)


# ── _file_icon() ──────────────────────────────────────────────────────────────

class TestFileIcon:
    def test_image_extension(self):
        assert _file_icon("photo.png") == "🖼"

    def test_jpg_extension(self):
        assert _file_icon("photo.jpg") == "🖼"

    def test_video_extension(self):
        assert _file_icon("clip.mp4") == "🎬"

    def test_audio_extension(self):
        assert _file_icon("song.mp3") == "🎵"

    def test_archive_extension(self):
        assert _file_icon("backup.zip") == "📦"

    def test_tar_gz_extension(self):
        assert _file_icon("archive.tgz") == "📦"

    def test_python_extension(self):
        assert _file_icon("script.py") == "📝"

    def test_javascript_extension(self):
        assert _file_icon("app.js") == "📝"

    def test_document_extension(self):
        assert _file_icon("report.pdf") == "📋"

    def test_markdown_extension(self):
        assert _file_icon("README.md") == "📋"

    def test_config_json(self):
        assert _file_icon("config.json") == "⚙"

    def test_config_yaml(self):
        assert _file_icon("config.yml") == "⚙"

    def test_csv_data(self):
        assert _file_icon("data.csv") == "🗃"

    def test_executable(self):
        assert _file_icon("setup.exe") == "⚡"

    def test_font(self):
        assert _file_icon("font.ttf") == "🔤"

    def test_unknown_extension_returns_default(self):
        assert _file_icon("file.xyz123") == "📄"

    def test_no_extension_returns_default(self):
        assert _file_icon("Makefile") == "📄"

    def test_dotfile_returns_default(self):
        assert _file_icon(".bashrc") == "📄"

    def test_dotfile_with_ext_returns_default(self):
        """'.env.local' starts with '.', treated as no-extension dotfile."""
        assert _file_icon(".env.local") == "📄"

    def test_case_insensitive_extension(self):
        """PNG (uppercase) should match same as png."""
        assert _file_icon("IMAGE.PNG") == "🖼"

    def test_mixed_case_extension(self):
        assert _file_icon("Movie.MOV") == "🎬"

    def test_double_extension_uses_last(self):
        """archive.tar.gz → last ext is 'gz' → archive icon."""
        assert _file_icon("archive.tar.gz") == "📦"


# ── _human_size() (remote_panel version) ─────────────────────────────────────

class TestHumanSizeRemote:
    def test_bytes_small(self):
        assert _human_size(512) == "512 B"

    def test_exactly_1023_bytes(self):
        assert _human_size(1023) == "1023 B"

    def test_exactly_1024_is_kb(self):
        assert _human_size(1024) == "1 KB"

    def test_1_mb(self):
        assert _human_size(1024 * 1024) == "1 MB"

    def test_1_gb(self):
        assert _human_size(1024 ** 3) == "1 GB"

    def test_1_tb(self):
        result = _human_size(1024 ** 4)
        assert "TB" in result

    def test_fractional_kb(self):
        assert "KB" in _human_size(1536)


# ── RemoteModel.sort() ────────────────────────────────────────────────────────

class TestRemoteModelSort:
    def test_sort_by_name_ascending(self, qapp):
        model = RemoteModel()
        model.load([_e("z.txt"), _e("a.txt"), _e("m.txt")])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names == sorted(names, key=str.lower)

    def test_sort_by_name_descending(self, qapp):
        model = RemoteModel()
        model.load([_e("a.txt"), _e("z.txt"), _e("m.txt")])
        model.sort(0, Qt.SortOrder.DescendingOrder)
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names == sorted(names, key=str.lower, reverse=True)

    def test_sort_by_name_case_insensitive(self, qapp):
        model = RemoteModel()
        model.load([_e("Zebra.txt"), _e("apple.txt"), _e("Mango.txt")])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names == sorted(names, key=str.lower)

    def test_dirs_before_files_on_name_sort(self, qapp):
        model = RemoteModel()
        model.load([
            _e("z_file.txt"),
            _e("a_dir", is_dir=True),
            _e("m_file.txt"),
        ])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        entries = [model.entry(i) for i in range(model.rowCount())]
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        # All dirs come before all files
        assert entries.index(dirs[-1]) < entries.index(files[0])

    def test_dotdot_pinned_to_top_after_sort(self, qapp):
        model = RemoteModel()
        model.load([_dotdot(), _e("z.txt"), _e("a.txt")])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        assert model.entry(0).name == ".."

    def test_dotdot_pinned_descending(self, qapp):
        model = RemoteModel()
        model.load([_dotdot(), _e("a.txt"), _e("z.txt")])
        model.sort(0, Qt.SortOrder.DescendingOrder)
        assert model.entry(0).name == ".."

    def test_sort_by_size_ascending(self, qapp):
        model = RemoteModel()
        model.load([_e("big.bin", size=9000), _e("small.bin", size=10),
                    _e("mid.bin", size=500)])
        model.sort(1, Qt.SortOrder.AscendingOrder)
        sizes = [model.entry(i).size for i in range(model.rowCount())]
        assert sizes == sorted(sizes)

    def test_sort_by_size_descending(self, qapp):
        model = RemoteModel()
        model.load([_e("big.bin", size=9000), _e("small.bin", size=10),
                    _e("mid.bin", size=500)])
        model.sort(1, Qt.SortOrder.DescendingOrder)
        sizes = [model.entry(i).size for i in range(model.rowCount())]
        assert sizes == sorted(sizes, reverse=True)

    def test_sort_by_size_dirs_before_files(self, qapp):
        model = RemoteModel()
        model.load([_e("big.bin", size=9000), _e("adir", is_dir=True, size=0)])
        model.sort(1, Qt.SortOrder.AscendingOrder)
        assert model.entry(0).is_dir

    def test_sort_by_mtime_ascending(self, qapp):
        model = RemoteModel()
        model.load([
            _e("new.txt", mtime=2000),
            _e("old.txt", mtime=1000),
            _e("mid.txt", mtime=1500),
        ])
        model.sort(2, Qt.SortOrder.AscendingOrder)
        mtimes = [model.entry(i).mtime for i in range(model.rowCount())]
        assert mtimes == sorted(mtimes)

    def test_sort_by_mtime_descending(self, qapp):
        model = RemoteModel()
        model.load([
            _e("new.txt", mtime=2000),
            _e("old.txt", mtime=1000),
        ])
        model.sort(2, Qt.SortOrder.DescendingOrder)
        assert model.entry(0).mtime > model.entry(1).mtime

    def test_col_minus1_restores_original_order(self, qapp):
        entries = [_e("z.txt"), _e("a.txt"), _e("m.txt")]
        model = RemoteModel()
        model.load(entries)
        original_names = [e.name for e in entries]
        model.sort(0, Qt.SortOrder.AscendingOrder)   # scramble
        model.sort(-1)                                # restore
        names = [model.entry(i).name for i in range(model.rowCount())]
        assert names == original_names

    def test_empty_model_sort_no_crash(self, qapp):
        model = RemoteModel()
        model.load([])
        model.sort(0)   # must not raise


# ── RemoteModel.data() ────────────────────────────────────────────────────────

class TestRemoteModelData:
    def _col(self, model, row, col, role=Qt.ItemDataRole.DisplayRole):
        return model.data(model.index(row, col), role)

    def test_dotdot_col0_shows_arrow(self, qapp):
        model = RemoteModel()
        model.load([_dotdot()])
        assert self._col(model, 0, 0) == "↑  .."

    def test_dotdot_col1_empty(self, qapp):
        model = RemoteModel()
        model.load([_dotdot()])
        assert self._col(model, 0, 1) == ""

    def test_dotdot_col2_empty(self, qapp):
        model = RemoteModel()
        model.load([_dotdot()])
        assert self._col(model, 0, 2) == ""

    def test_dir_col0_has_folder_icon(self, qapp):
        model = RemoteModel()
        model.load([_e("photos", is_dir=True)])
        text = self._col(model, 0, 0)
        assert "📁" in text and "photos" in text

    def test_dir_col1_size_empty(self, qapp):
        model = RemoteModel()
        model.load([_e("uploads", is_dir=True, size=999)])
        assert self._col(model, 0, 1) == ""

    def test_symlink_col0_has_link_icon(self, qapp):
        model = RemoteModel()
        model.load([_e("link_to_something", is_symlink=True)])
        text = self._col(model, 0, 0)
        assert "🔗" in text

    def test_file_col0_contains_name(self, qapp):
        model = RemoteModel()
        model.load([_e("report.pdf")])
        text = self._col(model, 0, 0)
        assert "report.pdf" in text

    def test_file_col0_has_icon(self, qapp):
        model = RemoteModel()
        model.load([_e("image.png")])
        text = self._col(model, 0, 0)
        assert "🖼" in text

    def test_file_col1_shows_size(self, qapp):
        model = RemoteModel()
        model.load([_e("data.bin", size=2048)])
        text = self._col(model, 0, 1)
        assert "KB" in text or "2" in text

    def test_file_col2_shows_date(self, qapp):
        model = RemoteModel()
        model.load([_e("f.txt", mtime=1_700_000_000)])
        text = self._col(model, 0, 2)
        assert "2023" in text or "2024" in text   # timezone-safe

    def test_file_col2_zero_mtime_empty(self, qapp):
        model = RemoteModel()
        model.load([_e("f.txt", mtime=0)])
        assert self._col(model, 0, 2) == ""

    def test_invalid_index_returns_none(self, qapp):
        model = RemoteModel()
        model.load([])
        assert model.data(QModelIndex()) is None

    def test_non_display_role_returns_none(self, qapp):
        model = RemoteModel()
        model.load([_e("f.txt")])
        result = model.data(model.index(0, 0), Qt.ItemDataRole.EditRole)
        assert result is None
