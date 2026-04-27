"""Task progress snapshot helpers for GUI Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


PROGRESS_STATUS_PENDING = "pending"
PROGRESS_STATUS_RUNNING = "running"
PROGRESS_STATUS_COMPLETED = "completed"
PROGRESS_STATUS_FAILED = "failed"
PROGRESS_STATUS_CANCELLING = "cancelling"
PROGRESS_STATUS_CANCELLED = "cancelled"
PROGRESS_STATUS_CANCEL_FAILED = "cancel_failed"

TERMINAL_PROGRESS_STATUSES = {
    PROGRESS_STATUS_COMPLETED,
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_CANCELLED,
}

STATUS_LABELS = {
    PROGRESS_STATUS_PENDING: "等待中",
    PROGRESS_STATUS_RUNNING: "运行中",
    PROGRESS_STATUS_COMPLETED: "已完成",
    PROGRESS_STATUS_FAILED: "失败",
    PROGRESS_STATUS_CANCELLING: "正在取消",
    PROGRESS_STATUS_CANCELLED: "已取消",
    PROGRESS_STATUS_CANCEL_FAILED: "取消失败",
}


@dataclass
class TaskProgressSnapshot:
    """UI-facing task progress state."""

    task_id: str
    title: str = "任务执行进展"
    previous_step: str = "尚未开始"
    current_step: str = "准备启动任务"
    next_step: str = "等待步骤解析"
    status: str = PROGRESS_STATUS_PENDING
    current_index: Optional[int] = None
    total_steps: int = 0
    status_message: str = ""
    can_cancel: bool = True

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


def _fallback_steps(instruction: str) -> tuple[str, str, str]:
    text = (instruction or "").strip() or "未提供任务指令"
    return "尚未开始", text, "等待后续步骤"


def build_progress_snapshot(
    task_id: str,
    instruction: str,
    status: str,
    state: Optional[dict] = None,
    status_message: str = "",
) -> TaskProgressSnapshot:
    """Build a popup-friendly snapshot from the current agent state."""
    state = state or {}
    sub_steps = state.get("sub_steps") or []
    current_step_index = state.get("current_step_index", 0)

    if sub_steps:
        previous_step = "尚未开始"
        current_step = "等待执行"
        next_step = "暂无后续步骤"

        if current_step_index > 0 and current_step_index - 1 < len(sub_steps):
            previous_step = sub_steps[current_step_index - 1].get("description", "上一步")

        if current_step_index < len(sub_steps):
            current_step = sub_steps[current_step_index].get("description", "当前步骤")
        else:
            current_step = "任务执行完成"

        if current_step_index + 1 < len(sub_steps):
            next_step = sub_steps[current_step_index + 1].get("description", "下一步")

        current_index = min(current_step_index + 1, len(sub_steps)) if sub_steps else None
        total_steps = len(sub_steps)
    else:
        previous_step, current_step, next_step = _fallback_steps(instruction)
        current_index = 1
        total_steps = 1

    if status == PROGRESS_STATUS_COMPLETED:
        current_step = "任务执行完成"
        next_step = "无"
    elif status == PROGRESS_STATUS_CANCELLED:
        current_step = "任务已取消"
        next_step = "无"
    elif status == PROGRESS_STATUS_CANCELLING:
        next_step = "等待当前步骤安全停止"
    elif status == PROGRESS_STATUS_FAILED:
        next_step = "无"
        if status_message:
            current_step = current_step or "执行失败"

    can_cancel = status not in TERMINAL_PROGRESS_STATUSES and status != PROGRESS_STATUS_CANCELLING

    return TaskProgressSnapshot(
        task_id=task_id,
        previous_step=previous_step,
        current_step=current_step,
        next_step=next_step,
        status=status,
        current_index=current_index,
        total_steps=total_steps,
        status_message=status_message,
        can_cancel=can_cancel,
    )
