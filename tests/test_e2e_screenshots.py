"""
E2E screenshot tests — renders major views offscreen and saves PNG screenshots.

Run with:
    pytest tests/test_e2e_screenshots.py -v -s

Screenshots are saved to: screenshots/ (created automatically).

After running, review each PNG for visual QA feedback.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect
from PySide6.QtCore import Qt

# Disable all QPropertyAnimations so fade-in/fade-out effects don't leave
# widgets at opacity 0 when screenshots are captured.
import sftp_ui.animations.transitions as _transitions
_transitions.ANIMATIONS_ENABLED = False

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"


def _save(widget, name: str) -> Path:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    path = SCREENSHOTS_DIR / f"{name}.png"
    # Ensure widget is visible and properly sized before grabbing
    widget.show()
    QApplication.processEvents()
    QApplication.processEvents()
    px = widget.grab()
    px.save(str(path))
    return path


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture(scope="session")
def theme_manager(qapp):
    from sftp_ui.styling.theme_manager import ThemeManager
    tm = ThemeManager(app=qapp)
    tm.apply("dark")
    return tm


# ── Main Window ───────────────────────────────────────────────────────────────

class TestScreenshotMainWindow:
    def test_main_window_idle(self, qapp, theme_manager):
        """Main window with no connections — shows empty state."""
        from sftp_ui.core.connection import ConnectionStore
        from sftp_ui.ui.main_window import MainWindow
        import tempfile, json

        # Empty store so no connections are pre-loaded
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp = f.name

        store = ConnectionStore(path=tmp)
        win = MainWindow(store=store, theme_manager=theme_manager)
        win.resize(1200, 750)
        p = _save(win, "01_main_window_idle")
        assert p.exists() and p.stat().st_size > 1000, f"Screenshot too small: {p}"
        win.close()

    def test_main_window_light_theme(self, qapp, theme_manager):
        """Main window in light theme."""
        from sftp_ui.core.connection import ConnectionStore
        from sftp_ui.ui.main_window import MainWindow
        import tempfile

        theme_manager.apply("light")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp = f.name

        store = ConnectionStore(path=tmp)
        win = MainWindow(store=store, theme_manager=theme_manager)
        win.resize(1200, 750)
        p = _save(win, "02_main_window_light")
        assert p.exists() and p.stat().st_size > 1000
        win.close()
        theme_manager.apply("dark")  # restore

    def test_main_window_with_connections(self, qapp, theme_manager):
        """Main window with several connections listed."""
        from sftp_ui.core.connection import Connection, ConnectionStore
        from sftp_ui.ui.main_window import MainWindow
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp = f.name

        store = ConnectionStore(path=tmp)
        store.add(Connection(name="Production Server", host="prod.example.com", user="deploy",
                             password="x", group="Production", favorite=True))
        store.add(Connection(name="Staging", host="staging.example.com", user="deploy",
                             password="x", group="Staging"))
        store.add(Connection(name="Dev Box", host="192.168.1.100", user="admin",
                             password="x", group="Development"))
        store.add(Connection(name="Backup Server", host="backup.internal", user="backup",
                             password="x"))

        win = MainWindow(store=store, theme_manager=theme_manager)
        win.resize(1200, 750)
        p = _save(win, "03_main_window_connections")
        assert p.exists() and p.stat().st_size > 1000
        win.close()


# ── Connection Dialog ─────────────────────────────────────────────────────────

class TestScreenshotConnectionDialog:
    def test_new_connection_empty(self, qapp, theme_manager):
        """New connection dialog — empty form."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        p = _save(dlg, "04_connection_dialog_empty")
        assert p.exists() and p.stat().st_size > 500
        dlg.close()

    def test_connection_dialog_filled(self, qapp, theme_manager):
        """Connection dialog with all fields filled in."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        from sftp_ui.core.connection import Connection
        import tempfile, os

        with tempfile.NamedTemporaryFile(delete=False) as f:
            key_path = f.name

        try:
            conn = Connection(
                name="My Production Server",
                host="prod.example.com",
                user="ubuntu",
                port=22,
                key_path=key_path,
                group="Production",
                favorite=True,
            )
            dlg = ConnectionDialog(conn=conn)
            p = _save(dlg, "05_connection_dialog_filled")
            assert p.exists() and p.stat().st_size > 500
            dlg.close()
        finally:
            os.unlink(key_path)

    def test_connection_dialog_with_tunnel(self, qapp, theme_manager):
        """Connection dialog with SSH tunnel expanded."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        dlg._tunnel_checkbox.setChecked(True)
        QApplication.processEvents()
        p = _save(dlg, "06_connection_dialog_tunnel")
        assert p.exists() and p.stat().st_size > 500
        dlg.close()


# ── Remote Panel ──────────────────────────────────────────────────────────────

class TestScreenshotRemotePanel:
    def test_remote_panel_empty_state(self, qapp, theme_manager):
        """Remote panel disconnected — empty state overlay."""
        from sftp_ui.ui.panels.remote_panel import RemotePanel
        panel = RemotePanel()
        panel.resize(600, 500)
        p = _save(panel, "07_remote_panel_empty")
        assert p.exists() and p.stat().st_size > 500
        panel.close()

    def test_remote_panel_with_entries(self, qapp, theme_manager):
        """Remote panel populated with typical directory listing."""
        from sftp_ui.ui.panels.remote_panel import RemotePanel
        from sftp_ui.core.sftp_client import RemoteEntry

        panel = RemotePanel()
        panel.resize(600, 500)

        # Simulate a loaded directory
        panel._empty_state.hide()
        entries = [
            RemoteEntry(name="..",         path="/home",              is_dir=True,  size=0,        mtime=0),
            RemoteEntry(name="projects",   path="/home/user/projects",is_dir=True,  size=0,        mtime=1700000000),
            RemoteEntry(name=".config",    path="/home/user/.config", is_dir=True,  size=0,        mtime=1700000000),
            RemoteEntry(name="deploy.sh",  path="/home/user/deploy.sh",is_dir=False,size=4096,     mtime=1710000000),
            RemoteEntry(name="app.tar.gz", path="/home/user/app.tar.gz",is_dir=False,size=52428800,mtime=1710000000),
            RemoteEntry(name="README.md",  path="/home/user/README.md",is_dir=False,size=2048,     mtime=1705000000),
            RemoteEntry(name="data.csv",   path="/home/user/data.csv",is_dir=False, size=1048576,  mtime=1708000000),
        ]
        panel._all_entries = entries
        panel._apply_entries()
        panel._breadcrumb.set_path("/home/user")
        QApplication.processEvents()

        p = _save(panel, "08_remote_panel_populated")
        assert p.exists() and p.stat().st_size > 500
        panel.close()


# ── Local Panel ───────────────────────────────────────────────────────────────

class TestScreenshotLocalPanel:
    def test_local_panel(self, qapp, theme_manager, tmp_path):
        """Local panel showing a typical directory."""
        # Create some files
        (tmp_path / "documents").mkdir()
        (tmp_path / "downloads").mkdir()
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "report.pdf").write_bytes(b"x" * 2048)
        (tmp_path / "data.csv").write_bytes(b"x" * 512)
        (tmp_path / "deploy.sh").write_bytes(b"x" * 256)

        from sftp_ui.ui.panels.local_panel import LocalPanel
        panel = LocalPanel(initial_path=str(tmp_path))
        panel.resize(400, 500)
        p = _save(panel, "09_local_panel")
        assert p.exists() and p.stat().st_size > 500
        panel.close()

    def test_local_panel_hidden_files(self, qapp, theme_manager, tmp_path):
        """Local panel with hidden files toggle active."""
        (tmp_path / ".hidden_dir").mkdir()
        (tmp_path / ".env").write_text("SECRET=xyz")
        (tmp_path / "visible.txt").write_text("hi")

        from sftp_ui.ui.panels.local_panel import LocalPanel
        panel = LocalPanel(initial_path=str(tmp_path))
        panel._hidden_btn.setChecked(True)
        QApplication.processEvents()
        panel.resize(400, 500)
        p = _save(panel, "10_local_panel_hidden")
        assert p.exists() and p.stat().st_size > 500
        panel.close()


# ── Transfer Panel ────────────────────────────────────────────────────────────

class TestScreenshotTransferPanel:
    def test_transfer_panel_active(self, qapp, theme_manager):
        """Transfer panel with an active upload and job queue."""
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        from sftp_ui.core.transfer import TransferJob, TransferDirection, TransferState

        panel = TransferPanel()
        panel.resize(900, 200)

        # Add some jobs
        j1 = TransferJob(local_path="/local/bigfile.zip", remote_path="/remote/bigfile.zip",
                         direction=TransferDirection.UPLOAD)
        j1.total_bytes = 104857600  # 100 MB
        j1.bytes_done  = 47185920   # ~45%
        j1.state = TransferState.RUNNING

        j2 = TransferJob(local_path="/local/photo.jpg", remote_path="/remote/photo.jpg",
                         direction=TransferDirection.UPLOAD)
        j2.total_bytes = 2097152
        j2.bytes_done  = 2097152
        j2.state = TransferState.DONE

        j3 = TransferJob(local_path="/local/data.csv", remote_path="/remote/data.csv",
                         direction=TransferDirection.UPLOAD)
        j3.state = TransferState.FAILED
        j3.error = "Connection reset"

        for job in (j1, j2, j3):
            panel.add_job(job)

        panel.update_progress(j1, j1.bytes_done, j1.total_bytes)
        panel._toggle_queue()  # expand queue
        QApplication.processEvents()

        p = _save(panel, "11_transfer_panel_active")
        assert p.exists() and p.stat().st_size > 500
        panel.close()

    def test_transfer_panel_paused(self, qapp, theme_manager):
        """Transfer panel in paused state."""
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        from sftp_ui.core.transfer import TransferJob, TransferDirection, TransferState

        panel = TransferPanel()
        panel.resize(900, 100)
        panel.set_paused(True)

        j1 = TransferJob(local_path="/local/video.mp4", remote_path="/remote/video.mp4",
                         direction=TransferDirection.UPLOAD)
        j1.total_bytes = 524288000
        j1.bytes_done  = 157286400
        j1.state = TransferState.RUNNING
        panel.add_job(j1)
        panel.update_progress(j1, j1.bytes_done, j1.total_bytes)
        # Flush the fade-in animation and any pending paints
        for _ in range(4):
            QApplication.processEvents()

        p = _save(panel, "12_transfer_panel_paused")
        # Paused panel is a narrow strip — 200 B is enough for a real render
        assert p.exists() and p.stat().st_size > 200
        panel.close()
