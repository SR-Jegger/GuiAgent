"""
PySide6-based task progress popup.

The popup layer is isolated from task execution. PySide6 is imported lazily
so the backend can still run even if the GUI dependency is missing.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from app.popup.task_api_client import TaskApiClient
from app.popup.task_progress import (
    PROGRESS_STATUS_CANCEL_FAILED,
    PROGRESS_STATUS_CANCELLED,
    PROGRESS_STATUS_CANCELLING,
    PROGRESS_STATUS_COMPLETED,
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_RUNNING,
    TaskProgressSnapshot,
)


_QT_RUNTIME = None
_QT_RUNTIME_LOCK = threading.Lock()


def _get_qt_runtime():
    global _QT_RUNTIME
    with _QT_RUNTIME_LOCK:
        if _QT_RUNTIME is None:
            _QT_RUNTIME = _QtPopupRuntime()
        return _QT_RUNTIME


class TaskProgressPopup:
    """Thin proxy that forwards popup lifecycle events to the Qt runtime."""

    def __init__(
        self,
        snapshot: TaskProgressSnapshot,
        api_client: Optional[TaskApiClient] = None,
    ):
        self._snapshot = snapshot
        self._api_client = api_client or TaskApiClient()
        self._runtime = _get_qt_runtime()

    def start(self) -> None:
        self._runtime.start_popup(self._snapshot, self._api_client)

    def update(self, snapshot: TaskProgressSnapshot) -> None:
        self._snapshot = snapshot
        self._runtime.update_popup(snapshot)

    def close(self) -> None:
        self._runtime.close_popup(self._snapshot.task_id)


class _QtPopupRuntime:
    """Singleton Qt runtime hosted on a dedicated UI thread."""

    def __init__(self):
        self._command_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed = False
        self._failure_message = ""

    def start_popup(self, snapshot: TaskProgressSnapshot, api_client: TaskApiClient) -> None:
        self._ensure_thread()
        if self._failed:
            print(f"[TaskProgressPopup] Qt popup unavailable: {self._failure_message}")
            return
        self._command_queue.put(("start", snapshot, api_client))

    def update_popup(self, snapshot: TaskProgressSnapshot) -> None:
        if self._failed:
            return
        self._command_queue.put(("update", snapshot))

    def close_popup(self, task_id: str) -> None:
        if self._failed:
            return
        self._command_queue.put(("close", task_id))

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="qt-task-progress-popup",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_event_loop(self) -> None:
        try:
            from PySide6 import QtCore, QtWidgets
        except Exception as exc:
            self._failed = True
            self._failure_message = str(exc)
            self._ready.set()
            return

        WindowClass = _build_task_progress_window()

        app = QtWidgets.QApplication.instance()
        owns_app = app is None
        if owns_app:
            app = QtWidgets.QApplication([])
            app.setQuitOnLastWindowClosed(False)

        windows: dict[str, object] = {}

        def process_commands() -> None:
            while True:
                try:
                    command = self._command_queue.get_nowait()
                except queue.Empty:
                    break

                kind = command[0]
                if kind == "start":
                    snapshot, api_client = command[1], command[2]
                    window = windows.get(snapshot.task_id)
                    if window is None:
                        window = WindowClass(snapshot, api_client)
                        windows[snapshot.task_id] = window
                        window.closed.connect(lambda task_id=snapshot.task_id: windows.pop(task_id, None))
                        window.show_with_animation()
                    else:
                        window.update_snapshot(snapshot)
                elif kind == "update":
                    snapshot = command[1]
                    window = windows.get(snapshot.task_id)
                    if window is not None:
                        window.update_snapshot(snapshot)
                elif kind == "close":
                    task_id = command[1]
                    window = windows.get(task_id)
                    if window is not None:
                        window.close_with_animation()

        timer = QtCore.QTimer()
        timer.timeout.connect(process_commands)
        timer.start(60)
        self._ready.set()

        if owns_app:
            app.exec()
        else:
            keepalive = threading.Event()
            while not keepalive.wait(1):
                pass


def _build_task_progress_window():
    from PySide6 import QtCore, QtGui, QtWidgets

    class TaskProgressWindow(QtWidgets.QWidget):
        closed = QtCore.Signal()
        snapshot_received = QtCore.Signal(object)

        def __init__(self, snapshot: TaskProgressSnapshot, api_client: TaskApiClient):
            super().__init__(None)
            self.snapshot = snapshot
            self.api_client = api_client
            self._auto_close_timer = QtCore.QTimer(self)
            self._auto_close_timer.setSingleShot(True)
            self._auto_close_timer.timeout.connect(self.close_with_animation)
            self._cancel_worker = None

            self._init_window()
            self._build_ui()
            self.snapshot_received.connect(self.update_snapshot)
            self.update_snapshot(snapshot)
            self._move_to_bottom_right()

        def _init_window(self) -> None:
            self.setWindowFlags(
                QtCore.Qt.Tool
                | QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.resize(450, 220)

            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(34)
            shadow.setOffset(0, 10)
            shadow.setColor(QtGui.QColor(3, 12, 24, 180))
            self.setGraphicsEffect(shadow)

        def _build_ui(self) -> None:
            root_layout = QtWidgets.QVBoxLayout(self)
            root_layout.setContentsMargins(0, 0, 0, 0)

            shell = QtWidgets.QFrame()
            shell.setObjectName("TaskProgressShell")
            root_layout.addWidget(shell)

            layout = QtWidgets.QVBoxLayout(shell)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(9)

            header_row = QtWidgets.QHBoxLayout()
            header_row.setSpacing(8)
            layout.addLayout(header_row)

            title_col = QtWidgets.QVBoxLayout()
            title_col.setSpacing(2)
            header_row.addLayout(title_col, 1)

            self.title_label = QtWidgets.QLabel("任务执行进展")
            self.title_label.setObjectName("PopupTitle")
            title_col.addWidget(self.title_label)

            self.meta_label = QtWidgets.QLabel()
            self.meta_label.setObjectName("PopupMeta")
            title_col.addWidget(self.meta_label)

            self.status_badge = QtWidgets.QLabel()
            self.status_badge.setAlignment(QtCore.Qt.AlignCenter)
            self.status_badge.setMinimumWidth(88)
            self.status_badge.setObjectName("StatusBadge")
            header_row.addWidget(self.status_badge, 0, QtCore.Qt.AlignTop)

            self.message_label = QtWidgets.QLabel()
            self.message_label.setObjectName("StatusMessage")
            self.message_label.setWordWrap(True)
            layout.addWidget(self.message_label)

            self.previous_card = self._build_step_card("上一步", False)
            layout.addWidget(self.previous_card["frame"])

            self.current_card = self._build_step_card("当前步骤", True)
            layout.addWidget(self.current_card["frame"])

            footer_row = QtWidgets.QHBoxLayout()
            footer_row.setSpacing(8)
            layout.addLayout(footer_row)

            self.hint_label = QtWidgets.QLabel("任务执行中")
            self.hint_label.setObjectName("FooterHint")
            footer_row.addWidget(self.hint_label, 1)

            self.cancel_button = QtWidgets.QPushButton("取消任务")
            self.cancel_button.setObjectName("CancelButton")
            self.cancel_button.clicked.connect(self._request_cancel)
            footer_row.addWidget(self.cancel_button, 0, QtCore.Qt.AlignRight)

            self.setStyleSheet(
                """
                #TaskProgressShell {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(11,18,32,245),
                        stop:1 rgba(19,31,52,242));
                    border: 1px solid rgba(91, 131, 177, 0.35);
                    border-radius: 18px;
                }
                #PopupTitle {
                    color: #F8FBFF;
                    font-size: 14px;
                    font-weight: 700;
                }
                #PopupMeta {
                    color: #7DD3FC;
                    font-size: 11px;
                }
                #StatusBadge {
                    border-radius: 11px;
                    padding: 6px 10px;
                    font-size: 11px;
                    font-weight: 700;
                    color: #F8FBFF;
                    background: #2563EB;
                }
                #StatusMessage {
                    color: #9FB0C7;
                    font-size: 11px;
                    padding: 0;
                }
                #StepFrame {
                    border-radius: 14px;
                    background: rgba(18, 32, 51, 0.96);
                    border: 1px solid rgba(93, 120, 158, 0.20);
                }
                #CurrentStepFrame {
                    border-radius: 15px;
                    background: rgba(19, 50, 74, 0.98);
                    border: 1px solid rgba(59, 166, 217, 0.85);
                }
                #StepTitle {
                    color: #7F93AD;
                    font-size: 10px;
                    font-weight: 600;
                }
                #StepText {
                    color: #D8E1EE;
                    font-size: 11px;
                }
                #CurrentStepTitle {
                    color: #8BDCFF;
                    font-size: 10px;
                    font-weight: 700;
                }
                #CurrentStepText {
                    color: #EFF9FF;
                    font-size: 12px;
                    font-weight: 700;
                }
                #FooterHint {
                    color: #7F93AD;
                    font-size: 10px;
                }
                #CancelButton {
                    background: #2563EB;
                    color: #F8FBFF;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 11px;
                    font-weight: 700;
                }
                #CancelButton:hover {
                    background: #3B82F6;
                }
                #CancelButton:disabled {
                    background: rgba(100, 116, 139, 0.45);
                    color: #D0D7E5;
                }
                """
            )

        def _build_step_card(self, title: str, highlighted: bool):
            frame = QtWidgets.QFrame()
            frame.setObjectName("CurrentStepFrame" if highlighted else "StepFrame")
            layout = QtWidgets.QVBoxLayout(frame)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(3)

            title_label = QtWidgets.QLabel(title)
            title_label.setObjectName("CurrentStepTitle" if highlighted else "StepTitle")
            layout.addWidget(title_label)

            text_label = QtWidgets.QLabel()
            text_label.setWordWrap(True)
            text_label.setObjectName("CurrentStepText" if highlighted else "StepText")
            layout.addWidget(text_label)
            return {"frame": frame, "text": text_label}

        def _move_to_bottom_right(self) -> None:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            self.move(
                geo.x() + geo.width() - self.width() - 22,
                geo.y() + geo.height() - self.height() - 26,
            )

        def show_with_animation(self) -> None:
            self.setWindowOpacity(0.0)
            self.show()
            self._move_to_bottom_right()

            start_pos = self.pos() + QtCore.QPoint(0, 14)
            end_pos = self.pos()
            self.move(start_pos)

            group = QtCore.QParallelAnimationGroup(self)

            fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(180)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)

            slide = QtCore.QPropertyAnimation(self, b"pos", self)
            slide.setDuration(180)
            slide.setStartValue(start_pos)
            slide.setEndValue(end_pos)
            slide.setEasingCurve(QtCore.QEasingCurve.OutCubic)

            group.addAnimation(fade)
            group.addAnimation(slide)
            group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        def close_with_animation(self) -> None:
            if not self.isVisible():
                self.close()
                return

            group = QtCore.QParallelAnimationGroup(self)

            fade = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(180)
            fade.setStartValue(self.windowOpacity())
            fade.setEndValue(0.0)

            slide = QtCore.QPropertyAnimation(self, b"pos", self)
            slide.setDuration(180)
            slide.setStartValue(self.pos())
            slide.setEndValue(self.pos() + QtCore.QPoint(0, 10))
            slide.setEasingCurve(QtCore.QEasingCurve.InCubic)

            group.addAnimation(fade)
            group.addAnimation(slide)
            group.finished.connect(self.close)
            group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        def update_snapshot(self, snapshot: TaskProgressSnapshot) -> None:
            self.snapshot = snapshot
            self.title_label.setText(snapshot.title)
            self.meta_label.setText(
                f"任务 ID: {snapshot.task_id[:8]}    步骤: {snapshot.current_index or '-'} / {snapshot.total_steps or '-'}"
            )
            self.previous_card["text"].setText(snapshot.previous_step or "无")
            self.current_card["text"].setText(snapshot.current_step or "无")
            self.message_label.setText(snapshot.status_message or "")
            self.hint_label.setText(self._hint_text(snapshot))
            self.cancel_button.setEnabled(snapshot.can_cancel)
            self._apply_status_style(snapshot)

            if snapshot.status in (PROGRESS_STATUS_COMPLETED, PROGRESS_STATUS_CANCELLED):
                self._auto_close_timer.start(2600)
            else:
                self._auto_close_timer.stop()

        def _hint_text(self, snapshot: TaskProgressSnapshot) -> str:
            if snapshot.status == PROGRESS_STATUS_FAILED:
                return "任务失败，保留窗口供排查"
            if snapshot.status == PROGRESS_STATUS_CANCEL_FAILED:
                return "取消失败，可重试"
            if snapshot.status == PROGRESS_STATUS_CANCELLING:
                return "等待任务安全停止"
            if snapshot.status == PROGRESS_STATUS_CANCELLED:
                return "任务已取消"
            if snapshot.status == PROGRESS_STATUS_COMPLETED:
                return "任务已完成"
            return "任务执行中"

        def _apply_status_style(self, snapshot: TaskProgressSnapshot) -> None:
            color_map = {
                PROGRESS_STATUS_RUNNING: ("#2563EB", "#9FB0C7"),
                PROGRESS_STATUS_CANCELLING: ("#F59E0B", "#FCD34D"),
                PROGRESS_STATUS_COMPLETED: ("#10B981", "#A7F3D0"),
                PROGRESS_STATUS_FAILED: ("#EF4444", "#FCA5A5"),
                PROGRESS_STATUS_CANCEL_FAILED: ("#EF4444", "#FCA5A5"),
                PROGRESS_STATUS_CANCELLED: ("#64748B", "#CBD5E1"),
            }
            badge_color, msg_color = color_map.get(snapshot.status, ("#2563EB", "#9FB0C7"))
            self.status_badge.setText(snapshot.status_label)
            self.status_badge.setStyleSheet(
                f"background: {badge_color}; color: #F8FBFF; border-radius: 11px; padding: 6px 10px;"
            )
            self.message_label.setStyleSheet(f"color: {msg_color}; font-size: 11px;")

        def _request_cancel(self) -> None:
            self.cancel_button.setEnabled(False)
            snapshot = TaskProgressSnapshot(
                task_id=self.snapshot.task_id,
                title=self.snapshot.title,
                previous_step=self.snapshot.previous_step,
                current_step=self.snapshot.current_step,
                next_step=self.snapshot.next_step,
                status=PROGRESS_STATUS_CANCELLING,
                current_index=self.snapshot.current_index,
                total_steps=self.snapshot.total_steps,
                status_message="正在向服务端发送取消请求...",
                can_cancel=False,
            )
            self.update_snapshot(snapshot)
            self._cancel_worker = threading.Thread(target=self._cancel_in_background, daemon=True)
            self._cancel_worker.start()

        def _cancel_in_background(self) -> None:
            result = self.api_client.cancel_task(self.snapshot.task_id)
            if result.success:
                snapshot = TaskProgressSnapshot(
                    task_id=self.snapshot.task_id,
                    title=self.snapshot.title,
                    previous_step=self.snapshot.previous_step,
                    current_step="任务已取消",
                    next_step="无",
                    status=PROGRESS_STATUS_CANCELLED,
                    current_index=self.snapshot.current_index,
                    total_steps=self.snapshot.total_steps,
                    status_message="取消请求已生效",
                    can_cancel=False,
                )
            else:
                snapshot = TaskProgressSnapshot(
                    task_id=self.snapshot.task_id,
                    title=self.snapshot.title,
                    previous_step=self.snapshot.previous_step,
                    current_step=self.snapshot.current_step,
                    next_step=self.snapshot.next_step,
                    status=PROGRESS_STATUS_CANCEL_FAILED,
                    current_index=self.snapshot.current_index,
                    total_steps=self.snapshot.total_steps,
                    status_message=f"取消失败: {result.message}",
                    can_cancel=True,
                )
            self.snapshot_received.emit(snapshot)

        def closeEvent(self, event):
            self.closed.emit()
            super().closeEvent(event)

    return TaskProgressWindow
