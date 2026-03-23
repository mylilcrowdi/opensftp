"""
LocalPanel — local filesystem browser.

Context menu (right-click):
  On item:   Open  |  Rename  |  Delete  |  Copy Path  |  ─  |  Info
  Always:    New Folder  |  New File  |  Paste (copy from clipboard)
"""
from __future__ import annotations

import os
import shutil
import stat
import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QHBoxLayout, QInputDialog, QLabel,
    QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from sftp_ui.core.platform_utils import file_manager_action_label, open_in_file_manager


class LocalPanel(QWidget):
    path_changed = Signal(str)
    status_message = Signal(str)

    def __init__(self, initial_path: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self._cwd = initial_path if (initial_path and Path(initial_path).is_dir()) else str(Path.home())
        self._sort_col: int = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._show_hidden: bool = False
        self._build_ui()
        self._populate(self._cwd)

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Local")
        header.setObjectName("section-header")
        header_row.addWidget(header, stretch=1)

        self._hidden_btn = QPushButton(".*")
        self._hidden_btn.setCheckable(True)
        self._hidden_btn.setChecked(False)
        self._hidden_btn.setToolTip("Show hidden files (dotfiles)")
        self._hidden_btn.setFixedWidth(32)
        self._hidden_btn.setObjectName("hidden-toggle")
        self._hidden_btn.toggled.connect(self._on_hidden_toggled)
        header_row.addWidget(self._hidden_btn)

        layout.addLayout(header_row)

        self._path_label = QLabel(self._cwd)
        self._path_label.setObjectName("path-label")
        # Do NOT use setWordWrap — wrapping lets the label grow vertically on
        # narrow windows, pushing the tree widget down.  Instead cap the label
        # to a single line; long paths are elided in _populate().
        self._path_label.setMinimumWidth(0)
        self._path_label.setMaximumHeight(24)
        layout.addWidget(self._path_label)

        self._tree = _LocalDragTree(self)
        self._tree.setHeaderLabels(["Name", "Size", "Modified"])
        self._tree.header().setMinimumSectionSize(60)
        self._tree.header().resizeSection(0, 200)
        self._tree.header().resizeSection(1, 70)
        self._tree.header().resizeSection(2, 130)
        self._tree.header().setSortIndicatorShown(True)
        self._tree.header().setSortIndicatorClearable(True)
        self._tree.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._tree.header().sectionClicked.connect(self._on_header_click)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setDragEnabled(True)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _populate(self, path: str) -> None:
        self._tree.clear()
        self._cwd = path
        # Elide long paths with "…" in the middle so the label never wraps.
        fm = self._path_label.fontMetrics()
        available = max(40, self._path_label.width() - 6)
        from PySide6.QtCore import Qt as _Qt
        elided = fm.elidedText(path, _Qt.TextElideMode.ElideMiddle, available)
        self._path_label.setText(elided)
        self._path_label.setToolTip(path)
        self.path_changed.emit(path)

        if path != "/":
            up = QTreeWidgetItem([".."])
            up.setData(0, Qt.ItemDataRole.UserRole, str(Path(path).parent))
            up.setData(0, Qt.ItemDataRole.UserRole + 1, True)
            self._tree.addTopLevelItem(up)

        try:
            entries = sorted(
                os.scandir(path),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            return

        def _fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.0f} {unit}"
                n /= 1024
            return f"{n:.1f} TB"

        n_dirs  = 0
        n_files = 0
        for entry in entries:
            # Filter dotfiles unless the hidden-files toggle is active
            if not self._show_hidden and entry.name.startswith("."):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                st = None
            if entry.is_dir(follow_symlinks=False):
                n_dirs += 1
                icon = "📁 "
                size_str = ""
            else:
                n_files += 1
                icon = "   "
                size_bytes = st.st_size if st else 0
                size_str = _fmt_size(size_bytes)
            mtime_str = (
                datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                if st else ""
            )

            item = QTreeWidgetItem([icon + entry.name, size_str, mtime_str])
            item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, entry.is_dir(follow_symlinks=False))
            self._tree.addTopLevelItem(item)

        # Re-apply current sort if the user previously clicked a column header
        if self._sort_col != -1:
            self._tree.setSortingEnabled(True)
            self._tree.sortByColumn(self._sort_col, self._sort_order)
            self._tree.setSortingEnabled(False)

        self.status_message.emit(
            f"{n_dirs} folder{'s' if n_dirs != 1 else ''}, "
            f"{n_files} file{'s' if n_files != 1 else ''}"
        )

    # ── Hidden files toggle ────────────────────────────────────────────────────

    def _on_hidden_toggled(self, checked: bool) -> None:
        self._show_hidden = checked
        self._populate(self._cwd)   # instant — no I/O overhead beyond scandir

    # ── Sorting ────────────────────────────────────────────────────────────────

    def _on_header_click(self, col: int) -> None:
        """Cycle sort for clicked column: neutral → asc → desc → neutral."""
        hdr = self._tree.header()
        if self._sort_col != col:
            self._sort_col = col
            self._sort_order = Qt.SortOrder.AscendingOrder
        elif self._sort_order == Qt.SortOrder.AscendingOrder:
            self._sort_order = Qt.SortOrder.DescendingOrder
        else:
            self._sort_col = -1

        if self._sort_col == -1:
            hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            # Restore natural dirs-first order
            self._populate(self._cwd)
        else:
            hdr.setSortIndicator(self._sort_col, self._sort_order)
            self._tree.setSortingEnabled(True)
            self._tree.sortByColumn(self._sort_col, self._sort_order)
            self._tree.setSortingEnabled(False)

    def _on_double_click(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if is_dir and path:
            self._populate(path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def current_path(self) -> str:
        return self._cwd

    def selected_paths(self) -> list[str]:
        """Return paths of all selected items (files AND directories).

        The '..' entry is excluded — it's a navigation aid, not a real path.
        Callers that only want files can filter with os.path.isfile().
        """
        paths = []
        for item in self._tree.selectedItems():
            p = item.data(0, Qt.ItemDataRole.UserRole)
            is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1)
            # Skip the '..' navigation item (its path equals the parent dir)
            if p and not (is_dir and p == str(Path(self._cwd).parent)):
                paths.append(p)
        return paths

    def _item_at(self, pos) -> Optional[QTreeWidgetItem]:
        return self._tree.itemAt(pos)

    # ── Context menu ───────────────────────────────────────────────────────────

    def _on_context_menu(self, pos) -> None:
        item = self._item_at(pos)
        item_path = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        item_is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1) if item else None
        # Skip ".." item
        if item_path and item_path == str(Path(self._cwd).parent):
            item_path = None

        menu = QMenu(self)

        if item_path:
            if not item_is_dir:
                open_act = menu.addAction(file_manager_action_label(is_dir=False))
            else:
                open_act = menu.addAction(file_manager_action_label(is_dir=True))
            menu.addSeparator()
            rename_act = menu.addAction("✏  Rename")
            delete_act = menu.addAction("🗑  Delete")
            menu.addSeparator()
            copy_path_act = menu.addAction("📋  Copy Path")
            menu.addSeparator()
            info_act = menu.addAction("ℹ  Info")
            menu.addSeparator()
        else:
            open_act = rename_act = delete_act = copy_path_act = info_act = None

        new_folder_act = menu.addAction("📁  New Folder")
        new_file_act = menu.addAction("📄  New File")

        # Paste from clipboard
        clipboard = QApplication.clipboard()
        clip_paths = []
        if clipboard.mimeData().hasUrls():
            clip_paths = [u.toLocalFile() for u in clipboard.mimeData().urls() if u.isLocalFile()]
        paste_act = menu.addAction(f"📋  Paste ({len(clip_paths)} file(s))") if clip_paths else None

        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action is None:
            return

        if action == open_act and item_path:
            open_in_file_manager(item_path)
        elif action == rename_act and item_path:
            self._do_rename(item_path, item_is_dir)
        elif action == delete_act and item_path:
            self._do_delete(item_path, item_is_dir)
        elif action == copy_path_act and item_path:
            QApplication.clipboard().setText(item_path)
        elif action == info_act and item_path:
            self._show_info(item_path)
        elif action == new_folder_act:
            self._do_new_folder()
        elif action == new_file_act:
            self._do_new_file()
        elif action == paste_act and clip_paths:
            self._do_paste(clip_paths)

    # ── Operations ─────────────────────────────────────────────────────────────

    def _do_rename(self, path: str, is_dir: bool) -> None:
        old_name = os.path.basename(path)
        new_name, ok = QInputDialog.getText(
            self, "Rename", f"Rename '{old_name}' to:", text=old_name,
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        if "/" in new_name.strip() or os.sep != "/" and os.sep in new_name.strip():
            QMessageBox.warning(self, "Invalid name", "Name must not contain path separators.")
            return
        new_path = os.path.join(os.path.dirname(path), new_name.strip())
        try:
            os.rename(path, new_path)
        except OSError as exc:
            QMessageBox.warning(self, "Rename failed", str(exc))
        self._populate(self._cwd)

    def _do_delete(self, path: str, is_dir: bool) -> None:
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete '{os.path.basename(path)}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
        self._populate(self._cwd)

    def _do_new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        if "/" in name.strip() or os.sep != "/" and os.sep in name.strip():
            QMessageBox.warning(self, "Invalid name", "Folder name must not contain path separators.")
            return
        try:
            os.mkdir(os.path.join(self._cwd, name.strip()))
        except OSError as exc:
            QMessageBox.warning(self, "Error", str(exc))
        self._populate(self._cwd)

    def _do_new_file(self) -> None:
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if not ok or not name.strip():
            return
        if "/" in name.strip() or os.sep != "/" and os.sep in name.strip():
            QMessageBox.warning(self, "Invalid name", "File name must not contain path separators.")
            return
        path = os.path.join(self._cwd, name.strip())
        try:
            with open(path, "w"):
                pass
        except OSError as exc:
            QMessageBox.warning(self, "Error", str(exc))
        self._populate(self._cwd)

    def _do_paste(self, paths: list[str]) -> None:
        for src in paths:
            dst = os.path.join(self._cwd, os.path.basename(src))
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            except OSError as exc:
                QMessageBox.warning(self, "Paste failed", str(exc))
        self._populate(self._cwd)

    def _show_info(self, path: str) -> None:
        try:
            s = os.stat(path)
        except OSError as exc:
            QMessageBox.warning(self, "Error", str(exc))
            return
        is_dir = os.path.isdir(path)
        mtime = datetime.datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        ctime = datetime.datetime.fromtimestamp(s.st_ctime).strftime("%Y-%m-%d %H:%M:%S")

        def _fmt(n):
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} TB"

        info = (
            f"<b>Name:</b> {os.path.basename(path)}<br>"
            f"<b>Path:</b> {path}<br>"
            f"<b>Type:</b> {'Directory' if is_dir else 'File'}<br>"
            f"<b>Size:</b> {_fmt(s.st_size)}<br>"
            f"<b>Modified:</b> {mtime}<br>"
            f"<b>Created:</b> {ctime}<br>"
            f"<b>Permissions:</b> {oct(stat.S_IMODE(s.st_mode))}"
        )
        msg = QMessageBox(self)
        msg.setWindowTitle("File Info")
        msg.setText(info)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()


# ── Sortable item ──────────────────────────────────────────────────────────────

# ── Drag tree ──────────────────────────────────────────────────────────────────

class _LocalDragTree(QTreeWidget):
    def __init__(self, panel: LocalPanel) -> None:
        super().__init__()
        self._panel = panel

    def mimeData(self, items):
        paths = self._panel.selected_paths()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return mime

    def keyPressEvent(self, event) -> None:
        key = event.key()
        items = self.selectedItems()
        # Filter out ".." item
        items = [it for it in items if it.data(0, Qt.ItemDataRole.UserRole) != str(Path(self._panel._cwd).parent)]

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if len(items) == 1:
                path = items[0].data(0, Qt.ItemDataRole.UserRole)
                is_dir = items[0].data(0, Qt.ItemDataRole.UserRole + 1)
                if is_dir and path:
                    self._panel._populate(path)
            return

        if key == Qt.Key.Key_Backspace:
            cwd = self._panel._cwd
            if cwd != str(Path(cwd).parent):  # not at root
                self._panel._populate(str(Path(cwd).parent))
            return

        if key == Qt.Key.Key_Delete and items:
            for it in items:
                path = it.data(0, Qt.ItemDataRole.UserRole)
                is_dir = it.data(0, Qt.ItemDataRole.UserRole + 1)
                if path:
                    self._panel._do_delete(path, is_dir)
            return

        if key == Qt.Key.Key_F2 and len(items) == 1:
            path = items[0].data(0, Qt.ItemDataRole.UserRole)
            is_dir = items[0].data(0, Qt.ItemDataRole.UserRole + 1)
            if path:
                self._panel._do_rename(path, is_dir)
            return

        super().keyPressEvent(event)


