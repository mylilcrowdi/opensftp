"""
Application bootstrap.

Responsible for:
- Creating the QApplication instance
- Initialising the ThemeManager and applying the default theme
- Creating and showing the MainWindow
"""
from __future__ import annotations

import sys


def main() -> None:
    from PySide6.QtWidgets import QApplication
    from sftp_ui.styling.theme_manager import ThemeManager
    from sftp_ui.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SFTP UI")
    app.setOrganizationName("async")

    theme_manager = ThemeManager(app)
    theme_manager.apply_system_theme()   # honour OS dark/light preference

    win = MainWindow(theme_manager=theme_manager)
    win.show()

    # Fade in after the window is on screen
    from sftp_ui.animations.transitions import fade_in
    anim = fade_in(win)
    anim.start()

    sys.exit(app.exec())
