"""
Frost theme screenshot tests — renders views with the Frost (glassmorphism) theme.

Run with:
    pytest tests/test_frost_screenshots.py -v -s

Screenshots saved to: screenshots/frost_*.png
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import sftp_ui.animations.transitions as _transitions
_transitions.ANIMATIONS_ENABLED = False

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"


def _save(widget, name: str) -> Path:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    path = SCREENSHOTS_DIR / f"{name}.png"
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
    tm.apply("frost")
    return tm


class TestFrostMainWindow:
    def test_frost_main_idle(self, qapp, theme_manager):
        """Frost theme: main window empty state."""
        from sftp_ui.core.connection import ConnectionStore
        from sftp_ui.ui.main_window import MainWindow

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp = f.name

        store = ConnectionStore(path=tmp)
        win = MainWindow(store=store, theme_manager=theme_manager)
        win.resize(1200, 750)
        p = _save(win, "frost_01_main_idle")
        assert p.exists() and p.stat().st_size > 1000
        win.close()

    def test_frost_main_with_connections(self, qapp, theme_manager):
        """Frost theme: main window with connections."""
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
        store.add(Connection(name="Backup", host="backup.internal", user="backup",
                             password="x"))

        win = MainWindow(store=store, theme_manager=theme_manager)
        win.resize(1200, 750)
        p = _save(win, "frost_02_main_connections")
        assert p.exists() and p.stat().st_size > 1000
        win.close()


class TestFrostDialogs:
    def test_frost_connection_dialog_empty(self, qapp, theme_manager):
        """Frost theme: new connection dialog."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        p = _save(dlg, "frost_03_connection_dialog")
        assert p.exists() and p.stat().st_size > 500
        dlg.close()

    def test_frost_connection_dialog_filled(self, qapp, theme_manager):
        """Frost theme: filled connection dialog."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        from sftp_ui.core.connection import Connection

        with tempfile.NamedTemporaryFile(delete=False) as f:
            key_path = f.name

        try:
            conn = Connection(
                name="Deep Space Server",
                host="nebula.cluster.io",
                user="astronaut",
                port=2222,
                key_path=key_path,
                group="Space Ops",
                favorite=True,
            )
            dlg = ConnectionDialog(conn=conn)
            p = _save(dlg, "frost_04_connection_filled")
            assert p.exists() and p.stat().st_size > 500
            dlg.close()
        finally:
            os.unlink(key_path)

    def test_frost_connection_dialog_tunnel(self, qapp, theme_manager):
        """Frost theme: connection dialog with tunnel expanded."""
        from sftp_ui.ui.dialogs.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog()
        dlg._name.setText("Tunnel Server")
        dlg._host.setText("target.internal")
        dlg._user.setText("admin")
        dlg._tunnel_checkbox.setChecked(True)
        dlg._tunnel_host.setText("bastion.io")
        dlg._tunnel_user.setText("jump")
        QApplication.processEvents()
        p = _save(dlg, "frost_05_connection_tunnel")
        assert p.exists() and p.stat().st_size > 500
        dlg.close()


class TestFrostPanels:
    def test_frost_local_panel(self, qapp, theme_manager, tmp_path):
        """Frost theme: local file panel."""
        (tmp_path / "documents").mkdir()
        (tmp_path / "projects").mkdir()
        (tmp_path / ".ssh").mkdir()
        (tmp_path / "deploy.sh").write_bytes(b"x" * 256)
        (tmp_path / "data.csv").write_bytes(b"x" * 4096)
        (tmp_path / "README.md").write_bytes(b"x" * 1024)

        from sftp_ui.ui.panels.local_panel import LocalPanel
        panel = LocalPanel(initial_path=str(tmp_path))
        panel.resize(400, 500)
        p = _save(panel, "frost_06_local_panel")
        assert p.exists() and p.stat().st_size > 500
        panel.close()

    def test_frost_remote_panel_populated(self, qapp, theme_manager):
        """Frost theme: remote panel with entries."""
        from sftp_ui.ui.panels.remote_panel import RemotePanel
        from sftp_ui.core.sftp_client import RemoteEntry

        panel = RemotePanel()
        panel.resize(600, 500)
        panel._empty_state.hide()

        entries = [
            RemoteEntry(name="..", path="/home", is_dir=True, size=0, mtime=0),
            RemoteEntry(name="projects", path="/home/user/projects", is_dir=True, size=0, mtime=1700000000),
            RemoteEntry(name="deploy.sh", path="/home/user/deploy.sh", is_dir=False, size=4096, mtime=1710000000),
            RemoteEntry(name="app.tar.gz", path="/home/user/app.tar.gz", is_dir=False, size=52428800, mtime=1710000000),
            RemoteEntry(name="README.md", path="/home/user/README.md", is_dir=False, size=2048, mtime=1705000000),
        ]
        panel._all_entries = entries
        panel._apply_entries()
        panel._breadcrumb.set_path("/home/user")
        QApplication.processEvents()

        p = _save(panel, "frost_07_remote_panel")
        assert p.exists() and p.stat().st_size > 500
        panel.close()

    def test_frost_transfer_panel(self, qapp, theme_manager):
        """Frost theme: transfer panel with active job."""
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        from sftp_ui.core.transfer import TransferJob, TransferDirection, TransferState

        panel = TransferPanel()
        panel.resize(900, 200)

        j1 = TransferJob(local_path="/local/archive.zip", remote_path="/remote/archive.zip",
                         direction=TransferDirection.UPLOAD)
        j1.total_bytes = 104857600
        j1.bytes_done = 47185920
        j1.state = TransferState.RUNNING

        j2 = TransferJob(local_path="/local/config.yml", remote_path="/remote/config.yml",
                         direction=TransferDirection.UPLOAD)
        j2.total_bytes = 1024
        j2.bytes_done = 1024
        j2.state = TransferState.DONE

        for job in (j1, j2):
            panel.add_job(job)
        panel.update_progress(j1, j1.bytes_done, j1.total_bytes)
        panel._toggle_queue()
        QApplication.processEvents()

        p = _save(panel, "frost_08_transfer_panel")
        assert p.exists() and p.stat().st_size > 500
        panel.close()


class TestFrostThemeDialog:
    def test_frost_theme_picker(self, qapp, theme_manager):
        """Frost theme: theme picker dialog showing all themes."""
        from sftp_ui.ui.dialogs.theme_dialog import ThemeDialog
        dlg = ThemeDialog(theme_manager)
        p = _save(dlg, "frost_09_theme_dialog")
        assert p.exists() and p.stat().st_size > 500
        dlg.close()
