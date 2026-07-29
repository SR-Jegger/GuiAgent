"""Popup-related helpers for task progress UI."""

from app.popup.task_progress import (
    PROGRESS_STATUS_CANCEL_FAILED,
    PROGRESS_STATUS_CANCELLED,
    PROGRESS_STATUS_CANCELLING,
    PROGRESS_STATUS_COMPLETED,
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_PENDING,
    PROGRESS_STATUS_RUNNING,
    TaskProgressSnapshot,
    build_progress_snapshot,
)

__all__ = [
    "TaskProgressSnapshot",
    "build_progress_snapshot",
    "PROGRESS_STATUS_PENDING",
    "PROGRESS_STATUS_RUNNING",
    "PROGRESS_STATUS_COMPLETED",
    "PROGRESS_STATUS_FAILED",
    "PROGRESS_STATUS_CANCELLING",
    "PROGRESS_STATUS_CANCELLED",
    "PROGRESS_STATUS_CANCEL_FAILED",
]
