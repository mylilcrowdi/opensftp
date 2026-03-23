"""
Tests for Transfer Pause/Resume — Feature 2.

Covers:
  - TransferQueue.pause() and unpause() API
  - is_paused() reflects state correctly
  - Paused queue does not pick up new jobs
  - Unpausing resumes job processing
  - TransferPanel exposes pause_resume_requested signal and set_paused()
"""
from __future__ import annotations

import sys
import os
import time
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.transfer import TransferJob, TransferState
from sftp_ui.core.queue import TransferQueue


# ── Queue pause/unpause ───────────────────────────────────────────────────────

def _make_blocking_engine():
    """Return a fake engine factory whose upload blocks until released."""
    release = threading.Event()
    started = threading.Event()

    class BlockingEngine:
        def upload(self, job, progress_callback=None, cancel_flag=None, **kw):
            job.state = TransferState.RUNNING
            started.set()
            release.wait(timeout=5)
            job.state = TransferState.DONE

        def upload_with_retry(self, job, **kw):
            self.upload(job, **kw)

        def download(self, job, **kw):
            self.upload(job, **kw)

        def download_with_retry(self, job, **kw):
            self.upload(job, **kw)

        @property
        def _sftp(self):
            class _Fake:
                def close(self): pass
            return _Fake()

    return BlockingEngine(), release, started


class TestQueuePauseAPI:
    def test_is_paused_false_by_default(self):
        from tests.conftest import FakeSFTPClient
        from sftp_ui.core.transfer import TransferEngine
        engine = TransferEngine(FakeSFTPClient())
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1)
        assert q.is_paused() is False

    def test_pause_sets_paused(self):
        from tests.conftest import FakeSFTPClient
        from sftp_ui.core.transfer import TransferEngine
        engine = TransferEngine(FakeSFTPClient())
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1)
        q.pause()
        assert q.is_paused() is True

    def test_unpause_clears_paused(self):
        from tests.conftest import FakeSFTPClient
        from sftp_ui.core.transfer import TransferEngine
        engine = TransferEngine(FakeSFTPClient())
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1)
        q.pause()
        q.unpause()
        assert q.is_paused() is False

    def test_pause_prevents_new_job_start(self, make_local_file):
        """Jobs enqueued while paused must remain PENDING."""
        from tests.conftest import FakeSFTPClient
        from sftp_ui.core.transfer import TransferEngine
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=128)

        q = TransferQueue(engine_factory=lambda: engine, num_workers=1)
        q.pause()
        q.start()

        path, _ = make_local_file(64)
        job = TransferJob(local_path=path, remote_path="/r/file")
        q.enqueue(job)

        time.sleep(0.15)  # give worker time to *not* pick it up
        assert job.state == TransferState.PENDING

        q.stop()

    def test_unpause_allows_job_to_complete(self, make_local_file):
        """Jobs unblocked by unpause() should reach DONE."""
        from tests.conftest import FakeSFTPClient
        from sftp_ui.core.transfer import TransferEngine
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=128)

        done_event = threading.Event()
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1)
        q.on_job_done = lambda job: done_event.set()
        q.pause()
        q.start()

        path, _ = make_local_file(64)
        job = TransferJob(local_path=path, remote_path="/r/file")
        q.enqueue(job)
        time.sleep(0.1)
        assert job.state == TransferState.PENDING   # still paused

        q.unpause()
        assert done_event.wait(timeout=3), "Job should complete after unpause"
        assert job.state == TransferState.DONE

        q.stop()


# ── TransferPanel UI ──────────────────────────────────────────────────────────

class TestTransferPanelPause:
    @pytest.fixture(autouse=True)
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication(sys.argv)

    def test_pause_resume_signal_exists(self):
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        panel = TransferPanel()
        assert hasattr(panel, "pause_resume_requested")

    def test_set_paused_changes_button_text(self):
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        panel = TransferPanel()
        panel.set_paused(True)
        assert "Resume" in panel._pause_btn.text()

    def test_set_unpaused_changes_button_text(self):
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        panel = TransferPanel()
        panel.set_paused(True)
        panel.set_paused(False)
        assert "Pause" in panel._pause_btn.text()

    def test_pause_btn_emits_signal(self):
        from sftp_ui.ui.widgets.transfer_panel import TransferPanel
        panel = TransferPanel()
        received = []
        panel.pause_resume_requested.connect(lambda: received.append(1))
        panel._pause_btn.click()
        assert received
