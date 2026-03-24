"""
Transfer queue — processes TransferJobs in parallel using N worker threads,
each with its own independent SFTP connection.

engine_factory is called once per worker to create a dedicated TransferEngine.
Workers run concurrently so many small files are handled without per-file
round-trip latency multiplying across the whole batch.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from sftp_ui.core.transfer import (
    TransferDirection,
    TransferEngine,
    TransferError,
    TransferJob,
    TransferState,
)

ProgressCallback = Callable[["TransferJob", int, int], None]
JobCallback = Callable[["TransferJob"], None]


class TransferQueue:
    def __init__(
        self,
        engine_factory: Callable[[], TransferEngine],
        num_workers: int = 4,
        max_retries: int = 5,
        retry_delay: float = 2.0,
    ) -> None:
        self._engine_factory = engine_factory
        self._num_workers = num_workers
        self._max_retries = max_retries
        self._retry_delay = retry_delay

        self._jobs: list[TransferJob] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        # Set = running (not paused); clear = paused.
        self._pause_event = threading.Event()
        self._pause_event.set()

        # Generation-based cancel: incrementing the counter makes all
        # currently-running jobs see cancel_flag() == True, while jobs
        # that start afterwards get a fresh snapshot and are unaffected.
        self._cancel_gen: int = 0

        self.on_progress: Optional[ProgressCallback] = None
        self.on_job_started: Optional[JobCallback] = None
        self.on_job_done: Optional[JobCallback] = None
        self.on_job_failed: Optional[JobCallback] = None
        self.on_job_cancelled: Optional[JobCallback] = None
        self.on_worker_error: Optional[JobCallback] = None  # called with exc when engine_factory fails

    # ── public API ────────────────────────────────────────────────────────────

    def enqueue(self, job: TransferJob) -> None:
        with self._lock:
            self._jobs.append(job)

    def jobs(self) -> list[TransferJob]:
        with self._lock:
            return list(self._jobs)

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs
                if j.state in (TransferState.PENDING, TransferState.RUNNING)
            )

    def pause(self) -> None:
        """Pause the queue — workers finish their current chunk then wait."""
        self._pause_event.clear()

    def unpause(self) -> None:
        """Resume a paused queue."""
        self._pause_event.set()

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def cancel_current(self) -> None:
        """Cancel all jobs that are currently running."""
        with self._lock:
            self._cancel_gen += 1

    def resume(self, job: TransferJob) -> None:
        """Re-queue a cancelled or failed job — engine resumes from byte offset."""
        with self._lock:
            if job.state in (TransferState.CANCELLED, TransferState.FAILED):
                job.state = TransferState.PENDING
                job.error = None

    def start(self) -> None:
        if self._threads:
            return
        self._stop_event.clear()
        for _ in range(self._num_workers):
            t = threading.Thread(target=self._run_worker, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        with self._lock:
            self._cancel_gen += 1  # abort all running transfers
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()

    def clear_done(self) -> None:
        with self._lock:
            self._jobs = [
                j for j in self._jobs
                if j.state not in (TransferState.DONE, TransferState.CANCELLED)
            ]

    # ── worker ────────────────────────────────────────────────────────────────

    def _next_pending(self) -> Optional[TransferJob]:
        """Atomically claim the next PENDING job by marking it RUNNING."""
        with self._lock:
            for job in self._jobs:
                if job.state == TransferState.PENDING:
                    job.state = TransferState.RUNNING
                    return job
        return None

    def _run_worker(self) -> None:
        try:
            engine = self._engine_factory()
        except Exception as exc:
            if self.on_worker_error:
                self.on_worker_error(exc)
            return

        # Mutable reference so reconnect_callback can swap the engine
        eng = [engine]

        def reconnect_cb() -> None:
            """Close the dead engine and create a fresh one."""
            try:
                eng[0]._sftp.close()
            except Exception:
                pass
            eng[0] = self._engine_factory()

        try:
            while not self._stop_event.is_set():
                # Block while the queue is paused (but wake quickly on stop)
                if not self._pause_event.wait(timeout=0.1):
                    continue
                job = self._next_pending()
                if job is None:
                    self._stop_event.wait(0.05)
                    continue

                # Snapshot the cancel generation at job-start time.
                # cancel_current() increments _cancel_gen so this lambda
                # returns True for all jobs started before the cancel,
                # but False for jobs started after it.
                with self._lock:
                    start_gen = self._cancel_gen

                def cancel_flag(g=start_gen):
                    # Block (spin) while paused; return True if cancelled.
                    while not self._pause_event.wait(timeout=0.05):
                        if self._cancel_gen > g:
                            return True
                    return self._cancel_gen > g

                if self.on_job_started:
                    self.on_job_started(job)

                def _progress(done: int, total: int, _job=job) -> None:
                    if self.on_progress:
                        self.on_progress(_job, done, total)

                try:
                    if job.direction == TransferDirection.DOWNLOAD:
                        eng[0].download_with_retry(
                            job,
                            progress_callback=_progress,
                            cancel_flag=cancel_flag,
                            max_retries=self._max_retries,
                            retry_delay=self._retry_delay,
                            reconnect_callback=reconnect_cb,
                        )
                    else:
                        eng[0].upload_with_retry(
                            job,
                            progress_callback=_progress,
                            cancel_flag=cancel_flag,
                            max_retries=self._max_retries,
                            retry_delay=self._retry_delay,
                            reconnect_callback=reconnect_cb,
                        )
                except TransferError:
                    pass

                if job.state == TransferState.DONE and self.on_job_done:
                    self.on_job_done(job)
                elif job.state == TransferState.FAILED and self.on_job_failed:
                    self.on_job_failed(job)
                elif job.state == TransferState.CANCELLED and self.on_job_cancelled:
                    self.on_job_cancelled(job)
        finally:
            try:
                eng[0]._sftp.close()
            except Exception:
                pass
