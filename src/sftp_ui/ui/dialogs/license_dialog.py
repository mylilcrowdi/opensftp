"""
LicenseDialog — activate/deactivate Pro license.

Shows current status, key input, activate/deactivate buttons,
and a link to purchase Pro.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from sftp_ui.core.license import LicenseManager, LicenseStatus

# TODO: Replace with actual Stripe Payment Link
_BUY_URL = "https://opensftp.app/pro"


class LicenseDialog(QDialog):
    """Dialog for entering and managing the Pro license key."""

    def __init__(self, manager: LicenseManager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("openSFTP Pro")
        self.setMinimumWidth(420)
        self._build_ui()
        self._update_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Status
        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(self._status_label)

        # Key input
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("PRO-XXXX-XXXX-XXXX")
        self._key_input.setStyleSheet("padding: 8px; font-family: monospace; font-size: 13px;")
        layout.addWidget(self._key_input)

        # Error label
        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #f38ba8; font-size: 12px;")
        layout.addWidget(self._error_label)

        # Buttons
        btn_row = QHBoxLayout()

        self._activate_btn = QPushButton("Activate")
        self._activate_btn.setStyleSheet(
            "QPushButton { background: #7B61FF; color: white; "
            "padding: 8px 16px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #6B51EF; }"
        )
        self._activate_btn.clicked.connect(self._on_activate)
        btn_row.addWidget(self._activate_btn)

        self._deactivate_btn = QPushButton("Deactivate")
        self._deactivate_btn.setStyleSheet(
            "QPushButton { padding: 8px 16px; border-radius: 4px; }"
        )
        self._deactivate_btn.clicked.connect(self._on_deactivate)
        btn_row.addWidget(self._deactivate_btn)

        btn_row.addStretch()

        self._buy_btn = QPushButton("Get Pro ($9)")
        self._buy_btn.setStyleSheet(
            "QPushButton { color: #7B61FF; padding: 8px 12px; "
            "border: 1px solid #7B61FF; border-radius: 4px; }"
            "QPushButton:hover { background: #7B61FF; color: white; }"
        )
        self._buy_btn.clicked.connect(self._on_buy)
        btn_row.addWidget(self._buy_btn)

        layout.addLayout(btn_row)

    def _update_state(self) -> None:
        is_pro = self._manager.status() == LicenseStatus.PRO
        self._error_label.setText("")

        if is_pro:
            self._status_label.setText("Status: Pro")
            self._key_input.setEnabled(False)
            self._key_input.setText("")
            self._activate_btn.setVisible(False)
            self._deactivate_btn.setVisible(True)
            self._buy_btn.setVisible(False)
        else:
            self._status_label.setText("Status: Free")
            self._key_input.setEnabled(True)
            self._activate_btn.setVisible(True)
            self._deactivate_btn.setVisible(False)
            self._buy_btn.setVisible(True)

    def _on_activate(self) -> None:
        key = self._key_input.text().strip()
        if not self._manager.validate_key(key):
            self._error_label.setText("Invalid license key format")
            return
        try:
            self._manager.activate(key, "")
            self._update_state()
        except ValueError as e:
            self._error_label.setText(str(e))

    def _on_deactivate(self) -> None:
        self._manager.deactivate()
        self._update_state()

    def _on_buy(self) -> None:
        QDesktopServices.openUrl(QUrl(_BUY_URL))
