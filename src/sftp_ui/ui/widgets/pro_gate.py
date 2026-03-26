"""
ProGate — widget that blocks access to Pro features for free users.

Shows the feature name, a short explanation, and an Upgrade button.
Emits upgrade_requested when the user clicks Upgrade.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from sftp_ui.core.license import LicenseManager, LicenseStatus


class ProGate(QWidget):
    """Inline widget that gates a Pro feature."""

    upgrade_requested = Signal()

    def __init__(self, manager: LicenseManager, feature_name: str,
                 parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.feature_name = feature_name
        self._build_ui()

    def is_blocked(self) -> bool:
        return self._manager.status() != LicenseStatus.PRO

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(f"<b>{self.feature_name}</b> is a Pro feature")
        title.setStyleSheet("font-size: 14px;")
        layout.addWidget(title)

        desc = QLabel("Upgrade to openSFTP Pro to unlock this feature.")
        desc.setStyleSheet("color: #7f849c; font-size: 12px;")
        layout.addWidget(desc)

        btn_row = QHBoxLayout()
        self._upgrade_btn = QPushButton("Upgrade to Pro")
        self._upgrade_btn.setStyleSheet(
            "QPushButton { background: #7B61FF; color: white; "
            "padding: 8px 16px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #6B51EF; }"
        )
        self._upgrade_btn.clicked.connect(self.upgrade_requested.emit)
        btn_row.addWidget(self._upgrade_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
