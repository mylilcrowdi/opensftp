"""
ShortcutsDialog — keyboard shortcut help overlay.

Triggered by F1 or Ctrl+? from the main window.  Displays all shortcuts
grouped by category in a clean, searchable modal dialog.

Visual structure:
  ┌─────────────────────────────────────────────┐
  │  ⌨  Keyboard Shortcuts                      │
  ├─────────────────────────────────────────────┤
  │  🔍 [Filter shortcuts…                    ] │
  ├─────────────────────────────────────────────┤
  │  CONNECTION                                  │
  │  Ctrl+K          Connect / Disconnect        │
  │  Ctrl+N          New connection              │
  │  Ctrl+B          Toggle bookmarks bar        │
  │                                              │
  │  NAVIGATION                                  │
  │  F5 / Ctrl+R     Refresh remote listing      │
  │  Ctrl+G          Go to path                  │
  │  …                                           │
  ├─────────────────────────────────────────────┤
  │                               [Close]        │
  └─────────────────────────────────────────────┘
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QSortFilterProxyModel, QStringListModel
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


# ---------------------------------------------------------------------------
# Shortcut data
# ---------------------------------------------------------------------------

# Each group is (title, [(keys_label, description), …])
_SHORTCUT_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Connection", [
        ("Ctrl+K",           "Connect / Disconnect"),
        ("Ctrl+N",           "New connection"),
        ("Ctrl+B",           "Toggle bookmarks bar"),
    ]),
    ("Remote Panel", [
        ("F5 / Ctrl+R",      "Refresh remote listing"),
        ("Ctrl+G",           "Go to path (focus path bar)"),
        ("Ctrl+Shift+.",     "Toggle hidden files"),
        ("Enter / Dbl-click","Open folder / download file"),
        ("Backspace",        "Navigate to parent directory"),
        ("Ctrl+A",           "Select all"),
        ("Delete",           "Delete selected file(s)"),
        ("F2",               "Rename selected file"),
        ("Ctrl+D",           "Download selected file(s)"),
        ("Ctrl+C",           "Copy path to clipboard"),
    ]),
    ("Local Panel", [
        ("Enter / Dbl-click","Open folder"),
        ("Backspace",        "Navigate to parent directory"),
        ("Ctrl+A",           "Select all"),
        ("Ctrl+U",           "Upload selected file(s)"),
        ("Delete",           "Delete selected file(s)"),
    ]),
    ("Window", [
        ("Ctrl+P",           "Command Palette"),
        ("F1 / Ctrl+?",      "Show this help overlay"),
    ]),
]


# ---------------------------------------------------------------------------
# Internal row widget
# ---------------------------------------------------------------------------

class _ShortcutRow(QWidget):
    """Single row: keys label (monospace, fixed width) + description."""

    def __init__(self, keys: str, description: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.keys = keys.lower()
        self.description = description.lower()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(16)

        keys_lbl = QLabel(keys)
        keys_lbl.setObjectName("shortcutKey")
        keys_lbl.setFixedWidth(160)
        keys_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("shortcutDesc")
        desc_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout.addWidget(keys_lbl)
        layout.addWidget(desc_lbl)

    def matches(self, query: str) -> bool:
        """Return True if the row's text matches the filter query (case-insensitive)."""
        if not query:
            return True
        q = query.lower()
        return q in self.keys or q in self.description


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------

class _SectionHeader(QLabel):
    """Category header label with a bottom border separator."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(title.upper(), parent)
        self.setObjectName("shortcutSection")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class ShortcutsDialog(QDialog):
    """Keyboard shortcut help overlay dialog.

    Args:
        parent: Parent widget (usually MainWindow).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.resize(520, 560)
        self.setModal(True)

        self._rows: list[tuple[_SectionHeader, list[_ShortcutRow]]] = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(8)

        # Title
        title = QLabel("⌨  Keyboard Shortcuts")
        title.setObjectName("shortcutTitle")
        root.addWidget(title)

        # Filter box
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter shortcuts…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self._filter_edit)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # Scroll area with shortcut groups
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 8, 0)
        self._content_layout.setSpacing(2)

        for group_title, shortcuts in _SHORTCUT_GROUPS:
            header = _SectionHeader(group_title)
            self._content_layout.addWidget(header)

            row_widgets: list[_ShortcutRow] = []
            for keys, desc in shortcuts:
                row = _ShortcutRow(keys, desc)
                self._content_layout.addWidget(row)
                row_widgets.append(row)

            self._rows.append((header, row_widgets))

            # Small spacer between sections
            spacer_widget = QWidget()
            spacer_widget.setFixedHeight(6)
            self._content_layout.addWidget(spacer_widget)

        self._content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # Close button
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        """Show/hide rows based on the filter text; hide section headers with no visible rows."""
        query = text.strip().lower()

        for header, row_widgets in self._rows:
            any_visible = False
            for row in row_widgets:
                visible = row.matches(query)
                row.setVisible(visible)
                if visible:
                    any_visible = True
            header.setVisible(any_visible)

    # ------------------------------------------------------------------
    # Accessors (for testing)
    # ------------------------------------------------------------------

    @property
    def filter_edit(self) -> QLineEdit:
        """The filter/search input widget."""
        return self._filter_edit

    def visible_row_count(self) -> int:
        """Number of shortcut rows currently visible (after filtering)."""
        return sum(
            1
            for _, row_widgets in self._rows
            for row in row_widgets
            if row.isVisible()
        )

    def section_count(self) -> int:
        """Total number of sections defined."""
        return len(self._rows)

    def total_shortcut_count(self) -> int:
        """Total number of shortcuts across all sections."""
        return sum(len(rows) for _, rows in self._rows)
