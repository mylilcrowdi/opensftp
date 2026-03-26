"""
ThemeManager — loads and applies QSS themes at runtime.

Themes are plain .qss files stored in sftp_ui/styling/themes/.
Using importlib.resources guarantees they are found both during
development and inside a Briefcase .app bundle.

Supports:
  - Named themes: dark, light, nord, dracula, solarized_dark
  - System mode: auto-follows the OS dark/light preference
  - Live switching with a theme_changed signal
  - Persistence via QSettings (key: "ui/theme")
"""
from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, QSettings

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

# ── Theme registry ────────────────────────────────────────────────────────────

AVAILABLE_THEMES: list[str] = [
    "dark",
    "light",
    "frost",
    "nord",
    "dracula",
    "solarized_dark",
]

# Human-readable labels shown in the UI
THEME_LABELS: dict[str, str] = {
    "dark":          "Dark (Catppuccin Mocha)",
    "light":         "Light (Catppuccin Latte)",
    "frost":         "Frost (Glassmorphism)",
    "nord":          "Nord",
    "dracula":       "Dracula",
    "solarized_dark": "Solarized Dark",
}

# Accent colour shown in theme-picker swatches  (bg, fg, accent)
THEME_SWATCHES: dict[str, tuple[str, str, str]] = {
    "dark":          ("#1e1e2e", "#cdd6f4", "#89b4fa"),
    "light":         ("#eff1f5", "#4c4f69", "#1e66f5"),
    "frost":         ("#0f1123", "#d4d8f0", "#00d4ff"),
    "nord":          ("#2e3440", "#d8dee9", "#88c0d0"),
    "dracula":       ("#282a36", "#f8f8f2", "#bd93f9"),
    "solarized_dark": ("#002b36", "#839496", "#268bd2"),
}

DEFAULT_THEME = "dark"

# ── OS preference helper ──────────────────────────────────────────────────────

def _system_prefers_dark() -> bool:
    """Return True if the OS reports a dark colour-scheme preference."""
    try:
        from PySide6.QtGui import QGuiApplication, Qt
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        return scheme != Qt.ColorScheme.Light
    except Exception:
        return True


# ── ThemeManager ─────────────────────────────────────────────────────────────

class ThemeManager(QObject):
    """Manages QSS theme loading, live switching, and persistence.

    Attributes
    ----------
    theme_changed : Signal(str)
        Emitted with the new theme name whenever a theme is applied.
    """

    theme_changed = Signal(str)

    def __init__(self, app: "QApplication") -> None:
        super().__init__(app)
        self._app = app
        self._current: str = DEFAULT_THEME
        self._mode: str = DEFAULT_THEME  # named theme OR "system"

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def current(self) -> str:
        """The currently active theme name (never "system")."""
        return self._current

    @property
    def mode(self) -> str:
        """The active mode — either "system" or a theme name."""
        return self._mode

    def available(self) -> list[str]:
        """Return a copy of the list of available theme names."""
        return list(AVAILABLE_THEMES)

    def apply(self, name: str) -> None:
        """Load and apply a theme by name.

        Raises ValueError for unknown theme names.
        Does *not* change the persisted mode — use set_mode() for that.
        """
        if name not in AVAILABLE_THEMES:
            raise ValueError(
                f"Unknown theme {name!r}. Available: {AVAILABLE_THEMES}"
            )
        qss = self._load(name)
        self._app.setStyleSheet(qss)
        self._current = name
        self.theme_changed.emit(name)

    def set_mode(self, mode: str) -> str:
        """Set the active mode, apply the corresponding theme, and persist.

        Parameters
        ----------
        mode:
            Either "system" (auto-detect) or a theme name from AVAILABLE_THEMES.

        Returns
        -------
        str
            The theme name that was actually applied.
        """
        if mode != "system" and mode not in AVAILABLE_THEMES:
            raise ValueError(
                f"Unknown mode {mode!r}. Expected 'system' or one of {AVAILABLE_THEMES}"
            )
        self._mode = mode
        if mode == "system":
            theme = "dark" if _system_prefers_dark() else "light"
        else:
            theme = mode
        self.apply(theme)
        self._persist(mode)
        return theme

    def apply_system_theme(self) -> str:
        """Detect the OS colour-scheme and apply the matching theme.

        Returns the name of the theme that was applied.
        """
        return self.set_mode("system")

    def toggle(self) -> str:
        """Cycle dark ↔ light regardless of current mode."""
        next_theme = "light" if self._current == "dark" else "dark"
        self.apply(next_theme)
        self._persist(next_theme)
        self._mode = next_theme
        return next_theme

    def restore(self) -> str:
        """Load the persisted mode from QSettings and apply it.

        Falls back to DEFAULT_THEME if nothing has been saved.
        Returns the theme name that was applied.
        """
        settings = QSettings("sftp-ui", "sftp-ui")
        mode = settings.value("ui/theme", DEFAULT_THEME)
        # Guard against corrupt settings
        valid_modes = AVAILABLE_THEMES + ["system"]
        if mode not in valid_modes:
            mode = DEFAULT_THEME
        return self.set_mode(mode)

    # ── private ───────────────────────────────────────────────────────────────

    def _persist(self, mode: str) -> None:
        settings = QSettings("sftp-ui", "sftp-ui")
        settings.setValue("ui/theme", mode)

    @staticmethod
    def _load(name: str) -> str:
        return (
            files("sftp_ui.styling.themes")
            .joinpath(f"{name}.qss")
            .read_text(encoding="utf-8")
        )
