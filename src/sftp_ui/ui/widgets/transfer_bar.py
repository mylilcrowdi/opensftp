"""
TransferBar — compact progress + cancel widget.

Lives at the bottom of MainWindow. Hidden when idle, visible during transfers.
Encapsulates all transfer-related UI state so MainWindow stays clean.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from sftp_ui.animations.transitions import fade_in, fade_out, pulse_progress


class TransferBar(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pulse_anim = None
        self._build_ui()
        self.setVisible(False)

    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)

        self._label = QLabel("Uploading…")
        self._label.setStyleSheet("color: #a6adc8; font-size: 12px;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("danger")
        self._cancel_btn.setFixedWidth(70)
        self._cancel_btn.clicked.connect(self.cancel_requested)

        row.addWidget(self._label)
        row.addWidget(self._bar, stretch=1)
        row.addWidget(self._cancel_btn)

    # ── public API ────────────────────────────────────────────────────────────

    def start(self, label: str = "Uploading…") -> None:
        self._label.setText(label)
        self._bar.setValue(0)
        self.setVisible(True)
        anim = fade_in(self)
        anim.start()
        self._pulse_anim = pulse_progress(self._bar)
        self._pulse_anim.start()

    def update_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._bar.setValue(int(done * 100 / total))

    def set_label(self, text: str) -> None:
        self._label.setText(text)

    def finish(self) -> None:
        if self._pulse_anim:
            self._pulse_anim.stop()
            self._pulse_anim = None
        self._bar.setValue(100)
        anim = fade_out(self)
        anim.finished.connect(self._on_fade_done)
        anim.start()

    def _on_fade_done(self) -> None:
        self.setVisible(False)
        self._bar.setValue(0)
