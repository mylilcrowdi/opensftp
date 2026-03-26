"""
Tests for thread-safety of the signal bridge between worker threads and UI.

The TransferQueue callbacks fire from worker threads. The _Signals QObject
bridges these to the main thread via Qt's signal/slot mechanism. These tests
verify that UI updates happen on the correct thread and don't race.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QObject, Signal, QTimer, QCoreApplication
from PySide6.QtWidgets import QApplication

from sftp_ui.core.transfer import (
    TransferDirection, TransferEngine, TransferJob, TransferState,
)
from sftp_ui.core.queue import TransferQueue
from sftp_ui.ui.widgets.transfer_panel import TransferPanel, _JobItem
from tests.conftest import FakeSFTPClient


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_engine_factory(sftp=None):
    shared = sftp or FakeSFTPClient()
    return lambda: TransferEngine(shared)


def _make_upload_job(tmp_path, name="file.bin", size=256):
    content = os.urandom(size)
    p = tmp_path / name
    p.write_bytes(content)
    return TransferJob(
        local_path=str(p), remote_path=f"/remote/{name}",
        direction=TransferDirection.UPLOAD,
    )


def _process_events(timeout_ms=500):
    """Process pending Qt events with a timeout."""
    app = QApplication.instance()
    if app:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)


# ── Signal bridge emits from worker thread ───────────────────────────────────

class _TestSignals(QObject):
    job_done = Signal(object)
    job_progress = Signal(object, int, int)


class TestSignalBridgeThreading:
    def test_callback_fires_from_worker_thread(self, tmp_path, qapp):
        """Queue callbacks come from non-main threads."""
        callback_threads = []
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_done = lambda j: callback_threads.append(threading.current_thread())

        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        time.sleep(0.5)
        queue.stop()

        assert len(callback_threads) >= 1
        assert callback_threads[0] is not threading.main_thread()

    def test_signal_delivers_to_main_thread(self, tmp_path, qapp):
        """Qt signal connected to slot runs the slot on the main thread."""
        slot_threads = []
        signals = _TestSignals()

        def slot(job):
            slot_threads.append(threading.current_thread())

        signals.job_done.connect(slot)

        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_done = lambda j: signals.job_done.emit(j)

        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        time.sleep(0.3)
        _process_events(300)
        queue.stop()

        assert len(slot_threads) >= 1
        assert slot_threads[0] is threading.main_thread()


# ── Panel updates under concurrent progress ─────────────────────────────────

class TestPanelConcurrentUpdates:
    def test_panel_handles_rapid_progress_updates(self, qapp):
        """Simulate rapid progress updates as if from multiple workers."""
        panel = TransferPanel()
        jobs = []
        for i in range(10):
            job = TransferJob(
                local_path=f"/local/file{i}.bin",
                remote_path=f"/remote/file{i}.bin",
                direction=TransferDirection.UPLOAD,
            )
            job.total_bytes = 1000
            job.state = TransferState.RUNNING
            jobs.append(job)
            panel.add_job(job)

        # Simulate 100 rapid progress updates
        for _ in range(100):
            for job in jobs:
                job.bytes_done = min(job.bytes_done + 100, job.total_bytes)
                panel.update_progress(job, job.bytes_done, job.total_bytes)

        # Panel should not crash and should show reasonable state
        assert panel.isVisible()
        # SmoothProgressBar animates to target; check the animation end value
        assert panel._progress_bar._anim.endValue() == 100

    def test_panel_handles_interleaved_state_changes(self, qapp):
        """Jobs completing while others start should not corrupt panel state."""
        panel = TransferPanel()

        job1 = TransferJob(local_path="/a", remote_path="/b",
                          direction=TransferDirection.UPLOAD)
        job1.total_bytes = 1000
        job1.state = TransferState.RUNNING
        panel.add_job(job1)

        job2 = TransferJob(local_path="/c", remote_path="/d",
                          direction=TransferDirection.UPLOAD)
        job2.total_bytes = 2000
        job2.state = TransferState.PENDING
        panel.add_job(job2)

        # job1 completes
        job1.bytes_done = 1000
        job1.state = TransferState.DONE
        panel.job_finished(job1)

        # job2 starts
        job2.state = TransferState.RUNNING
        job2.bytes_done = 500
        panel.update_progress(job2, 500, 2000)

        # Panel should still be visible (job2 running)
        assert not panel._all_settled()

    def test_job_item_refresh_during_state_transition(self, qapp):
        """Refreshing a _JobItem during rapid state changes must not crash."""
        job = TransferJob(local_path="/a", remote_path="/b",
                         direction=TransferDirection.UPLOAD)
        job.total_bytes = 1000
        item = _JobItem(job)

        # Rapid state transitions
        for state in [
            TransferState.PENDING,
            TransferState.RUNNING,
            TransferState.FAILED,
            TransferState.PENDING,  # resume
            TransferState.RUNNING,
            TransferState.DONE,
        ]:
            job.state = state
            if state == TransferState.RUNNING:
                job.bytes_done += 200
            if state == TransferState.FAILED:
                job.error = "timeout"
            elif state == TransferState.PENDING:
                job.error = None
            item.refresh()

        assert item._status.text() == "Done"


# ── Panel state after all jobs settle ────────────────────────────────────────

class TestPanelSettlement:
    def test_panel_settles_after_mixed_outcomes(self, qapp):
        """Panel with done, failed, and cancelled jobs is settled."""
        panel = TransferPanel()

        for i, state in enumerate([TransferState.DONE, TransferState.FAILED, TransferState.CANCELLED]):
            job = TransferJob(local_path=f"/l{i}", remote_path=f"/r{i}",
                            direction=TransferDirection.UPLOAD)
            job.state = state
            job.total_bytes = 100
            job.bytes_done = 100 if state == TransferState.DONE else 50
            if state == TransferState.FAILED:
                job.error = "err"
            panel.add_job(job)

        assert panel._all_settled()

    def test_toggle_label_accurate_during_batch(self, qapp):
        """Toggle label shows correct counts as jobs complete."""
        panel = TransferPanel()

        jobs = []
        for i in range(5):
            job = TransferJob(local_path=f"/l{i}", remote_path=f"/r{i}",
                            direction=TransferDirection.UPLOAD)
            job.state = TransferState.PENDING
            job.total_bytes = 100
            panel.add_job(job)
            jobs.append(job)

        panel._update_toggle_label()
        assert "5 pending" in panel._queue_toggle.text()

        # Complete 3, fail 1, leave 1 pending
        for j in jobs[:3]:
            j.state = TransferState.DONE
        jobs[3].state = TransferState.FAILED
        jobs[3].error = "err"

        panel._update_toggle_label()
        text = panel._queue_toggle.text()
        assert "1 pending" in text
        assert "1 failed" in text


# ── Speed measurement edge cases ────────────────────────────────────────────

class TestSpeedMeasurement:
    def test_speed_zero_when_paused(self, qapp):
        """After pause, speed should drop to zero on next sample."""
        panel = TransferPanel()
        job = TransferJob(local_path="/a", remote_path="/b",
                         direction=TransferDirection.UPLOAD)
        job.total_bytes = 10000
        job.state = TransferState.RUNNING
        job.bytes_done = 5000
        panel.add_job(job)

        # Simulate initial speed measurement
        panel._speed_sample_ts = time.monotonic() - 1.0
        panel._speed_sample_bytes = 0
        panel._speed_bps = 5000.0
        panel._speed_timer.start()

        # All jobs paused (no RUNNING)
        job.state = TransferState.PAUSED
        panel._sample_speed()

        assert panel._speed_bps == 0.0

    def test_eta_disappears_when_speed_zero(self, qapp):
        panel = TransferPanel()
        job = TransferJob(local_path="/a", remote_path="/b",
                         direction=TransferDirection.UPLOAD)
        job.total_bytes = 10000
        job.state = TransferState.PAUSED
        panel._jobs = [job]

        panel._sample_speed()
        assert panel._eta_label.text() == ""
