"""
CommandPaletteDialog — VS Code-style command palette (Ctrl+P).

A modal popup with a filter input that fuzzy-searches all registered
commands, showing name, category, and shortcut. Enter executes the
selected command, Escape closes.

Visual structure:
  ┌─────────────────────────────────────────────┐
  │  🔍 [Type a command…                      ] │
  ├─────────────────────────────────────────────┤
  │  Navigation    Refresh Remote       Ctrl+R  │
  │  Navigation    Go to Path           Ctrl+G  │
  │  Transfer      Upload Files                 │
  │  Connection    New Connection       Ctrl+N  │
  │  UI            Change Theme                 │
  └─────────────────────────────────────────────┘
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget,
)

from sftp_ui.core.command_registry import CommandRegistry


class _CommandRow(QWidget):
    """Single row in the command palette result list."""

    def __init__(self, cmd_id: str, name: str, category: str,
                 shortcut: str = "", parent=None) -> None:
        super().__init__(parent)
        self.cmd_id = cmd_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._category_label = QLabel(category)
        self._category_label.setFixedWidth(90)
        self._category_label.setStyleSheet("color: #7f849c; font-size: 11px;")

        self._name_label = QLabel(name)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Preferred)
        self._name_label.setStyleSheet("font-size: 13px;")

        self._shortcut_label = QLabel(shortcut)
        self._shortcut_label.setStyleSheet(
            "color: #585b70; font-size: 11px; font-family: monospace;"
        )
        self._shortcut_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
        self._shortcut_label.setFixedWidth(100)

        layout.addWidget(self._category_label)
        layout.addWidget(self._name_label)
        layout.addWidget(self._shortcut_label)


class CommandPaletteDialog(QDialog):
    """Modal command palette dialog."""

    def __init__(self, registry: CommandRegistry, parent=None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._selected_cmd_id: str | None = None

        self.setWindowTitle("Command Palette")
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setMinimumWidth(500)
        self.setMaximumHeight(400)

        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Filter input
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("Type a command…")
        self._filter_input.setStyleSheet(
            "padding: 10px 12px; font-size: 14px; border: none; "
            "border-bottom: 1px solid #313244;"
        )
        self._filter_input.textChanged.connect(self._on_filter_changed)
        self._filter_input.installEventFilter(self)
        layout.addWidget(self._filter_input)

        # Result list
        self._result_list = QListWidget()
        self._result_list.setStyleSheet(
            "QListWidget { border: none; outline: none; }"
            "QListWidget::item { padding: 0; }"
            "QListWidget::item:selected { background: #313244; }"
        )
        self._result_list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self._result_list)

    def _populate(self, query: str = "") -> None:
        self._result_list.clear()
        commands = self._registry.search(query)

        for cmd in commands:
            row = _CommandRow(
                cmd_id=cmd.id,
                name=cmd.name,
                category=cmd.category,
                shortcut=cmd.shortcut or "",
            )
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, cmd.id)
            self._result_list.addItem(item)
            self._result_list.setItemWidget(item, row)

        if self._result_list.count() > 0:
            self._result_list.setCurrentRow(0)

    def _on_filter_changed(self, text: str) -> None:
        self._populate(text)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        cmd_id = item.data(Qt.ItemDataRole.UserRole)
        if cmd_id:
            self.close()
            self._registry.execute(cmd_id)

    def _execute_current(self) -> None:
        item = self._result_list.currentItem()
        if item:
            self._on_item_activated(item)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self._filter_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                row = self._result_list.currentRow()
                if row < self._result_list.count() - 1:
                    self._result_list.setCurrentRow(row + 1)
                return True
            elif key == Qt.Key.Key_Up:
                row = self._result_list.currentRow()
                if row > 0:
                    self._result_list.setCurrentRow(row - 1)
                return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._execute_current()
                return True
            elif key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)
