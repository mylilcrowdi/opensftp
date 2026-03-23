"""
RemotePanel — remote SFTP filesystem browser.

Context menu (right-click):
  On item:   Download  |  Rename  |  Delete  |  ─  |  Info
  Always:    New Folder  |  New File  |  Paste (upload from clipboard)

Download → emits download_requested(entries) → MainWindow queues the jobs.
All mutating operations run in a background thread to keep UI responsive.
"""
from __future__ import annotations

import datetime
import threading
import time
from pathlib import PurePosixPath
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QEvent, QMimeData, QModelIndex, Qt, Signal, QObject
from PySide6.QtGui import QClipboard, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QInputDialog,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QTableView, QVBoxLayout, QWidget,
)

from sftp_ui.ui.widgets.skeleton_widget import SkeletonWidget

from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from PySide6.QtWidgets import QDialog as _QDialog
from sftp_ui.ui.dialogs.permissions_dialog import PermissionsDialog


_COLUMNS = ["Name", "Size", "Modified"]

_EXT_ICONS: dict[str, str] = {
    # Images
    "png": "🖼", "jpg": "🖼", "jpeg": "🖼", "gif": "🖼", "svg": "🖼", "webp": "🖼", "bmp": "🖼", "ico": "🖼",
    # Video
    "mp4": "🎬", "mov": "🎬", "avi": "🎬", "mkv": "🎬", "webm": "🎬", "m4v": "🎬",
    # Audio
    "mp3": "🎵", "wav": "🎵", "flac": "🎵", "ogg": "🎵", "m4a": "🎵", "aac": "🎵",
    # Archives
    "zip": "📦", "tar": "📦", "gz": "📦", "bz2": "📦", "xz": "📦", "7z": "📦", "rar": "📦", "tgz": "📦",
    # Code
    "py": "📝", "js": "📝", "ts": "📝", "rb": "📝", "go": "📝", "rs": "📝",
    "c": "📝", "cpp": "📝", "h": "📝", "java": "📝", "php": "📝", "sh": "📝",
    "css": "📝", "html": "📝", "htm": "📝", "jsx": "📝", "tsx": "📝", "vue": "📝",
    # Documents
    "pdf": "📋", "doc": "📋", "docx": "📋", "xls": "📋", "xlsx": "📋",
    "ppt": "📋", "pptx": "📋", "txt": "📋", "md": "📋", "rst": "📋",
    # Config
    "json": "⚙", "yaml": "⚙", "yml": "⚙", "toml": "⚙", "ini": "⚙", "xml": "⚙", "env": "⚙",
    # Executables / packages
    "exe": "⚡", "bin": "⚡", "dmg": "⚡", "pkg": "⚡", "deb": "⚡", "rpm": "⚡", "appimage": "⚡",
    # Fonts
    "ttf": "🔤", "otf": "🔤", "woff": "🔤", "woff2": "🔤",
    # Data
    "csv": "🗃", "sql": "🗃", "db": "🗃", "sqlite": "🗃",
}


def _duplicate_name(name: str, n: int = 1) -> str:
    """Return the nth copy name for *name*.

    n=1  →  "report copy.pdf"   /  "archive copy"
    n=2  →  "report copy 2.pdf" /  "archive copy 2"
    """
    suffix = " copy" if n == 1 else f" copy {n}"
    if "." in name and not name.startswith("."):
        base, ext = name.rsplit(".", 1)
        return f"{base}{suffix}.{ext}"
    return f"{name}{suffix}"


def _file_icon(name: str) -> str:
    """Return an emoji icon for the file based on its extension."""
    if "." in name and not name.startswith("."):
        ext = name.rsplit(".", 1)[-1].lower()
        return _EXT_ICONS.get(ext, "📄")
    return "📄"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Model ──────────────────────────────────────────────────────────────────────

class RemoteModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._entries: list[RemoteEntry] = []
        self._original: list[RemoteEntry] = []   # unsorted order from server

    def load(self, entries: list[RemoteEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._original = list(entries)
        self.endResetModel()

    def sort(self, col: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """Sort by column.  col == -1 restores the original server order."""
        self.beginResetModel()
        if col == -1:
            self._entries = list(self._original)
        else:
            # Pin ".." to the top; sort everything else
            up   = [e for e in self._entries if e.name == ".."]
            rest = [e for e in self._entries if e.name != ".."]

            reverse = (order == Qt.SortOrder.DescendingOrder)
            if col == 0:    # Name — dirs before files within each sort direction
                rest.sort(key=lambda e: (not e.is_dir, e.name.lower()), reverse=reverse)
            elif col == 1:  # Size — dirs have no size, treat as 0
                rest.sort(key=lambda e: (not e.is_dir, e.size), reverse=reverse)
            elif col == 2:  # Modified
                rest.sort(key=lambda e: (not e.is_dir, e.mtime), reverse=reverse)

            self._entries = up + rest
        self.endResetModel()

    def entry(self, row: int) -> RemoteEntry:
        return self._entries[row]

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            col = index.column()
            if col == 0:
                if entry.name == "..":
                    return "↑  .."
                if entry.is_symlink:
                    icon = "🔗 "
                elif entry.is_dir:
                    icon = "📁 "
                else:
                    icon = _file_icon(entry.name) + " "
                return icon + entry.name
            if col == 1:
                return "" if entry.is_dir else _human_size(entry.size)
            if col == 2:
                if entry.name == ".." or entry.mtime == 0:
                    return ""
                return datetime.datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
        return None


# ── Panel ──────────────────────────────────────────────────────────────────────

class _ListdirSignals(QObject):
    """Bridge for delivering listdir results back to the main thread."""
    done     = Signal(str, list, int)   # (path, entries, gen)
    error    = Signal(str, str, int)    # (path, error_message, gen)
    progress = Signal(str, int, int)    # (path, count_so_far, gen)


class _BreadcrumbBar(QWidget):
    """Clickable path segments with an inline edit mode (Cmd+G)."""
    navigate_to = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path = "/"
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Crumb display — wrapped in a horizontal scroll area so deep paths
        # don't overflow the bar; scrollbars are hidden and the view auto-scrolls
        # to the rightmost (current) segment after every rebuild.
        self._crumb_widget = QWidget()
        self._crumb_layout = QHBoxLayout(self._crumb_widget)
        self._crumb_layout.setContentsMargins(0, 0, 0, 0)
        self._crumb_layout.setSpacing(0)
        self._crumb_layout.addStretch()

        self._crumb_scroll = QScrollArea()
        self._crumb_scroll.setWidget(self._crumb_widget)
        self._crumb_scroll.setWidgetResizable(True)
        self._crumb_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._crumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._crumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Clip height to crumb widget so the scroll area doesn't add extra space
        self._crumb_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._crumb_scroll.setFixedHeight(28)

        # Inline path editor (hidden by default)
        self._editor = QLineEdit()
        self._editor.setObjectName("path-editor")
        self._editor.setVisible(False)
        self._editor.returnPressed.connect(self._on_confirm)
        self._editor.installEventFilter(self)

        outer.addWidget(self._crumb_scroll, stretch=1)
        outer.addWidget(self._editor, stretch=1)

    def set_path(self, path: str) -> None:
        self._path = path
        self._rebuild(path)
        if not self._editor.isHidden():
            self._editor.setText(path)

    def focus_editor(self) -> None:
        """Switch to edit mode."""
        self._editor.setText(self._path)
        self._crumb_scroll.setVisible(False)
        self._editor.setVisible(True)
        self._editor.selectAll()
        self._editor.setFocus()

    def _on_confirm(self) -> None:
        path = self._editor.text().strip()
        self._editor.setVisible(False)
        self._crumb_scroll.setVisible(True)
        if path:
            self.navigate_to.emit(path)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._editor.setVisible(False)
                self._crumb_scroll.setVisible(True)
                return True
        return super().eventFilter(obj, event)

    def _rebuild(self, path: str) -> None:
        # Clear all except the trailing stretch
        while self._crumb_layout.count() > 1:
            item = self._crumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        parts = PurePosixPath(path).parts  # e.g. ('/', 'home', 'user')
        acc = ""
        n = len(parts)
        for i, part in enumerate(parts):
            if part == "/":
                acc = "/"
                display = "/"
            else:
                acc = str(PurePosixPath(acc) / part)
                display = part

            is_last = (i == n - 1)
            if is_last:
                lbl = QLabel(display)
                lbl.setObjectName("path-label")
                self._crumb_layout.insertWidget(self._crumb_layout.count() - 1, lbl)
            else:
                target = acc
                btn = QPushButton(display)
                btn.setFlat(True)
                btn.setObjectName("breadcrumb-btn")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, p=target: self.navigate_to.emit(p))
                self._crumb_layout.insertWidget(self._crumb_layout.count() - 1, btn)

                # Don't add a separator after the root "/" button — it already
                # reads as a path delimiter and adding " / " produces "/ / home".
                if part != "/":
                    sep = QLabel(" / ")
                    sep.setStyleSheet("color: #45475a; padding: 0;")
                    self._crumb_layout.insertWidget(self._crumb_layout.count() - 1, sep)

        # Scroll to the far right so the current (deepest) segment is always visible
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, lambda: self._crumb_scroll.horizontalScrollBar().setValue(
            self._crumb_scroll.horizontalScrollBar().maximum()
        ))


class _EmptyStateOverlay(QWidget):
    """Shown over the table when no SFTP connection is active."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("empty-state-overlay")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("⇄")
        icon.setObjectName("empty-state-icon")
        icon.setStyleSheet("font-size: 40px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("No connection")
        title.setObjectName("empty-state-title")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Select a connection above and click Connect")
        hint.setObjectName("empty-state-hint")
        hint.setStyleSheet("font-size: 12px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(icon)
        layout.addSpacing(10)
        layout.addWidget(title)
        layout.addSpacing(4)
        layout.addWidget(hint)
        layout.addStretch()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().color(self.backgroundRole()))


class RemotePanel(QWidget):
    path_changed = Signal(str)
    upload_requested = Signal(list, str)       # (local_paths, remote_dir)
    download_requested = Signal(list)          # list[RemoteEntry]
    status_message = Signal(str)
    column_widths_changed = Signal(list)   # [w0, w1, w2]

    def __init__(self, sftp: Optional[SFTPClient] = None, parent=None) -> None:
        super().__init__(parent)
        self._sftp = sftp
        self._cwd = "/"
        self._model = RemoteModel()
        self._sort_col: int = -1                          # -1 = neutral
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._show_hidden: bool = False                   # dotfiles visible?
        self._filter_text: str = ""                       # inline filename filter
        self._all_entries: list[RemoteEntry] = []         # unfiltered server listing
        self._edit_watchers: list = []
        self._nav_gen: int = 0                            # incremented on each navigate() call
        self._nav_signals = _ListdirSignals()
        self._nav_signals.done.connect(self._on_listdir_done)
        self._nav_signals.error.connect(self._on_listdir_error)
        self._nav_signals.progress.connect(self._on_listdir_progress)
        self._build_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Remote")
        header.setObjectName("section-header")
        header_row.addWidget(header, stretch=1)

        self._hidden_btn = QPushButton(".*")
        self._hidden_btn.setCheckable(True)
        self._hidden_btn.setChecked(False)
        self._hidden_btn.setToolTip("Show hidden files (Cmd+Shift+.)")
        self._hidden_btn.setFixedWidth(32)
        self._hidden_btn.setObjectName("hidden-toggle")
        self._hidden_btn.toggled.connect(self._on_hidden_toggled)
        header_row.addWidget(self._hidden_btn)

        layout.addLayout(header_row)

        self._breadcrumb = _BreadcrumbBar(self)
        self._breadcrumb.navigate_to.connect(self.navigate)
        layout.addWidget(self._breadcrumb)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter files…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setObjectName("filter-edit")
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter_edit)

        self._table = _DropTable(self)
        self._table.setModel(self._model)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(60)
        hdr.resizeSection(0, 260)
        hdr.resizeSection(1, 80)
        hdr.resizeSection(2, 140)
        hdr.sectionResized.connect(self._on_column_resized)
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # none active
        hdr.sectionClicked.connect(self._on_header_click)
        self._table.setSortingEnabled(False)   # we handle cycling manually
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        # Skeleton overlay — sits on top of the table viewport while listing is in-flight
        self._skeleton = SkeletonWidget(self._table.viewport())
        self._skeleton.hide()
        self._table.viewport().installEventFilter(self)

        self._empty_state = _EmptyStateOverlay(self._table.viewport())
        self._empty_state.setGeometry(self._table.viewport().rect())
        self._empty_state.show()
        self._empty_state.raise_()

    # ── Skeleton overlay ───────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        """Keep skeleton overlay sized to the table viewport."""
        if obj is self._table.viewport() and event.type() == QEvent.Type.Resize:
            self._skeleton.setGeometry(self._table.viewport().rect())
            self._empty_state.setGeometry(self._table.viewport().rect())
        return super().eventFilter(obj, event)

    # ── Sorting ────────────────────────────────────────────────────────────────

    def _on_header_click(self, col: int) -> None:
        """Cycle sort for the clicked column: neutral → asc → desc → neutral."""
        hdr = self._table.horizontalHeader()
        if self._sort_col != col:
            # New column — start ascending
            self._sort_col   = col
            self._sort_order = Qt.SortOrder.AscendingOrder
        elif self._sort_order == Qt.SortOrder.AscendingOrder:
            self._sort_order = Qt.SortOrder.DescendingOrder
        else:
            # Third click — back to neutral (server order)
            self._sort_col = -1

        if self._sort_col == -1:
            hdr.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            self._model.sort(-1)
        else:
            hdr.setSortIndicator(self._sort_col, self._sort_order)
            self._model.sort(self._sort_col, self._sort_order)

    # ── Hidden files toggle ────────────────────────────────────────────────────

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self._apply_entries()

    def _on_hidden_toggled(self, checked: bool) -> None:
        self._show_hidden = checked
        self._apply_entries()   # instant — no SFTP call needed

    def toggle_hidden(self) -> None:
        """Called from Cmd+Shift+. shortcut."""
        self._hidden_btn.setChecked(not self._hidden_btn.isChecked())

    def _on_column_resized(self, logical: int, old_size: int, new_size: int) -> None:
        hdr = self._table.horizontalHeader()
        widths = [hdr.sectionSize(i) for i in range(self._model.columnCount())]
        self.column_widths_changed.emit(widths)

    def set_column_widths(self, widths: list[int]) -> None:
        """Restore saved column widths for all three columns."""
        hdr = self._table.horizontalHeader()
        for i, w in enumerate(widths[:3]):
            hdr.resizeSection(i, w)

    def focus_path_input(self) -> None:
        """Enter path-edit mode (Cmd+G)."""
        self._breadcrumb.focus_editor()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_sftp(self, sftp: SFTPClient) -> None:
        self._sftp = sftp
        self._empty_state.hide()

    def set_disconnected(self) -> None:
        """Called on disconnect — clear the model and show the empty state."""
        self._sftp = None
        self._cwd = "/"
        self._breadcrumb.set_path("/")
        self._model.load([])
        self._empty_state.setGeometry(self._table.viewport().rect())
        self._empty_state.show()
        self._empty_state.raise_()

    def navigate_or_root(self, path: str) -> None:
        """Navigate to path, falling back to '/' on any SFTP error.

        Used on initial connect to restore the last remote directory.
        If the path no longer exists (deleted, chroot changed, different
        server layout), the user lands at root without an error dialog.
        """
        if path and path != "/":
            try:
                self.navigate(path)
                return
            except Exception:
                pass
        self.navigate("/")

    def current_path(self) -> str:
        return self._cwd

    def navigate(self, path: str) -> None:
        """Start a non-blocking streaming directory listing.

        Uses listdir_stream() which pipelines paramiko readdir requests for
        lower latency on slow connections.  A generation counter (_nav_gen)
        ensures that results from a superseded navigate() call are silently
        discarded so rapid navigation never shows stale data.
        """
        if not self._sftp:
            return
        self._nav_gen += 1
        gen = self._nav_gen

        # Clear the filename filter on every navigation so the new directory
        # is not silently pre-filtered by a stale search term.
        if self._filter_text:
            self._filter_edit.clear()

        self._breadcrumb.set_path(path)
        self._skeleton.setGeometry(self._table.viewport().rect())
        self._skeleton.show()
        self._skeleton.raise_()

        def _run() -> None:
            all_entries: list = []
            try:
                def _on_batch(batch: list, is_final: bool) -> None:
                    all_entries.extend(batch)
                    if is_final:
                        self._nav_signals.done.emit(path, all_entries, gen)
                    else:
                        self._nav_signals.progress.emit(path, len(all_entries), gen)

                self._sftp.listdir_stream(path, _on_batch)
            except Exception as exc:
                self._nav_signals.error.emit(path, str(exc), gen)

        threading.Thread(target=_run, daemon=True).start()

    def refresh(self) -> None:
        self.navigate(self._cwd)

    def _on_listdir_done(self, path: str, entries: list, gen: int) -> None:
        if gen != self._nav_gen:
            return  # superseded by a newer navigate() call — discard stale result
        self._skeleton.hide()
        self._cwd = path
        self._breadcrumb.set_path(path)

        # Reset sort state so a fresh directory is not misleadingly sorted.
        # The sort indicator from the previous directory would persist otherwise.
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        # Prepend ".." when not at root so the user can navigate up
        if path != "/":
            parent = str(PurePosixPath(path).parent)
            up_entry = RemoteEntry(name="..", path=parent, is_dir=True, size=0, mtime=0)
            entries = [up_entry] + entries

        # Store the full unfiltered listing so toggling hidden files is instant
        self._all_entries = entries
        self._apply_entries()
        self.path_changed.emit(path)
        # Emit directory item count to status bar
        n_dirs  = sum(1 for e in self._all_entries if e.is_dir and e.name != "..")
        n_files = sum(1 for e in self._all_entries if not e.is_dir)
        self.status_message.emit(f"{n_dirs} folder{'s' if n_dirs != 1 else ''}, {n_files} file{'s' if n_files != 1 else ''}")

    def _apply_entries(self) -> None:
        """Load (filtered) entries into the model, then re-apply the current sort."""
        if self._show_hidden:
            visible = self._all_entries
        else:
            visible = [e for e in self._all_entries
                       if e.name == ".." or not e.name.startswith(".")]
        if self._filter_text:
            visible = [e for e in visible
                       if e.name == ".." or self._filter_text in e.name.lower()]
        self._model.load(visible)
        if self._sort_col != -1:
            self._model.sort(self._sort_col, self._sort_order)

    def _on_listdir_error(self, path: str, msg: str, gen: int) -> None:
        if gen != self._nav_gen:
            return  # superseded by a newer navigate() call — discard stale error
        self._skeleton.hide()
        # Restore the breadcrumb to the last valid path; the attempted path
        # may not exist so don't update _cwd.
        self._breadcrumb.set_path(self._cwd)
        self.status_message.emit(f"Error listing {path}: {msg}")

    def _on_listdir_progress(self, path: str, count: int, gen: int) -> None:
        """Update status bar with streaming progress; ignores stale generations."""
        if gen != self._nav_gen:
            return
        self.status_message.emit(f"Loading… {count} items")

    def _on_selection_changed(self) -> None:
        """Update status bar when the selection changes in the remote table."""
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        # Exclude the ".." navigation entry from the count
        selected = [self._model.entry(r) for r in rows if self._model.entry(r).name != ".."]
        n = len(selected)
        if n == 0:
            # Restore the directory summary when nothing is selected
            n_dirs  = sum(1 for e in self._all_entries if e.is_dir and e.name != "..")
            n_files = sum(1 for e in self._all_entries if not e.is_dir)
            self.status_message.emit(
                f"{n_dirs} folder{'s' if n_dirs != 1 else ''}, "
                f"{n_files} file{'s' if n_files != 1 else ''}"
            )
        elif n == 1:
            entry = selected[0]
            if entry.is_dir:
                self.status_message.emit("1 folder selected")
            else:
                self.status_message.emit(
                    f"1 file selected  ({_human_size(entry.size)})"
                )
        else:
            n_dirs  = sum(1 for e in selected if e.is_dir)
            n_files = sum(1 for e in selected if not e.is_dir)
            parts = []
            if n_dirs:
                parts.append(f"{n_dirs} folder{'s' if n_dirs != 1 else ''}")
            if n_files:
                parts.append(f"{n_files} file{'s' if n_files != 1 else ''}")
            self.status_message.emit(f"{n} items selected  ({', '.join(parts)})")

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _on_double_click(self, index: QModelIndex) -> None:
        entry = self._model.entry(index.row())
        if entry.is_dir:
            self.navigate(entry.path)
        else:
            self.download_requested.emit([entry])

    def _selected_entries(self) -> list[RemoteEntry]:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        return [self._model.entry(r) for r in sorted(rows)]

    def _entry_at(self, pos) -> Optional[RemoteEntry]:
        idx = self._table.indexAt(pos)
        if idx.isValid():
            return self._model.entry(idx.row())
        return None

    # ── Context menu ───────────────────────────────────────────────────────────

    def _on_context_menu(self, pos) -> None:
        entry = self._entry_at(pos)
        selected = self._selected_entries()
        if entry and entry not in selected:
            selected = [entry]
        # Exclude the ".." navigation entry from all actions
        selected = [e for e in selected if e.name != ".."]

        menu = QMenu(self)

        if selected:
            dl_act = menu.addAction("↓  Download")
            menu.addSeparator()
            rename_act = menu.addAction("✏  Rename") if len(selected) == 1 else None
            del_act = menu.addAction("🗑  Delete")
            menu.addSeparator()
            copy_path_act = menu.addAction("📋  Copy Path") if len(selected) == 1 else None
            info_act = menu.addAction("ℹ  Info") if len(selected) == 1 else None
            perm_act = menu.addAction("🔒  Permissions…") if len(selected) == 1 else None
            dup_act = menu.addAction("⧉  Duplicate") if len(selected) == 1 and not selected[0].is_dir else None
            edit_remote_act = menu.addAction("✎  Edit") if len(selected) == 1 and not selected[0].is_dir else None
            menu.addSeparator()
        else:
            dl_act = rename_act = del_act = copy_path_act = info_act = perm_act = dup_act = edit_remote_act = None

        new_folder_act = menu.addAction("📁  New Folder")
        new_file_act = menu.addAction("📄  New File")

        # Paste from clipboard
        clipboard = QApplication.clipboard()
        clip_urls = clipboard.mimeData().urls() if clipboard.mimeData().hasUrls() else []
        local_files = [u.toLocalFile() for u in clip_urls if u.isLocalFile()]
        paste_act = menu.addAction(f"📋  Upload from Clipboard ({len(local_files)} file(s))") if local_files else None
        if paste_act:
            paste_act.setEnabled(bool(self._sftp))

        if not self._sftp:
            for act in (dl_act, rename_act, del_act, new_folder_act, new_file_act):
                if act:
                    act.setEnabled(False)

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action is None:
            return

        if action == dl_act and selected:
            self.download_requested.emit(selected)
        elif action == rename_act and selected:
            self._do_rename(selected[0])
        elif action == del_act and selected:
            self._do_delete(selected)
        elif action == copy_path_act and selected:
            QApplication.clipboard().setText(selected[0].path)
            self.status_message.emit(f"Copied: {selected[0].path}")
        elif action == new_folder_act:
            self._do_new_folder()
        elif action == new_file_act:
            self._do_new_file()
        elif action == info_act and selected:
            self._show_info(selected[0])
        elif action == perm_act and selected:
            self._do_permissions(selected[0])
        elif action == dup_act and selected:
            self._do_duplicate(selected[0])
        elif action == edit_remote_act and selected:
            self._do_edit_remote(selected[0])
        elif action == paste_act and local_files:
            self.upload_requested.emit(local_files, self._cwd)

    # ── Mutating operations (run in thread) ────────────────────────────────────

    def _do_permissions(self, entry: RemoteEntry) -> None:
        """Open the chmod editor for *entry* and apply the selected mode on Accept."""
        # If the entry already carries st_mode (from listdir), use it directly.
        # Otherwise fall back to a live stat() call so we show accurate bits.
        initial_mode = entry.st_mode
        if initial_mode == 0 and self._sftp:
            try:
                attrs = self._sftp.stat(entry.path)
                initial_mode = int(attrs.st_mode or 0)
            except Exception:
                pass  # unknown — dialog starts at 0o000

        dlg = PermissionsDialog(
            path=entry.path,
            name=entry.name,
            initial_mode=initial_mode,
            parent=self,
        )
        if dlg.exec() != _QDialog.DialogCode.Accepted:
            return

        new_mode = dlg.current_mode()
        old_perm = initial_mode & 0o7777
        if new_mode == old_perm:
            self.status_message.emit("Permissions unchanged.")
            return

        self.status_message.emit(
            f"chmod {new_mode:04o} {entry.name}…"
        )

        def _run() -> None:
            try:
                self._sftp.chmod(entry.path, new_mode)
                self.status_message.emit(
                    f"Permissions set to {new_mode:04o} on {entry.name}"
                )
            except Exception as exc:
                self.status_message.emit(f"chmod failed: {exc}")
            finally:
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_rename(self, entry: RemoteEntry) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename", f"Rename '{entry.name}' to:",
            text=entry.name,
        )
        if not ok or not new_name.strip() or new_name.strip() == entry.name:
            return
        if "/" in new_name.strip():
            QMessageBox.warning(self, "Invalid name", "Name must not contain '/'.")
            return
        new_path = str(PurePosixPath(self._cwd) / new_name.strip())

        self.status_message.emit(f"Renaming {entry.name} → {new_name}…")

        def _run():
            try:
                self._sftp.rename(entry.path, new_path)
                self.status_message.emit(f"Renamed {entry.name} → {new_name}")
            except Exception as exc:
                self.status_message.emit(f"Rename failed: {exc}")
            finally:
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_delete(self, entries: list[RemoteEntry]) -> None:
        names = ", ".join(e.name for e in entries[:3])
        if len(entries) > 3:
            names += f" + {len(entries) - 3} more"
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete {len(entries)} item(s)?\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ── Phase 0: immediate pre-thread feedback ─────────────────────────────
        label = entries[0].name if len(entries) == 1 else f"{len(entries)} items"
        self.status_message.emit(f"Scanning {label} for deletion…")

        def _run() -> None:
            t_start = time.monotonic()

            # ── Phase 1: scan everything first ─────────────────────────────────
            # Collect (files_to_remove, dirs_to_remove) for all selected entries.
            # dirs_to_remove is depth-first post-order so deepest dirs come first.
            files_to_remove: list[RemoteEntry] = []
            dirs_to_remove: list[str] = []       # deepest → shallowest

            # Iterative DFS — avoids Python recursion limit on deep trees
            _stack: list[str] = []
            _visited_dirs: list[str] = []
            dirs_scanned = 0

            for e in entries:
                if e.is_dir:
                    _stack.append(e.path)
                else:
                    files_to_remove.append(e)

            while _stack:
                dir_path = _stack.pop()
                _visited_dirs.append(dir_path)
                dirs_scanned += 1
                if dirs_scanned % 5 == 0:
                    self.status_message.emit(
                        f"Scanning {label}… {len(files_to_remove):,} files found so far…"
                    )
                try:
                    children = self._sftp.listdir(dir_path)
                except Exception:
                    continue
                for child in children:
                    if child.is_dir:
                        _stack.append(child.path)
                    else:
                        files_to_remove.append(child)

            # Reverse so deepest dirs come first (correct rmdir order)
            dirs_to_remove.extend(reversed(_visited_dirs))

            total_files = len(files_to_remove)
            total_bytes = sum(f.size for f in files_to_remove)
            total_mb    = total_bytes / (1024 * 1024)

            if total_files:
                self.status_message.emit(
                    f"Found {total_files:,} file{'s' if total_files != 1 else ''} "
                    f"({total_mb:.1f} MB) in {label} — deleting…"
                )
            else:
                self.status_message.emit(f"Removing {label}…")

            # ── Phase 2: delete files one-by-one with live progress ────────────
            errors: list[str] = []
            bytes_freed = 0
            t_last_emit = time.monotonic()

            for i, f in enumerate(files_to_remove, 1):
                try:
                    self._sftp.remove(f.path)
                    bytes_freed += f.size
                except Exception as exc:
                    errors.append(f.name)

                # Emit every 10 files or every 0.25 s — whichever comes first
                if i % 10 == 0 or (time.monotonic() - t_last_emit) >= 0.25:
                    t_last_emit = time.monotonic()
                    elapsed     = max(time.monotonic() - t_start, 0.001)
                    rate        = i / elapsed                # files/sec
                    mb_freed    = bytes_freed / (1024 * 1024)
                    remaining   = (total_files - i) / rate if rate > 0 else 0
                    eta         = f"~{remaining:.0f}s left" if remaining >= 2 else "almost done"
                    self.status_message.emit(
                        f"Removing… {i:,} / {total_files:,} files · "
                        f"{mb_freed:.1f} / {total_mb:.1f} MB freed · "
                        f"{rate:.0f} files/s · {eta}…"
                    )

            # ── Phase 3: remove now-empty directories (deepest → shallowest) ───
            if dirs_to_remove:
                self.status_message.emit(
                    f"Cleaning up {len(dirs_to_remove)} director{'ies' if len(dirs_to_remove) != 1 else 'y'}…"
                )
            for d in dirs_to_remove:
                try:
                    self._sftp.rmdir(d)
                except Exception as exc:
                    errors.append(str(PurePosixPath(d).name))

            # ── Phase 4: final summary ─────────────────────────────────────────
            elapsed_total = time.monotonic() - t_start
            mb_freed      = bytes_freed / (1024 * 1024)

            if errors:
                self.status_message.emit(
                    f"Removed {label} with {len(errors)} error(s): "
                    f"{', '.join(errors[:3])}"
                    + (" …" if len(errors) > 3 else "")
                )
            elif total_files:
                self.status_message.emit(
                    f"Removed {label} — {total_files:,} files, "
                    f"{mb_freed:.1f} MB freed in {elapsed_total:.1f}s"
                )
            else:
                self.status_message.emit(f"Removed {label}")

            self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        if "/" in name.strip():
            QMessageBox.warning(self, "Invalid name", "Folder name must not contain '/'.")
            return
        path = str(PurePosixPath(self._cwd) / name.strip())

        self.status_message.emit(f"Creating folder {name}…")

        def _run():
            try:
                self._sftp.mkdir(path)
                self.status_message.emit(f"Created {name}/")
            except Exception as exc:
                self.status_message.emit(f"Failed to create folder: {exc}")
            finally:
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_new_file(self) -> None:
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if not ok or not name.strip():
            return
        if "/" in name.strip():
            QMessageBox.warning(self, "Invalid name", "File name must not contain '/'.")
            return
        path = str(PurePosixPath(self._cwd) / name.strip())

        self.status_message.emit(f"Creating {name}…")

        def _run():
            try:
                self._sftp.create_file(path)
                self.status_message.emit(f"Created {name}")
            except Exception as exc:
                self.status_message.emit(f"Failed to create file: {exc}")
            finally:
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_duplicate(self, entry: RemoteEntry) -> None:
        """Create a copy of a remote file in the same directory.

        Tries "name copy.ext", then "name copy 2.ext", … up to copy 99
        to avoid silently overwriting an existing file.
        """
        name = entry.name
        self.status_message.emit(f"Duplicating {name}…")

        def _run() -> None:
            # Find a non-colliding name
            new_name = new_path = ""
            for n in range(1, 100):
                candidate = _duplicate_name(name, n)
                candidate_path = str(PurePosixPath(self._cwd) / candidate)
                try:
                    self._sftp.stat(candidate_path)
                    # stat succeeded → file exists, try next n
                except Exception:
                    new_name, new_path = candidate, candidate_path
                    break
            else:
                self.status_message.emit(f"Duplicate failed: too many copies of {name}")
                return

            try:
                with self._sftp.open_remote(entry.path, "rb") as src:
                    with self._sftp.open_remote(new_path, "wb") as dst:
                        while True:
                            chunk = src.read(256 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                self.status_message.emit(f"Duplicated → {new_name}")
            except Exception as exc:
                self.status_message.emit(f"Duplicate failed: {exc}")
            finally:
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()

    def _do_edit_remote(self, entry: RemoteEntry) -> None:
        """Download to a temp file, open in the default editor, and watch for saves."""
        import os as _os
        import subprocess
        import tempfile
        from PySide6.QtCore import QFileSystemWatcher

        self.status_message.emit(f"Opening {entry.name}…")

        tmp_dir = tempfile.mkdtemp(prefix="sftp-ui-edit-")
        tmp_path = _os.path.join(tmp_dir, entry.name)

        def _download():
            try:
                with self._sftp.open_remote(entry.path, "rb") as src:
                    with open(tmp_path, "wb") as dst:
                        while True:
                            chunk = src.read(256 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
            except Exception as exc:
                self.status_message.emit(f"Download for edit failed: {exc}")
                return

            subprocess.Popen(["open", tmp_path])
            self.status_message.emit(f"Editing {entry.name} — save the file to upload changes")

            watcher = QFileSystemWatcher([tmp_path])
            last_mtime = [_os.path.getmtime(tmp_path)]

            def _on_changed(_path: str) -> None:
                try:
                    mtime = _os.path.getmtime(_path)
                    if mtime == last_mtime[0]:
                        return
                    last_mtime[0] = mtime
                except OSError:
                    return
                self.status_message.emit(f"Uploading {entry.name}…")

                def _upload() -> None:
                    try:
                        with open(_path, "rb") as src:
                            with self._sftp.open_remote(entry.path, "wb") as dst:
                                while True:
                                    chunk = src.read(256 * 1024)
                                    if not chunk:
                                        break
                                    dst.write(chunk)
                        self.status_message.emit(f"Saved {entry.name}")
                    except Exception as exc:
                        self.status_message.emit(f"Save failed: {exc}")

                threading.Thread(target=_upload, daemon=True).start()

            watcher.fileChanged.connect(_on_changed)
            self._edit_watchers.append(watcher)

        threading.Thread(target=_download, daemon=True).start()

    def _show_info(self, entry: RemoteEntry) -> None:
        dt = datetime.datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M:%S")
        info = (
            f"<b>Name:</b> {entry.name}<br>"
            f"<b>Path:</b> {entry.path}<br>"
            f"<b>Type:</b> {'Directory' if entry.is_dir else 'File'}<br>"
            f"<b>Size:</b> {_human_size(entry.size) if not entry.is_dir else '—'}<br>"
            f"<b>Modified:</b> {dt}"
        )
        msg = QMessageBox(self)
        msg.setWindowTitle("File Info")
        msg.setText(info)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    # ── Drop handling ──────────────────────────────────────────────────────────

    def _on_drop(self, local_paths: list[str], target_dir: Optional[str] = None) -> None:
        dest = target_dir or self._cwd
        n = len(local_paths)
        self.status_message.emit(f"Preparing upload of {n} item(s) to {dest}…")
        self.upload_requested.emit(local_paths, dest)


# ── Drop overlay ───────────────────────────────────────────────────────────────

class _DropOverlay(QWidget):
    """Semi-transparent blue highlight shown over the table while a drag hovers."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Tinted fill
        p.fillRect(self.rect(), QColor(137, 180, 250, 22))

        # Border
        pen = QPen(QColor("#89b4fa"), 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))

        # Center hint label
        f = QFont(p.font())
        f.setPointSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#89b4fa"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop to upload")


# ── Drop-enabled table ─────────────────────────────────────────────────────────

class _DropTable(QTableView):
    def __init__(self, panel: RemotePanel) -> None:
        super().__init__()
        self._panel = panel
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

        self._drop_overlay = _DropOverlay(self.viewport())

    def _show_overlay(self) -> None:
        self._drop_overlay.setGeometry(self.viewport().rect())
        self._drop_overlay.show()
        self._drop_overlay.raise_()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self._show_overlay()
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            # Highlight a directory row when hovering over it so the user knows
            # files will be dropped into that subdirectory instead of _cwd.
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                entry = self._panel._model.entry(idx.row())
                if entry.is_dir and entry.name != "..":
                    self.setCurrentIndex(idx)
                else:
                    self.clearSelection()
            else:
                self.clearSelection()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_overlay.hide()
        self.clearSelection()
        super().dragLeaveEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        selected = self._panel._selected_entries()
        # Filter out ".." from actions that shouldn't touch the parent-dir entry
        actionable = [e for e in selected if e.name != ".."]

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if len(selected) == 1:
                e = selected[0]
                if e.is_dir:
                    self._panel.navigate(e.path)
                else:
                    self._panel.download_requested.emit([e])
            return

        if key in (Qt.Key.Key_Backspace,):
            # Navigate up to parent directory
            cwd = self._panel._cwd
            if cwd != "/":
                self._panel.navigate(str(PurePosixPath(cwd).parent))
            return

        if key == Qt.Key.Key_Delete and actionable:
            self._panel._do_delete(actionable)
            return

        if key == Qt.Key.Key_F2 and len(actionable) == 1:
            self._panel._do_rename(actionable[0])
            return

        super().keyPressEvent(event)

    def dropEvent(self, event) -> None:
        self._drop_overlay.hide()
        self.clearSelection()
        urls = event.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if paths:
            # If dropped onto a directory row, upload into that subdirectory.
            idx = self.indexAt(event.position().toPoint())
            target_dir = self._panel._cwd
            if idx.isValid():
                entry = self._panel._model.entry(idx.row())
                if entry.is_dir and entry.name != "..":
                    target_dir = entry.path
            self._panel._on_drop(paths, target_dir)
            event.acceptProposedAction()
