"""
Transfer engine — upload and download with resume support.

Upload:  local → remote  (checks remote_size, appends/resumes)
Download: remote → local  (checks local_size, appends/resumes)

On any I/O error → raise TransferError; caller handles retries.
"""
from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

CHUNK_SIZE = 1024 * 1024  # 1 MB — fewer round-trips vs the old 256 KB


class TransferDirection(Enum):
    UPLOAD = auto()
    DOWNLOAD = auto()


class TransferState(Enum):
    PENDING = auto()
    RUNNING = auto()
    PAUSED = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


class TransferError(Exception):
    pass


ProgressCallback = Callable[[int, int], None]  # (bytes_done, total_bytes)


@dataclass
class TransferJob:
    local_path: str
    remote_path: str
    direction: TransferDirection = TransferDirection.UPLOAD
    id: str = field(default_factory=lambda: str(uuid4()))
    state: TransferState = TransferState.PENDING
    bytes_done: int = 0
    total_bytes: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def progress(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return min(1.0, self.bytes_done / self.total_bytes)

    @property
    def filename(self) -> str:
        if self.direction == TransferDirection.DOWNLOAD:
            return Path(self.remote_path).name
        return Path(self.local_path).name


class TransferEngine:
    def __init__(self, sftp_client, chunk_size: int = CHUNK_SIZE) -> None:
        self._sftp = sftp_client
        self._chunk_size = chunk_size

    # ── Upload ────────────────────────────────────────────────────────────────

    def upload(
        self,
        job: TransferJob,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
    ) -> None:
        local_path = job.local_path
        remote_path = job.remote_path

        if not os.path.isfile(local_path):
            job.state = TransferState.FAILED
            job.error = f"Local file not found: {local_path!r}"
            raise TransferError(job.error)

        local_size = os.path.getsize(local_path)
        job.total_bytes = local_size

        try:
            remote_size = self._sftp.remote_size(remote_path)
        except Exception as exc:
            job.state = TransferState.FAILED
            job.error = f"Could not stat remote path: {exc}"
            raise TransferError(job.error) from exc

        if remote_size > local_size:
            remote_size = 0

        if remote_size == local_size and local_size > 0:
            job.bytes_done = local_size
            job.state = TransferState.DONE
            job.finished_at = time.time()
            if progress_callback:
                progress_callback(local_size, local_size)
            return

        job.bytes_done = remote_size
        job.state = TransferState.RUNNING
        open_mode = "ab" if remote_size > 0 else "wb"

        try:
            with open(local_path, "rb") as local_f:
                local_f.seek(remote_size)
                with self._sftp.open_remote(remote_path, open_mode) as remote_f:
                    remote_f.set_pipelined(True)
                    while True:
                        if cancel_flag and cancel_flag():
                            job.state = TransferState.CANCELLED
                            job.finished_at = time.time()
                            return
                        chunk = local_f.read(self._chunk_size)
                        if not chunk:
                            break
                        remote_f.write(chunk)
                        job.bytes_done += len(chunk)
                        if progress_callback:
                            progress_callback(job.bytes_done, local_size)
        except Exception as exc:
            job.state = TransferState.FAILED
            job.error = str(exc)
            raise TransferError(str(exc)) from exc

        job.state = TransferState.DONE
        job.finished_at = time.time()

        # Preserve local file permissions on the remote copy.
        # Only the lower 12 bits (mode bits) are sent; the server may apply its
        # own umask on top, but this is still far better than silently using 0644.
        # Errors are intentionally swallowed — chmod is best-effort and a failure
        # here must not turn a successful transfer into a failed one.
        try:
            local_mode = stat.S_IMODE(os.stat(local_path).st_mode)
            self._sftp.chmod(remote_path, local_mode)
        except Exception:
            pass

    def upload_with_retry(
        self,
        job: TransferJob,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        reconnect_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self._with_retry(
            self.upload, job, progress_callback, cancel_flag,
            max_retries, retry_delay, reconnect_callback,
        )

    # ── Download ──────────────────────────────────────────────────────────────

    def download(
        self,
        job: TransferJob,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
    ) -> None:
        remote_path = job.remote_path
        local_path = job.local_path

        try:
            remote_size = self._sftp.stat(remote_path).st_size or 0
        except Exception as exc:
            job.state = TransferState.FAILED
            job.error = f"Could not stat remote file: {exc}"
            raise TransferError(job.error) from exc

        job.total_bytes = remote_size

        local_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
        if local_size > remote_size:
            local_size = 0
            try:
                os.remove(local_path)
            except OSError:
                pass

        if local_size == remote_size and remote_size > 0:
            job.bytes_done = remote_size
            job.state = TransferState.DONE
            job.finished_at = time.time()
            if progress_callback:
                progress_callback(remote_size, remote_size)
            return

        job.bytes_done = local_size
        job.state = TransferState.RUNNING
        open_mode = "ab" if local_size > 0 else "wb"

        try:
            with self._sftp.open_remote(remote_path, "rb") as remote_f:
                remote_f.prefetch()
                remote_f.seek(local_size)
                os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
                with open(local_path, open_mode) as local_f:
                    while True:
                        if cancel_flag and cancel_flag():
                            job.state = TransferState.CANCELLED
                            job.finished_at = time.time()
                            return
                        chunk = remote_f.read(self._chunk_size)
                        if not chunk:
                            break
                        local_f.write(chunk)
                        job.bytes_done += len(chunk)
                        if progress_callback:
                            progress_callback(job.bytes_done, remote_size)
        except Exception as exc:
            job.state = TransferState.FAILED
            job.error = str(exc)
            raise TransferError(str(exc)) from exc

        job.state = TransferState.DONE
        job.finished_at = time.time()

    def download_with_retry(
        self,
        job: TransferJob,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        reconnect_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        self._with_retry(
            self.download, job, progress_callback, cancel_flag,
            max_retries, retry_delay, reconnect_callback,
        )

    # ── Shared retry wrapper ──────────────────────────────────────────────────

    def _with_retry(
        self,
        fn,
        job: TransferJob,
        progress_callback,
        cancel_flag,
        max_retries: int,
        retry_delay: float,
        reconnect_callback,
    ) -> None:
        last_error: Optional[TransferError] = None
        for attempt in range(1, max_retries + 1):
            if cancel_flag and cancel_flag():
                job.state = TransferState.CANCELLED
                return
            if attempt > 1:
                job.state = TransferState.PENDING
                job.error = None
            try:
                fn(job, progress_callback=progress_callback, cancel_flag=cancel_flag)
                return
            except TransferError as exc:
                last_error = exc
                if attempt < max_retries:
                    if reconnect_callback:
                        try:
                            reconnect_callback()
                        except Exception:
                            pass
                    time.sleep(retry_delay)
        job.state = TransferState.FAILED
        job.error = str(last_error)
        raise last_error
