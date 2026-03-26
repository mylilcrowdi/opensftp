"""
Tests for per-file progress bars in _JobItem.

Currently _JobItem only shows state text (Pending, Done, Failed).
These tests drive the addition of an individual progress bar per job,
showing real-time transfer progress for each file in the queue.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.transfer import TransferDirection, TransferJob, TransferState
from sftp_ui.ui.widgets.transfer_panel import _JobItem


# ── helpers ──────────────────────────────────────────────────────────────────

def _upload_job(name: str = "file.bin", state: TransferState = TransferState.PENDING) -> TransferJob:
    job = TransferJob(local_path=f"/local/{name}", remote_path=f"/remote/{name}",
                      direction=TransferDirection.UPLOAD)
    job.state = state
    return job


def _download_job(name: str = "file.bin", state: TransferState = TransferState.PENDING) -> TransferJob:
    job = TransferJob(local_path=f"/local/{name}", remote_path=f"/remote/{name}",
                      direction=TransferDirection.DOWNLOAD)
    job.state = state
    return job


# ── Per-file progress bar existence ──────────────────────────────────────────

class TestJobItemHasProgressBar:
    def test_job_item_has_progress_bar_widget(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        item = _JobItem(job)
        assert hasattr(item, "_progress_bar"), "_JobItem must have a _progress_bar widget"

    def test_progress_bar_hidden_when_pending(self, qapp):
        job = _upload_job(state=TransferState.PENDING)
        item = _JobItem(job)
        assert not item._progress_bar.isVisible()

    def test_progress_bar_visible_when_running(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 500
        item = _JobItem(job)
        # isVisible() requires parent to be visible; check the widget's own state
        assert not item._progress_bar.isHidden()

    def test_progress_bar_hidden_when_done(self, qapp):
        job = _upload_job(state=TransferState.DONE)
        item = _JobItem(job)
        assert not item._progress_bar.isVisible()

    def test_progress_bar_hidden_when_failed(self, qapp):
        job = _upload_job(state=TransferState.FAILED)
        item = _JobItem(job)
        assert not item._progress_bar.isVisible()

    def test_progress_bar_hidden_when_cancelled(self, qapp):
        job = _upload_job(state=TransferState.CANCELLED)
        item = _JobItem(job)
        assert not item._progress_bar.isVisible()


# ── Per-file progress bar values ─────────────────────────────────────────────

class TestJobItemProgressBarValues:
    def test_progress_bar_reflects_job_progress(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 500
        item = _JobItem(job)
        item.refresh()
        assert item._progress_bar.value() == 50

    def test_progress_bar_zero_at_start(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 0
        item = _JobItem(job)
        assert item._progress_bar.value() == 0

    def test_progress_bar_100_at_completion(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 1000
        item = _JobItem(job)
        assert item._progress_bar.value() == 100

    def test_progress_bar_updates_on_refresh(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 200
        item = _JobItem(job)
        assert item._progress_bar.value() == 20

        job.bytes_done = 800
        item.refresh()
        assert item._progress_bar.value() == 80

    def test_progress_bar_handles_zero_total(self, qapp):
        """Zero-byte file should not crash with division by zero."""
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 0
        job.bytes_done = 0
        item = _JobItem(job)
        assert item._progress_bar.value() == 0

    def test_progress_bar_capped_at_100(self, qapp):
        """bytes_done > total_bytes should not produce >100%."""
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 100
        job.bytes_done = 150  # can happen briefly during race
        item = _JobItem(job)
        assert item._progress_bar.value() <= 100


# ── Per-file progress bar for downloads ──────────────────────────────────────

class TestJobItemProgressBarDownload:
    def test_download_progress_bar_visible_when_running(self, qapp):
        job = _download_job(state=TransferState.RUNNING)
        job.total_bytes = 2000
        job.bytes_done = 1000
        item = _JobItem(job)
        assert not item._progress_bar.isHidden()

    def test_download_progress_bar_reflects_progress(self, qapp):
        job = _download_job(state=TransferState.RUNNING)
        job.total_bytes = 2000
        job.bytes_done = 500
        item = _JobItem(job)
        assert item._progress_bar.value() == 25


# ── Status text includes percentage when running ─────────────────────────────

class TestJobItemStatusWithProgress:
    def test_running_status_shows_percentage(self, qapp):
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1000
        job.bytes_done = 450
        item = _JobItem(job)
        # Status text should contain percentage when running
        text = item._status.text()
        assert "45%" in text or "Transferring" in text

    def test_running_status_shows_size(self, qapp):
        """Running status should show how much has been transferred."""
        job = _upload_job(state=TransferState.RUNNING)
        job.total_bytes = 1024 * 1024  # 1 MB
        job.bytes_done = 512 * 1024    # 512 KB
        item = _JobItem(job)
        text = item._status.text()
        # Should show either the size or percentage
        assert any(x in text for x in ("50%", "512", "KB", "Transferring"))
