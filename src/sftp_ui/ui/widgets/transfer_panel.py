"""
TransferPanel — live transfer progress + collapsible job queue.

Layout:
  ┌─────────────────────────────────────────────────────────────────────┐
  │  ↑  bigfile.zip          [▓▓▓▓▓▓▒▒▒▒▒▒] 47%   1.2 MB/s  [✕ Cancel]│
  ├─────────────────────────────────────────────────────────────────────┤
  │  [▾ Queue · 2 pending · 1 failed]                                   │
  │  ┌──────────────────────────────────────────────────────────────┐   │
  │  │  ✓  photo.jpg              Done                               │   │
  │  │  ↑  video.mp4              Pending                            │   │
  │  │  ✗  data.csv     Failed    [↻ Resume]                         │   │
  │  └──────────────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, Qt, Signal, QTimer,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from sftp_ui.ui.widgets.smooth_progress_bar import SmoothProgressBar

from sftp_ui.core.transfer import TransferDirection, TransferJob, TransferState
from sftp_ui.animations.transitions import fade_in, fade_out


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_speed(bps: float) -> str:
    return _fmt_size(int(bps)) + "/s"


def _fmt_eta(remaining: int, bps: float) -> str:
    if bps <= 0 or remaining <= 0:
        return ""
    secs = int(remaining / bps)
    if secs < 60:
        return f"~{secs}s"
    return f"~{secs // 60}m {secs % 60}s"


# ── Job item in queue list ─────────────────────────────────────────────────────

class _JobItem(QWidget):
    resume_clicked = Signal(object)  # TransferJob

    _STATE_ICONS = {
        TransferState.PENDING:   ("⋯", "#7f849c"),
        TransferState.RUNNING:   ("↑", "#89b4fa"),
        TransferState.DONE:      ("✓", "#a6e3a1"),
        TransferState.FAILED:    ("✗", "#f38ba8"),
        TransferState.CANCELLED: ("⊘", "#f9e2af"),
        TransferState.PAUSED:    ("‖", "#fab387"),
    }
    _DL_ICON = ("↓", "#89b4fa")

    def __init__(self, job: TransferJob, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)

        self._icon = QLabel()
        self._icon.setFixedWidth(16)
        self._name = QLabel()
        self._name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._status = QLabel()
        self._status.setStyleSheet("color: #7f849c; font-size: 11px;")
        self._resume_btn = QPushButton("↻ Resume")
        self._resume_btn.setFixedWidth(80)
        self._resume_btn.setObjectName("primary")
        self._resume_btn.setVisible(False)
        self._resume_btn.clicked.connect(lambda: self.resume_clicked.emit(self.job))

        row.addWidget(self._icon)
        row.addWidget(self._name)
        row.addWidget(self._status)
        row.addWidget(self._resume_btn)

    def refresh(self) -> None:
        state = self.job.state
        if self.job.direction == TransferDirection.DOWNLOAD and state == TransferState.RUNNING:
            icon_char, icon_color = self._DL_ICON
        else:
            icon_char, icon_color = self._STATE_ICONS.get(state, ("?", "#7f849c"))

        self._icon.setText(icon_char)
        self._icon.setStyleSheet(f"color: {icon_color}; font-weight: bold;")
        self._name.setText(self.job.filename)

        if state == TransferState.CANCELLED and self.job.error:
            status_text = self.job.error.capitalize()
        elif state == TransferState.FAILED:
            status_text = f"Failed: {self.job.error or ''}"
        else:
            status_text = {
                TransferState.PENDING:   "Pending",
                TransferState.RUNNING:   "Transferring…",
                TransferState.DONE:      "Done",
                TransferState.CANCELLED: "Cancelled",
                TransferState.PAUSED:    "Paused",
            }.get(state, "")
        self._status.setText(status_text)
        # Only offer Resume for user-cancelled or failed jobs, not intentional skips
        can_resume = (
            state == TransferState.FAILED
            or (state == TransferState.CANCELLED and not self.job.error)
        )
        self._resume_btn.setVisible(can_resume)


# ── Main panel ─────────────────────────────────────────────────────────────────

class TransferPanel(QWidget):
    cancel_requested = Signal()
    resume_requested = Signal(object)    # TransferJob
    pause_resume_requested = Signal()    # toggle pause/resume

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._jobs: list[TransferJob] = []
        self._job_items: dict[str, _JobItem] = {}  # job.id → widget
        self._queue_expanded = False

        # Speed tracking — polled by timer, not computed in progress callbacks.
        # Paramiko's pipelining buffers writes locally so callbacks fire at
        # disk speed; timer-based sampling of job.bytes_done reflects actual
        # network throughput because the SSH window stalls writes on slow links.
        self._speed_bps: float = 0.0
        self._speed_sample_bytes: int = 0
        self._speed_sample_ts: float = 0.0
        self._speed_timer = QTimer(self)
        self._speed_timer.setInterval(500)
        self._speed_timer.timeout.connect(self._sample_speed)

        self._queue_anim: Optional[QPropertyAnimation] = None
        self._first_shown_at: float = 0.0

        self._build_ui()
        self.setVisible(False)

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Active transfer row
        self._active_row = QWidget()
        self._active_row.setObjectName("transfer-active")
        active_layout = QHBoxLayout(self._active_row)
        active_layout.setContentsMargins(12, 8, 12, 8)
        active_layout.setSpacing(10)

        self._dir_icon = QLabel("↑")
        self._dir_icon.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._dir_icon.setFixedWidth(16)

        # Filename: Expanding but capped so it never pushes cancel off-screen.
        # Text is elided with "…" when it would overflow.
        self._filename_label = QLabel()
        self._filename_label.setStyleSheet("font-weight: 600;")
        self._filename_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._filename_label.setMaximumWidth(260)
        self._filename_label.setMinimumWidth(60)

        # File counter e.g. "3 / 45"
        self._file_counter_label = QLabel()
        self._file_counter_label.setStyleSheet("color: #7f849c; font-size: 11px;")
        self._file_counter_label.setFixedWidth(60)
        self._file_counter_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._file_counter_label.setVisible(False)

        self._progress_bar = SmoothProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)

        self._pct_label = QLabel("0%")
        self._pct_label.setFixedWidth(40)
        self._pct_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pct_label.setStyleSheet("font-weight: 600;")

        self._speed_label = QLabel()
        self._speed_label.setMinimumWidth(70)
        self._speed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._speed_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        self._speed_label.setVisible(False)

        self._eta_label = QLabel()
        self._eta_label.setMinimumWidth(45)
        self._eta_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._eta_label.setStyleSheet("font-size: 11px;")
        self._eta_label.setVisible(False)

        self._pause_btn = QPushButton("‖ Pause")
        self._pause_btn.setObjectName("pause-btn")
        self._pause_btn.setFixedHeight(28)
        self._pause_btn.setMinimumWidth(80)
        self._pause_btn.setToolTip("Pause / resume all transfers")
        self._pause_btn.clicked.connect(self.pause_resume_requested)

        self._cancel_btn = QPushButton("✕ Cancel")
        self._cancel_btn.setFixedHeight(28)
        self._cancel_btn.setMinimumWidth(70)
        self._cancel_btn.setToolTip("Cancel current transfer")
        self._cancel_btn.setObjectName("danger")
        self._cancel_btn.clicked.connect(self.cancel_requested)

        active_layout.addWidget(self._dir_icon)
        active_layout.addWidget(self._filename_label)
        active_layout.addWidget(self._file_counter_label)
        active_layout.addWidget(self._progress_bar, stretch=1)
        active_layout.addWidget(self._pct_label)
        active_layout.addWidget(self._speed_label)
        active_layout.addWidget(self._eta_label)
        active_layout.addWidget(self._pause_btn)
        active_layout.addWidget(self._cancel_btn)
        root.addWidget(self._active_row)

        # Separator — objectName lets QSS control the colour without inline override
        sep = QFrame()
        sep.setObjectName("transfer-sep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # Queue toggle row
        self._queue_toggle = QPushButton("▸  Queue")
        self._queue_toggle.setObjectName("queue-toggle")
        self._queue_toggle.clicked.connect(self._toggle_queue)
        root.addWidget(self._queue_toggle)

        # Collapsible queue list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setMaximumHeight(0)  # collapsed by default
        self._scroll.setStyleSheet("background: transparent;")

        self._queue_container = QWidget()
        self._queue_container.setStyleSheet("background: transparent;")
        self._queue_layout = QVBoxLayout(self._queue_container)
        self._queue_layout.setContentsMargins(0, 0, 0, 0)
        self._queue_layout.setSpacing(1)

        # Overflow label shown when job count exceeds _MAX_VISIBLE_ITEMS
        self._overflow_label = QLabel()
        self._overflow_label.setStyleSheet("color: #7f849c; font-size: 11px; padding: 2px 12px;")
        self._overflow_label.setVisible(False)
        self._queue_layout.addWidget(self._overflow_label)

        self._queue_layout.addStretch()
        self._scroll.setWidget(self._queue_container)
        root.addWidget(self._scroll)

    # Maximum _JobItem widgets rendered — beyond this we show an overflow label.
    # Large directory uploads can have thousands of files; creating a widget per
    # file freezes the UI via rapid signal delivery on the main thread.
    _MAX_VISIBLE_ITEMS = 80

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_paused(self, paused: bool) -> None:
        """Update the pause button label to reflect the current queue state."""
        self._pause_btn.setText("▶ Resume" if paused else "‖ Pause")

    def add_job(self, job: TransferJob) -> None:
        self._jobs.append(job)

        if len(self._job_items) < self._MAX_VISIBLE_ITEMS:
            item = _JobItem(job)
            item.resume_clicked.connect(self.resume_requested)
            self._job_items[job.id] = item
            # Insert before overflow label and stretch (last 2 items)
            insert_at = max(0, self._queue_layout.count() - 2)
            self._queue_layout.insertWidget(insert_at, item)

        self._update_overflow_label()
        self._update_toggle_label()
        if not self.isVisible():
            import time
            self._first_shown_at = time.monotonic()
            self.setVisible(True)
            fade_in(self, 180).start()

    def update_progress(self, job: TransferJob, done: int, total: int) -> None:
        # With parallel workers multiple jobs are running at once.
        # Use job.bytes_done on all jobs so every worker's progress counts.
        batch_total = sum(j.total_bytes for j in self._jobs)
        batch_done  = sum(j.bytes_done  for j in self._jobs)
        pct = max(0, min(100, int(batch_done * 100 / batch_total))) if batch_total else 0

        # Show the triggering job's filename in the active row.
        icon = "↓" if job.direction == TransferDirection.DOWNLOAD else "↑"
        self._dir_icon.setText(icon)
        fm = self._filename_label.fontMetrics()
        elided = fm.elidedText(job.filename, Qt.TextElideMode.ElideMiddle,
                               self._filename_label.maximumWidth())
        self._filename_label.setText(elided)

        total_jobs = len(self._jobs)
        if total_jobs > 1:
            done_jobs = sum(1 for j in self._jobs
                            if j.state not in (TransferState.PENDING, TransferState.RUNNING))
            self._file_counter_label.setText(f"{done_jobs} / {total_jobs}")
            self._file_counter_label.setVisible(True)
        else:
            self._file_counter_label.setVisible(False)

        self._progress_bar.setValue(pct)
        self._pct_label.setText(f"{pct}%")

        if job.id in self._job_items:
            self._job_items[job.id].refresh()

        # Start the speed timer — it samples aggregate bytes_done every 500ms.
        if not self._speed_timer.isActive():
            self._speed_sample_bytes = batch_done
            self._speed_sample_ts = time.monotonic()
            self._speed_timer.start()

    def refresh_job(self, job: TransferJob) -> None:
        """Called when a worker picks up an already-registered job (state → RUNNING)."""
        if job.id in self._job_items:
            self._job_items[job.id].refresh()

    def job_finished(self, job: TransferJob) -> None:
        if job.id in self._job_items:
            self._job_items[job.id].refresh()
        self._update_toggle_label()
        # Don't reset the speed sample — with parallel workers other jobs are
        # still running and we want continuous throughput measurement.

        if self._all_settled():
            # Small delay before hiding so the user can see the final state
            QTimer.singleShot(2500, self._maybe_hide)

    def _all_settled(self) -> bool:
        return all(
            j.state not in (TransferState.PENDING, TransferState.RUNNING)
            for j in self._jobs
        )

    def _maybe_hide(self) -> None:
        import time
        if not self._all_settled():
            return
        # Guarantee at least 2.5 s of visibility so the user sees the result
        elapsed = time.monotonic() - self._first_shown_at
        remaining = max(0, 2.5 - elapsed)
        if remaining > 0.05:
            QTimer.singleShot(int(remaining * 1000), self._maybe_hide)
            return
        anim = fade_out(self, 300)
        anim.finished.connect(lambda: (self.setVisible(False), self._clear()))
        anim.start()

    def _clear(self) -> None:
        self._jobs.clear()
        self._job_items.clear()
        # Remove all widgets except the overflow label and the stretch
        for i in reversed(range(self._queue_layout.count())):
            item = self._queue_layout.itemAt(i)
            w = item.widget() if item else None
            if w and w is not self._overflow_label:
                self._queue_layout.removeWidget(w)
                w.deleteLater()
        self._overflow_label.setVisible(False)
        self._speed_timer.stop()
        self._speed_bps = 0.0
        self._speed_sample_bytes = 0
        self._speed_sample_ts = 0.0
        self._progress_bar.setValue(0)
        self._pct_label.setText("0%")
        self._speed_label.setText("")
        self._speed_label.setVisible(False)
        self._eta_label.setText("")
        self._eta_label.setVisible(False)
        self._file_counter_label.setVisible(False)

    def _update_overflow_label(self) -> None:
        hidden = len(self._jobs) - len(self._job_items)
        if hidden > 0:
            self._overflow_label.setText(f"  … and {hidden} more file(s)")
            self._overflow_label.setVisible(True)
        else:
            self._overflow_label.setVisible(False)

    # ── Queue toggle ───────────────────────────────────────────────────────────

    def _toggle_queue(self) -> None:
        self._queue_expanded = not self._queue_expanded
        target_h = min(200, max(60, len(self._jobs) * 36)) if self._queue_expanded else 0
        arrow = "▾" if self._queue_expanded else "▸"
        self._update_toggle_label(arrow=arrow)

        if self._queue_anim:
            self._queue_anim.stop()
        self._queue_anim = QPropertyAnimation(self._scroll, b"maximumHeight", self)
        self._queue_anim.setDuration(200)
        self._queue_anim.setStartValue(self._scroll.maximumHeight())
        self._queue_anim.setEndValue(target_h)
        self._queue_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._queue_anim.start()

    def _update_toggle_label(self, arrow: Optional[str] = None) -> None:
        if arrow is None:
            arrow = "▾" if self._queue_expanded else "▸"
        pending = sum(1 for j in self._jobs if j.state == TransferState.PENDING)
        # Count only genuine failures and user-initiated cancellations (job.error
        # is set for intentional skips like "up to date" / "skipped (exists)").
        failed = sum(
            1 for j in self._jobs
            if j.state == TransferState.FAILED
            or (j.state == TransferState.CANCELLED and not j.error)
        )
        parts = [f"{len(self._jobs)} jobs"]
        if pending:
            parts.append(f"{pending} pending")
        if failed:
            parts.append(f"{failed} failed")
        self._queue_toggle.setText(f"{arrow}  Queue · {' · '.join(parts)}")

    def _sample_speed(self) -> None:
        """Called every 500 ms to measure real network throughput.

        Sums bytes_done across ALL running jobs so parallel workers all
        contribute to the displayed speed.  Reads job.bytes_done directly —
        this reflects actual bytes ACK'd by the remote because paramiko's SSH
        window stalls writes when the window fills.
        """
        running = [j for j in self._jobs
                   if j.state in (TransferState.RUNNING, TransferState.PENDING)]
        if not running:
            self._speed_bps = 0.0
            self._speed_label.setText("")
            self._eta_label.setText("")
            self._speed_timer.stop()
            return

        now = time.monotonic()
        # Aggregate bytes across every job (done jobs contribute their full total)
        current = sum(j.bytes_done for j in self._jobs)
        elapsed = now - self._speed_sample_ts

        if self._speed_sample_ts > 0 and elapsed > 0:
            delta = current - self._speed_sample_bytes
            if delta >= 0:
                instant = delta / elapsed
                # Exponential moving average — smooth out burst/stall cycles
                self._speed_bps = (0.6 * self._speed_bps + 0.4 * instant
                                   if self._speed_bps > 0 else instant)

        self._speed_sample_bytes = current
        self._speed_sample_ts = now

        speed_text = _fmt_speed(self._speed_bps) if self._speed_bps > 0 else ""
        self._speed_label.setText(speed_text)
        self._speed_label.setVisible(bool(speed_text))

        batch_total = sum(j.total_bytes for j in self._jobs)
        remaining = max(0, batch_total - current)
        eta_text = _fmt_eta(remaining, self._speed_bps)
        self._eta_label.setText(eta_text)
        self._eta_label.setVisible(bool(eta_text))
