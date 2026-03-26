"""High-res frost screenshot for visual verification."""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication
import sftp_ui.animations.transitions as _t
_t.ANIMATIONS_ENABLED = False

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)

@pytest.fixture(scope="session")
def tm(qapp):
    from sftp_ui.styling.theme_manager import ThemeManager
    t = ThemeManager(app=qapp)
    t.apply("frost")
    return t

def test_hires_frost(qapp, tm):
    from sftp_ui.core.connection import Connection, ConnectionStore
    from sftp_ui.ui.main_window import MainWindow

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write("[]")
        tmp = f.name

    store = ConnectionStore(path=tmp)
    store.add(Connection(name="Production", host="prod.io", user="deploy",
                         password="x", group="Prod", favorite=True))
    store.add(Connection(name="Staging", host="staging.io", user="deploy",
                         password="x", group="Dev"))

    win = MainWindow(store=store, theme_manager=tm)
    win.resize(1600, 900)
    win.show()
    QApplication.processEvents()
    QApplication.processEvents()
    px = win.grab()
    path = SCREENSHOTS_DIR / "frost_hires.png"
    px.save(str(path))
    assert path.exists()
    win.close()
