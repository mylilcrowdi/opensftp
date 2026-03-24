"""
SearchDialog — modeless dialog for recursive remote file search.

Layout:
  ┌───────────────────────────────────────────────────────┐
  │  Pattern: [____________________]  [Search] [Cancel]   │
  │  Search in: /home/user                                │
  │  ☐ Regex   ☐ Case-sensitive   Max depth: [5]          │
  ├───────────────────────────────────────────────────────┤
  │  Name           │ Path           │ Size               │
  │  config.py      │ /home/...      │ 2.1 KB             │
  │  main.py        │ /home/...      │ 4.0 KB             │
  └───────────────────────────────────────────────────────┘
  │  Found 42 matches (120 dirs scanned)                   │
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from sftp_ui.core.sftp_client import RemoteEntry, SFTPClient
from sftp_ui.core.search import RemoteSearch


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class SearchDialog(QDialog):
    """Modeless search dialog for remote file search."""

    navigate_to = Signal(str, str)  # (parent_dir, filename) — navigate remote panel

    def __init__(self, sftp: SFTPClient, remote_cwd: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote Search")
        self.setMinimumSize(600, 400)
        self.resize(700, 500)
        self.setModal(False)

        self._sftp = sftp
        self._remote_cwd = remote_cwd
        self._search: Optional[RemoteSearch] = None
        self._match_count = 0

        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Search input row ──────────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Pattern:"))

        self._pattern_input = QLineEdit()
        self._pattern_input.setPlaceholderText("*.py, *.log, data_*")
        self._pattern_input.returnPressed.connect(self._on_search)
        input_row.addWidget(self._pattern_input, stretch=1)

        self._search_btn = QPushButton("Search")
        self._search_btn.setObjectName("primary")
        self._search_btn.clicked.connect(self._on_search)
        input_row.addWidget(self._search_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        input_row.addWidget(self._cancel_btn)

        layout.addLayout(input_row)

        # ── Search location ───────────────────────────────────────────────────
        self._search_in_label = QLabel(f"Search in: {self._remote_cwd}")
        self._search_in_label.setObjectName("path-label")
        layout.addWidget(self._search_in_label)

        # ── Options row ───────────────────────────────────────────────────────
        opts_row = QHBoxLayout()

        self._regex_cb = QCheckBox("Regex")
        self._regex_cb.setToolTip("Use regex instead of glob (e.g. ^test_.*\\.py$)")
        opts_row.addWidget(self._regex_cb)

        self._case_cb = QCheckBox("Case-sensitive")
        opts_row.addWidget(self._case_cb)

        opts_row.addWidget(QLabel("Max depth:"))
        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(1, 100)
        self._depth_spin.setValue(5)
        self._depth_spin.setFixedWidth(60)
        opts_row.addWidget(self._depth_spin)

        opts_row.addStretch()
        layout.addLayout(opts_row)

        # ── Results table ─────────────────────────────────────────────────────
        self._results_table = QTableWidget()
        self._results_table.setColumnCount(3)
        self._results_table.setHorizontalHeaderLabels(["Name", "Path", "Size"])
        self._results_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._results_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results_table.setSortingEnabled(True)
        self._results_table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._results_table)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status_label = QLabel("Ready")
        self._status_label.setObjectName("path-label")
        layout.addWidget(self._status_label)

    def _connect_signals(self) -> None:
        pass  # search signals are connected per-search in _on_search

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        pattern = self._pattern_input.text().strip()
        if not pattern:
            return

        # Stop any existing search
        if self._search and self._search.is_running:
            self._search.cancel()

        # Clear results
        self._results_table.setRowCount(0)
        self._match_count = 0
        self._status_label.setText("Searching…")
        self._search_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        self._search = RemoteSearch(self._sftp)
        self._search.signals.match_found.connect(self._on_match)
        self._search.signals.search_done.connect(self._on_done)
        self._search.signals.search_error.connect(self._on_error)

        self._search.start(
            self._remote_cwd,
            pattern,
            use_regex=self._regex_cb.isChecked(),
            case_sensitive=self._case_cb.isChecked(),
            max_depth=self._depth_spin.value(),
        )

    def _on_cancel(self) -> None:
        if self._search:
            self._search.cancel()
        self._search_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _on_match(self, entry: RemoteEntry) -> None:
        self._match_count += 1
        row = self._results_table.rowCount()
        self._results_table.insertRow(row)
        self._results_table.setItem(row, 0, QTableWidgetItem(entry.name))
        self._results_table.setItem(row, 1, QTableWidgetItem(entry.path))
        self._results_table.setItem(
            row, 2, QTableWidgetItem(_human_size(entry.size) if entry.size else "—")
        )
        # Store entry for navigation
        self._results_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, entry.path)
        self._status_label.setText(f"Found {self._match_count} match(es)…")

    def _on_done(self, dirs_scanned: int) -> None:
        self._search_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText(
            f"Done — {self._match_count} match(es), {dirs_scanned} dirs scanned"
        )

    def _on_error(self, msg: str) -> None:
        self._search_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText(f"Error: {msg}")

    def _on_double_click(self, index) -> None:
        """Navigate to the clicked result's parent directory."""
        row = index.row()
        item = self._results_table.item(row, 0)
        if item is None:
            return
        full_path = item.data(Qt.ItemDataRole.UserRole)
        if full_path:
            from pathlib import PurePosixPath
            parent = str(PurePosixPath(full_path).parent)
            name = PurePosixPath(full_path).name
            self.navigate_to.emit(parent, name)
