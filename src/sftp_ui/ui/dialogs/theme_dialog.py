"""
ThemeDialog — visual theme picker with live preview.

Shows a card grid of available themes (swatch + name + description).
Clicking a card applies the theme live so the user can preview it
instantly.  Cancel restores the previous theme.

Also offers a "Follow System" option that auto-detects the OS
dark/light preference.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QBrush, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sftp_ui.styling.theme_manager import (
    AVAILABLE_THEMES,
    THEME_LABELS,
    THEME_SWATCHES,
    ThemeManager,
)


# ── Swatch widget ─────────────────────────────────────────────────────────────

class _Swatch(QWidget):
    """Tiny colour-strip showing bg / fg / accent."""

    def __init__(self, bg: str, fg: str, accent: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bg = QColor(bg)
        self._fg = QColor(fg)
        self._accent = QColor(accent)
        self.setFixedSize(60, 32)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()

        # Background strip
        p.setBrush(QBrush(self._bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 6, 6)

        # Three vertical colour bands inside
        w = r.width() // 3
        h = r.height()
        for i, col in enumerate([self._bg, self._fg, self._accent]):
            x = i * w
            p.setBrush(QBrush(col))
            p.drawRect(x, 0, w if i < 2 else r.width() - 2 * w, h)

        # Border
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#ffffff30"), 1))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 6, 6)


# ── Theme card ────────────────────────────────────────────────────────────────

class _ThemeCard(QPushButton):
    """Clickable card representing a single theme."""

    def __init__(
        self,
        theme_key: str,
        label: str,
        bg: str,
        fg: str,
        accent: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.theme_key = theme_key
        self.setCheckable(True)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        swatch = _Swatch(bg, fg, accent, self)
        layout.addWidget(swatch)

        name_lbl = QLabel(label, self)
        name_lbl.setObjectName("theme-card-label")
        layout.addWidget(name_lbl, 1)

        self.setObjectName("theme-card")


# ── System card ───────────────────────────────────────────────────────────────

class _SystemCard(QPushButton):
    """Card for the 'Follow System' mode."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.theme_key = "system"
        self.setCheckable(True)
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        icon_lbl = QLabel("🖥", self)
        icon_lbl.setFixedWidth(20)
        layout.addWidget(icon_lbl)

        text_lbl = QLabel("Follow System  (auto dark / light)", self)
        text_lbl.setObjectName("theme-card-label")
        layout.addWidget(text_lbl, 1)

        self.setObjectName("theme-card")


# ── ThemeDialog ───────────────────────────────────────────────────────────────

class ThemeDialog(QDialog):
    """Modal theme picker dialog.

    Parameters
    ----------
    theme_manager:
        The application ThemeManager instance.  The dialog applies themes
        live for preview; cancelling restores the previous theme.
    parent:
        Optional parent widget.
    """

    def __init__(
        self,
        theme_manager: ThemeManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._mgr = theme_manager
        self._original_theme = theme_manager.current
        self._original_mode = theme_manager.mode
        self._cards: dict[str, QPushButton] = {}

        self.setWindowTitle("Choose Theme")
        self.setMinimumWidth(400)
        self.setModal(True)

        self._build_ui()
        self._select_card(self._original_mode)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Appearance", self)
        title.setObjectName("section-header")
        root.addWidget(title)

        # System card
        sys_card = _SystemCard(self)
        sys_card.clicked.connect(lambda: self._on_card_clicked("system"))
        self._cards["system"] = sys_card
        root.addWidget(sys_card)

        # Separator label
        sep_lbl = QLabel("Themes", self)
        sep_lbl.setObjectName("section-header")
        root.addWidget(sep_lbl)

        # Theme cards grid (2 columns)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, key in enumerate(AVAILABLE_THEMES):
            bg, fg, accent = THEME_SWATCHES[key]
            card = _ThemeCard(key, THEME_LABELS[key], bg, fg, accent, self)
            card.clicked.connect(lambda _checked=False, k=key: self._on_card_clicked(k))
            self._cards[key] = card
            grid.addWidget(card, i // 2, i % 2)
        root.addLayout(grid)

        # Button box
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        bbox.accepted.connect(self._on_accept)
        bbox.rejected.connect(self._on_cancel)
        root.addWidget(bbox)

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_card_clicked(self, key: str) -> None:
        self._select_card(key)
        self._mgr.set_mode(key)

    def _select_card(self, key: str) -> None:
        """Check the given card and uncheck all others."""
        for k, card in self._cards.items():
            card.setChecked(k == key)

    def _on_accept(self) -> None:
        self.accept()

    def _on_cancel(self) -> None:
        # Restore the original theme without touching persistence
        try:
            self._mgr.apply(self._original_theme)
        except ValueError:
            pass
        self.reject()

    # ── Public helpers ────────────────────────────────────────────────────────

    @property
    def selected_mode(self) -> str:
        """The mode that is currently checked in the dialog."""
        for k, card in self._cards.items():
            if card.isChecked():
                return k
        return self._original_mode
