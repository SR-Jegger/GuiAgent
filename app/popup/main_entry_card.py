"""主入口交互卡片

服务启动时显示的任务入口卡片，包含：
- 任务输入区（文字 + 语音 + 预存指令）
- 执行进展区

支持 @path/to/task.md 语法：提交时把输入框里的 @路径引用展开为
对应 markdown 文件的原始全文（多行字符串），整体作为一个 instruction
发给后端。展开失败（文件不存在）时该 @path 被替换为空字符串。

来自 @path 展开的指令会带上 from_file=True 标记，后端据此跳过语义匹配，
直接把多行文本交给 task_decomposer 按行解析。
"""

from __future__ import annotations

import os
import re
import threading
from typing import Callable, Optional
from enum import Enum

from app.popup.task_progress import (
    PROGRESS_STATUS_CANCELLED,
    PROGRESS_STATUS_CANCELLING,
    PROGRESS_STATUS_COMPLETED,
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_PENDING,
    PROGRESS_STATUS_RUNNING,
    TaskProgressSnapshot,
    STATUS_LABELS,
)
from app.popup.preset_commands import create_preset_system, PRESETS


class CardState(str, Enum):
    """卡片状态"""
    IDLE = "idle"           # 空闲，等待输入
    RECORDING = "recording" # 语音录音中
    CONFIRMING = "confirming" # 倒计时确认中（语音输入后自动执行前）
    RUNNING = "running"     # 任务执行中
    COMPLETED = "completed" # 任务完成
    FAILED = "failed"       # 任务失败
    CANCELLED = "cancelled" # 任务取消


# 默认配置（可从 model_config.json 覆盖）
DEFAULT_ASR_SERVER_URL = "ws://192.168.137.2:4040/asr/stream"

_QT_RUNTIME = None
_QT_RUNTIME_LOCK = threading.Lock()


def _get_qt_runtime():
    """获取 Qt 运行时单例"""
    global _QT_RUNTIME
    with _QT_RUNTIME_LOCK:
        if _QT_RUNTIME is None:
            _QT_RUNTIME = _QtCardRuntime()
        return _QT_RUNTIME


class MainEntryCard:
    """主入口卡片代理类"""

    def __init__(
        self,
        on_submit: Callable[[str, bool], None],
        on_cancel: Optional[Callable[[], None]] = None,
        asr_server_url: str = DEFAULT_ASR_SERVER_URL,
    ):
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._asr_server_url = asr_server_url
        self._runtime = _get_qt_runtime()
        self._state = CardState.IDLE
        self._progress: Optional[TaskProgressSnapshot] = None

    def start(self) -> None:
        """显示卡片"""
        self._runtime.start_card(
            on_submit=self._on_submit,
            on_cancel=self._on_cancel,
            asr_server_url=self._asr_server_url,
        )

    def update_progress(self, snapshot: TaskProgressSnapshot) -> None:
        """更新进展显示"""
        self._progress = snapshot
        self._runtime.update_progress(snapshot)

    def set_state(self, state: CardState) -> None:
        """设置卡片状态"""
        self._state = state
        self._runtime.set_state(state)

    def show_confirmation(
        self,
        message: str,
        on_confirm: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        on_alternate: Optional[Callable[[], None]] = None,
    ) -> None:
        """弹出确认 UI（建议推进 X，确认吗？+ 确认/取消/换一个 三按钮）。

        线程安全：通过 signal marshal 到 Qt 线程。
        """
        self._runtime.show_confirmation(message, on_confirm, on_cancel, on_alternate)

    def close(self) -> None:
        """关闭卡片"""
        self._runtime.close_card()


class _QtCardRuntime:
    """Qt 运行时（运行在独立 UI 线程）"""

    def __init__(self):
        self._command_queue = []
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed = False
        self._failure_message = ""
        self._window = None
        self._window_params: Optional[tuple] = None  # 初始化属性
        self._app = None  # Qt Application 实例
        self._shutdown_requested = threading.Event()  # 关闭信号

    def start_card(self, on_submit, on_cancel, asr_server_url) -> None:
        self._ensure_thread()
        if self._failed:
            print(f"[MainEntryCard] Qt unavailable: {self._failure_message}")
            return
        self._window_params = (on_submit, on_cancel, asr_server_url)
        # 通知线程创建窗口
        self._ready.set()

    def update_progress(self, snapshot: TaskProgressSnapshot) -> None:
        if self._failed or not self._window:
            return
        # 通过 Qt 信号更新
        if hasattr(self._window, 'snapshot_received'):
            self._window.snapshot_received.emit(snapshot)

    def set_state(self, state: CardState) -> None:
        if self._failed or not self._window:
            return
        if hasattr(self._window, 'state_changed'):
            self._window.state_changed.emit(state.value)

    def show_confirmation(
        self,
        message: str,
        on_confirm: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        on_alternate: Optional[Callable[[], None]] = None,
    ) -> None:
        """通过 signal marshal 到 Qt 线程显示确认 UI。"""
        if self._failed or not self._window:
            return
        if hasattr(self._window, 'confirmation_requested'):
            self._window.confirmation_requested.emit(message, on_confirm, on_cancel, on_alternate)

    def close_card(self) -> None:
        """关闭卡片并终止 Qt 事件循环"""
        self._shutdown_requested.set()  # 设置关闭信号
        if self._window:
            # 通过信号安全关闭窗口
            if hasattr(self._window, 'request_close'):
                self._window.request_close.emit()
            else:
                self._window.close()
        # 等待线程结束（最多 2 秒）
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="qt-main-entry-card",
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

        WindowClass = _build_main_entry_window()

        app = QtWidgets.QApplication.instance()
        owns_app = app is None
        if owns_app:
            app = QtWidgets.QApplication([])
            app.setQuitOnLastWindowClosed(True)  # 窗口关闭时退出应用
        self._app = app

        self._ready.set()

        # 定时检查并创建窗口
        def check_and_create_window():
            if self._shutdown_requested.is_set():
                # 收到关闭信号，退出应用
                if owns_app:
                    app.quit()
                return

            if self._window is None and self._window_params is not None:
                on_submit, on_cancel, asr_url = self._window_params
                self._window = WindowClass(on_submit, on_cancel, asr_url)
                self._window.show_with_animation()

        timer = QtCore.QTimer()
        timer.timeout.connect(check_and_create_window)
        timer.start(100)  # 每100ms检查一次

        if owns_app:
            app.exec()
        else:
            # 非 owner 模式：等待关闭信号
            while not self._shutdown_requested.wait(0.1):
                pass


def _build_main_entry_window():
    """构建主入口窗口类"""
    from PySide6 import QtCore, QtGui, QtWidgets
    from app.voice.asr_client import RealtimeASRClient

    class CountdownCircle(QtWidgets.QWidget):
        """圆形倒计时光圈组件"""

        def __init__(self, duration: int = 2000, parent=None):
            super().__init__(parent)
            self.duration = duration  # 倒计时时长（毫秒）
            self._elapsed = 0
            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self._update_progress)
            self._start_time = 0
            self._running = False

            self.setFixedSize(60, 60)
            self.setVisible(False)

        @QtCore.Slot()
        def start(self) -> None:
            """启动倒计时"""
            self._elapsed = 0
            self._start_time = QtCore.QTime.currentTime()
            self._running = True
            self.setVisible(True)
            self._timer.start(30)  # 30ms 刷新

        @QtCore.Slot()
        def stop(self) -> None:
            """停止倒计时"""
            self._running = False
            self._timer.stop()
            self.setVisible(False)

        def _update_progress(self) -> None:
            """更新进度"""
            elapsed = self._start_time.msecsTo(QtCore.QTime.currentTime())
            self._elapsed = elapsed

            if elapsed >= self.duration:
                self._running = False
                self._timer.stop()
                self.setVisible(False)
                # 发送完成信号
                if hasattr(self, 'on_complete') and self.on_complete:
                    self.on_complete()

            self.update()  # 触发重绘

        def paintEvent(self, event):
            """绘制圆形光圈"""
            if not self._running:
                return

            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            # 计算进度
            progress = min(1.0, self._elapsed / self.duration)
            remaining = self.duration - self._elapsed
            seconds = max(0, remaining / 1000)

            # 绘制背景圆环
            center = QtCore.QPointF(30, 30)
            radius = 26

            bg_pen = QtGui.QPen(QtGui.QColor(100, 116, 139, 100), 4)
            painter.setPen(bg_pen)
            painter.drawEllipse(center, radius, radius)

            # 绘制进度圆环（渐变色）
            progress_angle = int(360 * progress)

            gradient = QtGui.QConicalGradient(center, -90)
            gradient.setColorAt(0, QtGui.QColor(59, 166, 217))
            gradient.setColorAt(0.5, QtGui.QColor(16, 185, 129))
            gradient.setColorAt(1, QtGui.QColor(59, 166, 217))

            progress_pen = QtGui.QPen(QtGui.QBrush(gradient), 4)
            progress_pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(progress_pen)

            rect = QtCore.QRectF(4, 4, 52, 52)
            painter.drawArc(rect, 90 * 16, -progress_angle * 16)

            # 绘制中心文字（倒计时秒数）
            painter.setPen(QtGui.QColor(248, 251, 255))
            font = QtGui.QFont()
            font.setPointSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QtCore.QRectF(0, 0, 60, 60), QtCore.Qt.AlignCenter, f"{seconds:.1f}")

    class _DockShell(QtWidgets.QFrame):
        """贴边抽屉壳：横向胶囊形，承载展开/语音两个图标按钮，支持纵向拖拽。

        边框颜色 + 动态环绕动画由 set_border 驱动，paintEvent 自定义绘制。
        动态环绕用 QObject.startTimer 驱动 rotation 角度，conic gradient 沿圆周旋转。
        """

        dragMoved = QtCore.Signal(QtCore.QPoint)
        dragReleased = QtCore.Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("DockShell")
            self.setFixedSize(100, 60)
            self._drag_offset: Optional[QtCore.QPoint] = None
            # 边框绘制参数
            self._border_color = QtGui.QColor("#9CA3AF")
            self._border_animated = False
            self._border_rotation_value = 0.0
            # QObject 内置 timer（不依赖单独 QObject 的线程亲和性）
            self._border_timer_id: int = -1

        def timerEvent(self, event):
            if self._border_animated:
                self._border_rotation_value = (self._border_rotation_value + 6) % 360
                self.update()
            super().timerEvent(event)

        def set_border(self, color: QtGui.QColor, animated: bool) -> None:
            """设置边框颜色，animated=True 时启用 conic gradient 旋转动画。"""
            self._border_color = color
            self._border_animated = animated
            if animated:
                if self._border_timer_id < 0:
                    self._border_timer_id = self.startTimer(30)
            else:
                if self._border_timer_id >= 0:
                    self.killTimer(self._border_timer_id)
                    self._border_timer_id = -1
            self.update()

        def paintEvent(self, event):
            super().paintEvent(event)  # 画 QSS 背景
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            # 边框画在 widget 内侧，留 2px 余量给阴影/抗锯齿
            rect = self.rect().adjusted(2, 2, -2, -2)
            if self._border_animated:
                # 动态环绕：亮蓝段沿圆周旋转
                # 0.0-0.3 是亮蓝段（占圆周 30%），其余是深蓝，随 rotation 旋转
                gradient = QtGui.QConicalGradient(rect.center(), self._border_rotation_value)
                gradient.setColorAt(0.0, QtGui.QColor("#93C5FD"))   # 亮蓝
                gradient.setColorAt(0.3, QtGui.QColor("#2563EB"))   # 深蓝
                gradient.setColorAt(1.0, QtGui.QColor("#93C5FD"))   # 循环回亮蓝
                pen = QtGui.QPen(QtGui.QBrush(gradient), 4)
                pen.setCapStyle(QtCore.Qt.RoundCap)
                painter.setPen(pen)
                painter.drawRoundedRect(rect, 12, 12)
            else:
                pen = QtGui.QPen(self._border_color, 2)
                painter.setPen(pen)
                painter.drawRoundedRect(rect, 12, 12)

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                # 只有点击在 shell 本身（非按钮子控件）才发起拖拽
                child = self.childAt(event.position().toPoint())
                if child is None or not isinstance(child, QtWidgets.QPushButton):
                    self._drag_offset = event.globalPosition().toPoint() - self.window().pos()

        def mouseMoveEvent(self, event):
            if self._drag_offset is not None:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.dragMoved.emit(new_pos)

        def mouseReleaseEvent(self, event):
            if self._drag_offset is not None:
                self._drag_offset = None
                self.dragReleased.emit()

    class MainEntryWindow(QtWidgets.QWidget):
        snapshot_received = QtCore.Signal(object)
        state_changed = QtCore.Signal(str)
        request_close = QtCore.Signal()
        start_countdown_requested = QtCore.Signal()  # 新增：请求启动倒计时信号
        confirmation_requested = QtCore.Signal(str, object, object, object)  # message, on_confirm, on_cancel, on_alternate

        def __init__(
            self,
            on_submit: Callable[[str, bool], None],
            on_cancel: Optional[Callable[[], None]],
            asr_server_url: str,
        ):
            super().__init__(None)
            self.on_submit = on_submit
            self.on_cancel = on_cancel
            self.asr_server_url = asr_server_url

            self._state = CardState.IDLE
            self._asr_client = RealtimeASRClient(server_url=asr_server_url)
            self._progress_snapshot: Optional[TaskProgressSnapshot] = None
            self._countdown_text: str = ""  # 倒计时显示文本

            # Create preset panel
            self._preset_btn = None
            self._preset_panel = None

            # Dock（贴边抽屉）状态
            self._docked: bool = True  # 默认以 dock 形态启动
            self._DOCK_W = 100
            self._DOCK_H = 60
            self._FULL_W = 380
            self._FULL_H = 400

            # 跨会话记忆 dock 纵向位置（相对屏幕可用高度的比例 0.0-1.0）
            self._settings = QtCore.QSettings("GuiAgent", "MainEntryCard")
            try:
                self._dock_y_ratio = float(self._settings.value("dock_y_ratio", 0.5))
            except (TypeError, ValueError):
                self._dock_y_ratio = 0.5
            self._dock_y_ratio = max(0.0, min(1.0, self._dock_y_ratio))

            # 确认 UI 回调临时存储
            self._confirm_callbacks: dict = {}

            self._init_window()
            self._build_ui()

            # 连接信号
            self.snapshot_received.connect(self._handle_progress_update)
            self.state_changed.connect(self._handle_state_change)
            self.request_close.connect(self._safe_close)
            self.start_countdown_requested.connect(self._handle_start_countdown)  # 新增
            self.confirmation_requested.connect(self._on_confirmation_requested)  # 确认 UI

        def _init_window(self) -> None:
            self.setWindowFlags(
                QtCore.Qt.Tool
                | QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            # 初始尺寸按 dock 形态；展开动画会扩到 _FULL_W x _FULL_H
            self.resize(self._DOCK_W, self._DOCK_H)

        def _build_ui(self) -> None:
            root_layout = QtWidgets.QVBoxLayout(self)
            root_layout.setContentsMargins(0, 0, 0, 0)

            # 用 QStackedWidget 在 dock / 主卡片之间切换（同一窗口、同一信号槽）
            self._stack = QtWidgets.QStackedWidget(self)
            root_layout.addWidget(self._stack)

            # === page 0: 主卡片 shell（原有内容）===
            shell = QtWidgets.QFrame()
            shell.setObjectName("MainCardShell")
            self._stack.addWidget(shell)
            self._main_shell = shell

            # 主卡片阴影
            main_shadow = QtWidgets.QGraphicsDropShadowEffect(shell)
            main_shadow.setBlurRadius(34)
            main_shadow.setOffset(0, 10)
            main_shadow.setColor(QtGui.QColor(3, 12, 24, 180))
            shell.setGraphicsEffect(main_shadow)

            layout = QtWidgets.QVBoxLayout(shell)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(12)

            # === 标题行（左边标题和状态，右边折叠按钮和倒计时）===
            title_row = QtWidgets.QHBoxLayout()
            layout.addLayout(title_row)

            title_label = QtWidgets.QLabel("GUI Agent")
            title_label.setObjectName("CardTitle")
            title_row.addWidget(title_label)

            # 折叠时的状态标签
            self.collapsed_status = QtWidgets.QLabel("")
            self.collapsed_status.setObjectName("CollapsedStatus")
            self.collapsed_status.setVisible(False)
            title_row.addWidget(self.collapsed_status)

            title_row.addStretch(1)

            # 折叠按钮（右上角，点击回到 dock 形态）
            self.collapse_button = QtWidgets.QPushButton("▼")
            self.collapse_button.setObjectName("CollapseButton")
            self.collapse_button.setFixedSize(28, 28)
            self.collapse_button.clicked.connect(self._toggle_dock)
            title_row.addWidget(self.collapse_button)

            # 倒计时光圈组件（右上角）
            self.countdown_circle = CountdownCircle(duration=2000)
            self.countdown_circle.on_complete = self._on_countdown_complete
            title_row.addWidget(self.countdown_circle)

            # 倒计时提示文本（右上角，光圈旁）
            self.countdown_hint = QtWidgets.QLabel()
            self.countdown_hint.setObjectName("CountdownHint")
            self.countdown_hint.setVisible(False)
            title_row.addWidget(self.countdown_hint)

            # === 可折叠内容区 ===
            self.collapsible_content = QtWidgets.QWidget()
            content_layout = QtWidgets.QVBoxLayout(self.collapsible_content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(12)
            layout.addWidget(self.collapsible_content)

            # === 输入区 ===
            input_section = QtWidgets.QVBoxLayout()
            input_section.setSpacing(6)
            content_layout.addLayout(input_section)

            input_hint = QtWidgets.QLabel("任务指令：")
            input_hint.setObjectName("SectionHint")
            input_section.addWidget(input_hint)

            # 文本输入框
            self.text_input = QtWidgets.QTextEdit()
            self.text_input.setObjectName("TextInput")
            self.text_input.setPlaceholderText("输入任务指令，或使用语音输入...")
            self.text_input.setMaximumHeight(80)
            input_section.addWidget(self.text_input)

            # === 三按钮均分一行 ===
            button_row = QtWidgets.QHBoxLayout()
            button_row.setSpacing(12)
            input_section.addLayout(button_row)

            # 预存指令按钮
            self._preset_btn, self._preset_panel = create_preset_system(
                self,
                on_select=lambda text: self.text_input.setText(text),
                presets=PRESETS,
            )
            button_row.addWidget(self._preset_btn, 1)

            self.voice_button = QtWidgets.QPushButton("🎤 语音输入")
            self.voice_button.setObjectName("VoiceButton")
            self.voice_button.clicked.connect(self._toggle_voice_recording)
            button_row.addWidget(self.voice_button, 1)

            self.submit_button = QtWidgets.QPushButton("✓ 确认执行")
            self.submit_button.setObjectName("SubmitButton")
            self.submit_button.clicked.connect(self._submit_task)
            button_row.addWidget(self.submit_button, 1)

            # === 分隔线 ===
            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.HLine)
            separator.setObjectName("Separator")
            content_layout.addWidget(separator)

            # === 进展区 ===
            progress_section = QtWidgets.QVBoxLayout()
            progress_section.setSpacing(6)
            content_layout.addLayout(progress_section)

            progress_hint = QtWidgets.QLabel("执行进展：")
            progress_hint.setObjectName("SectionHint")
            progress_section.addWidget(progress_hint)

            # 上一步卡片
            self.prev_card = self._build_step_card("上一步", False)
            progress_section.addWidget(self.prev_card["frame"])

            # 当前步骤卡片
            self.current_card = self._build_step_card("当前步骤", True)
            progress_section.addWidget(self.current_card["frame"])

            # 状态消息
            self.status_message = QtWidgets.QLabel()
            self.status_message.setObjectName("StatusMessage")
            self.status_message.setWordWrap(True)
            progress_section.addWidget(self.status_message)

            # === 确认 UI（模式 A 弹出：建议推进 X，确认吗？+ 3 按钮）===
            self._confirm_frame = QtWidgets.QFrame()
            self._confirm_frame.setObjectName("ConfirmFrame")
            confirm_layout = QtWidgets.QVBoxLayout(self._confirm_frame)
            confirm_layout.setContentsMargins(8, 8, 8, 8)
            confirm_layout.setSpacing(6)

            self._confirm_label = QtWidgets.QLabel("")
            self._confirm_label.setObjectName("ConfirmLabel")
            self._confirm_label.setWordWrap(True)
            self._confirm_label.setStyleSheet("color: #F59E0B; font-weight: bold;")
            confirm_layout.addWidget(self._confirm_label)

            confirm_btn_row = QtWidgets.QHBoxLayout()
            confirm_btn_row.setSpacing(6)
            for text, key, obj_name in [
                ("确认", "confirm", "ConfirmBtn"),
                ("换一个", "alternate", "AlternateBtn"),
                ("取消", "cancel", "CancelConfirmBtn"),
            ]:
                btn = QtWidgets.QPushButton(text)
                btn.setObjectName(obj_name)
                btn.clicked.connect(lambda checked, k=key: self._on_confirm_button(k))
                confirm_btn_row.addWidget(btn, 1)
            confirm_layout.addLayout(confirm_btn_row)
            self._confirm_frame.hide()
            progress_section.addWidget(self._confirm_frame)

            # === 底部状态提示行（左状态，右取消按钮）===
            footer_row = QtWidgets.QHBoxLayout()
            footer_row.setSpacing(8)
            content_layout.addLayout(footer_row)

            self.hint_label = QtWidgets.QLabel("等待任务输入")
            self.hint_label.setObjectName("FooterHint")
            footer_row.addWidget(self.hint_label, 1)

            self.cancel_button = QtWidgets.QPushButton("取消任务")
            self.cancel_button.setObjectName("CancelButton")
            self.cancel_button.clicked.connect(self._cancel_task)
            self.cancel_button.setEnabled(False)
            footer_row.addWidget(self.cancel_button)

            # === 样式 ===
            self.setStyleSheet(self._get_stylesheet())

            # === page 1: dock shell（贴边抽屉，两个图标按钮 + 状态色点）===
            self._dock_shell = _DockShell(self)
            self._stack.addWidget(self._dock_shell)

            # dock 阴影
            dock_shadow = QtWidgets.QGraphicsDropShadowEffect(self._dock_shell)
            dock_shadow.setBlurRadius(20)
            dock_shadow.setOffset(0, 4)
            dock_shadow.setColor(QtGui.QColor(3, 12, 24, 160))
            self._dock_shell.setGraphicsEffect(dock_shadow)

            # dock 内部布局：两个 40x40 图标按钮水平并列，垂直居中
            dock_layout = QtWidgets.QHBoxLayout(self._dock_shell)
            dock_layout.setContentsMargins(8, 8, 8, 8)
            dock_layout.setSpacing(8)

            self._dock_expand_btn = QtWidgets.QPushButton("☰")
            self._dock_expand_btn.setObjectName("DockExpandButton")
            self._dock_expand_btn.setFixedSize(40, 40)
            self._dock_expand_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self._dock_expand_btn.clicked.connect(self._toggle_dock)
            dock_layout.addWidget(self._dock_expand_btn)

            self._dock_voice_btn = QtWidgets.QPushButton("🎤")
            self._dock_voice_btn.setObjectName("DockVoiceButton")
            self._dock_voice_btn.setFixedSize(40, 40)
            self._dock_voice_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self._dock_voice_btn.clicked.connect(self._toggle_voice_recording)
            dock_layout.addWidget(self._dock_voice_btn)

            # dock 拖拽信号路由到主窗口
            self._dock_shell.dragMoved.connect(self._on_dock_drag_moved)
            self._dock_shell.dragReleased.connect(self._save_dock_position)

            # QStackedWidget 的 sizeHint 取所有 page 最大值，会推大窗口；
            # 用固定 size 强制 dock 形态的初始尺寸
            self.setFixedSize(self._DOCK_W, self._DOCK_H)

            # 初始展示 dock 形态
            self._stack.setCurrentWidget(self._dock_shell)
            self._update_dock_border()

        def _build_step_card(self, title: str, highlighted: bool):
            frame = QtWidgets.QFrame()
            frame.setObjectName("CurrentStepFrame" if highlighted else "StepFrame")
            layout = QtWidgets.QHBoxLayout(frame)
            layout.setContentsMargins(12, 6, 12, 6)
            layout.setSpacing(8)

            title_label = QtWidgets.QLabel(title)
            title_label.setObjectName("CurrentStepTitle" if highlighted else "StepTitle")
            layout.addWidget(title_label, 0)

            text_label = QtWidgets.QLabel("暂无")
            text_label.setWordWrap(True)
            text_label.setObjectName("CurrentStepText" if highlighted else "StepText")
            layout.addWidget(text_label, 1)

            return {"frame": frame, "text": text_label}

        def _get_stylesheet(self) -> str:
            return """
                #MainCardShell {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(11,18,32,245),
                        stop:1 rgba(19,31,52,242));
                    border: 1px solid rgba(91, 131, 177, 0.35);
                    border-radius: 18px;
                }
                #CardTitle {
                    color: #F8FBFF;
                    font-size: 15px;
                    font-weight: 700;
                }
                #SectionHint {
                    color: #7DD3FC;
                    font-size: 11px;
                }
                #TextInput {
                    background: rgba(18, 32, 51, 0.96);
                    border: 1px solid rgba(93, 120, 158, 0.25);
                    border-radius: 10px;
                    color: #F8FBFF;
                    font-size: 12px;
                    padding: 8px;
                }
                #TextInput:focus {
                    border: 1px solid rgba(59, 166, 217, 0.85);
                }
                #VoiceButton {
                    background: #2563EB;
                    color: #F8FBFF;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 11px;
                    font-weight: 600;
                }
                #VoiceButton:hover {
                    background: #3B82F6;
                }
                #VoiceButton:checked {
                    background: #EF4444;
                }
                #SubmitButton {
                    background: #10B981;
                    color: #F8FBFF;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 16px;
                    font-size: 11px;
                    font-weight: 700;
                }
                #SubmitButton:hover {
                    background: #059669;
                }
                #SubmitButton:disabled {
                    background: rgba(100, 116, 139, 0.45);
                }
                #Separator {
                    background: rgba(91, 131, 177, 0.25);
                    max-height: 1px;
                }
                #StepFrame {
                    border-radius: 10px;
                    background: rgba(18, 32, 51, 0.96);
                    border: 1px solid rgba(93, 120, 158, 0.20);
                }
                #CurrentStepFrame {
                    border-radius: 10px;
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
                    font-size: 11px;
                    font-weight: 600;
                }
                #StatusMessage {
                    color: #9FB0C7;
                    font-size: 10px;
                }
                #FooterHint {
                    color: #7F93AD;
                    font-size: 10px;
                }
                #CountdownHint {
                    color: #10B981;
                    font-size: 11px;
                    font-weight: 600;
                }
                #CancelButton {
                    background: #2563EB;
                    color: #F8FBFF;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 11px;
                    font-weight: 600;
                }
                #CancelButton:hover {
                    background: #3B82F6;
                }
                #CancelButton:disabled {
                    background: rgba(100, 116, 139, 0.45);
                    color: #D0D7E5;
                }
                #CollapseButton {
                    background: rgba(59, 166, 217, 0.35);
                    color: #F8FBFF;
                    border: none;
                    border-radius: 14px;
                    font-size: 12px;
                    font-weight: 600;
                }
                #CollapseButton:hover {
                    background: rgba(59, 166, 217, 0.6);
                }
                #CollapsedStatus {
                    color: #10B981;
                    font-size: 11px;
                    font-weight: 600;
                    margin-left: 8px;
                }
                #DockShell {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 rgba(11,18,32,245),
                        stop:1 rgba(19,31,52,242));
                    border-radius: 14px;
                }
                #DockExpandButton, #DockVoiceButton {
                    background: rgba(59, 166, 217, 0.25);
                    color: #F8FBFF;
                    border: none;
                    border-radius: 10px;
                    font-size: 18px;
                }
                #DockExpandButton:hover, #DockVoiceButton:hover {
                    background: rgba(59, 166, 217, 0.5);
                }
                #DockCountdownLabel {
                    color: #F59E0B;
                    font-size: 11px;
                    font-weight: 700;
                    qproperty-alignment: AlignCenter;
                }
            """

        def show_with_animation(self) -> None:
            self.setWindowOpacity(0.0)
            self.show()
            if self._docked:
                self._move_dock_to_edge()
            else:
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

        def _move_to_bottom_right(self) -> None:
            """主卡片形态的兜底定位（启动时若不是 dock 态走这里）。"""
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            self.resize(self._FULL_W, self._FULL_H)
            self.move(
                geo.x() + geo.width() - self.width() - 22,
                geo.y() + geo.height() - self.height() - 26,
            )

        def _toggle_voice_recording(self) -> None:
            """切换语音录音状态"""
            if self._state == CardState.RECORDING:
                # 停止录音，启动倒计时自动执行
                text = self._asr_client.stop_recording()
                # 纠正 ASR 同音错字（数字标识命中 cache 时用真实 target_id 汉字部分替换）
                text = self._correct_asr_text(text)
                self._state = CardState.CONFIRMING
                self._set_dock_voice_recording(False)
                self._update_dock_border()
                self.voice_button.setText("🎤 语音输入")
                self.voice_button.setStyleSheet("background: #2563EB;")
                self.voice_button.setEnabled(False)
                self.cancel_button.setEnabled(True)  # 启用取消按钮
                if self._preset_btn:
                    self._preset_btn.setEnabled(False)

                # 纠正后文本写回输入框，让用户看到准确结果（也作为提交时的指令）
                self.text_input.setText(text)
                # 显示倒计时提示
                self.countdown_hint.setVisible(True)
                self.countdown_hint.setText(f"识别结果: {text[:30]}...")
                self.hint_label.setText("倒计时中，点击确认立即执行")

                # 启动倒计时光圈
                self.countdown_circle.start()
            elif self._state == CardState.CONFIRMING:
                # 倒计时期间点击语音按钮，取消倒计时重新录音
                self._cancel_countdown()
                # 开始新录音（带 final 回调）
                success = self._asr_client.start_recording(
                    callback=self._on_transcript_update,
                    on_final=self._on_transcript_final,
                )
                if success:
                    self._state = CardState.RECORDING
                    self._update_dock_border()
                    self.voice_button.setText("🔴 正在录音...")
                    self.voice_button.setStyleSheet("background: #EF4444;")
                    # RECORDING 期间不应允许取消按钮（没有运行中的任务可取消）
                    self.cancel_button.setEnabled(False)
                    if self._preset_btn:
                        self._preset_btn.setEnabled(False)
                    self.hint_label.setText("正在录音，说话结束后自动执行...")
                    self._set_dock_voice_recording(True)
            else:
                # IDLE 或其他状态：开始录音（带 final 回调）
                success = self._asr_client.start_recording(
                    callback=self._on_transcript_update,
                    on_final=self._on_transcript_final,
                )
                if success:
                    self._state = CardState.RECORDING
                    self._update_dock_border()
                    self.voice_button.setText("🔴 正在录音...")
                    self.voice_button.setStyleSheet("background: #EF4444;")
                    if self._preset_btn:
                        self._preset_btn.setEnabled(False)
                    self.hint_label.setText("正在录音，点击停止...")
                    self._set_dock_voice_recording(True)
                else:
                    self.hint_label.setText("语音服务连接失败")
                    if self._docked:
                        self._flash_dock_border_red()

        def _on_transcript_update(self, text: str) -> None:
            """语音转写结果回调（实时更新）"""
            # 只有在录音状态才允许写文本框，避免倒计时/执行期间残留的
            # transcript.update 把已确认的指令文本覆盖掉
            if self._state != CardState.RECORDING:
                return
            # 更新文本框（在主线程）
            QtCore.QMetaObject.invokeMethod(
                self.text_input,
                "setText",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, text),
            )

        def _on_transcript_final(self, text: str) -> None:
            """一句话结束回调（静音检测触发，自动启动倒计时）"""
            # 只有在录音状态才处理
            if self._state != CardState.RECORDING:
                return

            # 纠正 ASR 同音错字（数字标识命中 cache 时用真实 target_id 汉字部分替换）
            text = self._correct_asr_text(text)
            print(f"[MainEntryCard] 检测到静音，自动触发倒计时: {text[:30]}...")

            # 保存文本，发射信号在主线程处理
            self._countdown_text = text
            self.start_countdown_requested.emit()

        @QtCore.Slot()
        def _handle_start_countdown(self) -> None:
            """在主线程处理倒计时启动"""
            # 立刻停止录音：避免倒计时期间音频继续推送到服务端，
            # 导致新的 transcript.update/final 把输入框文本覆盖掉
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()

            # dock 形态下自动展开：用户从 dock 启动语音后需看到识别结果与倒计时
            if self._docked:
                self._toggle_dock()

            # 停止录音状态，进入倒计时
            self._state = CardState.CONFIRMING
            self._set_dock_voice_recording(False)
            self._update_dock_border()
            self.voice_button.setText("🎤 语音输入")
            self.voice_button.setStyleSheet("background: #2563EB;")
            self.voice_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            if self._preset_btn:
                self._preset_btn.setEnabled(False)

            # 纠正后文本已在 _on_transcript_final 写入 _countdown_text，
            # 这里写回输入框覆盖实时转写的错字版本
            text = self._countdown_text
            self.text_input.setText(text)
            # 显示倒计时提示
            self.countdown_hint.setVisible(True)
            self.countdown_hint.setText(f"识别: {text[:30]}...")
            self.hint_label.setText("倒计时中，点击确认立即执行")

            # 启动倒计时光圈
            self.countdown_circle.start()

        def _correct_asr_text(self, text: str) -> str:
            """纠正 ASR 同音错字：用 cache 真实 target_id 替换 text 里的同音汉字部分。

            纯字符串操作，<1ms。cache 未就绪或未命中时原样返回。
            异常时原样返回，不影响主流程。
            """
            if not text:
                return text
            try:
                from utils.kill_chain_cache import correct_target_id_homophone
                return correct_target_id_homophone(text)
            except Exception as e:
                print(f"[MainEntryCard] ASR 文本纠正失败: {e}，原样返回")
                return text

        def _resolve_instruction(self) -> tuple[str, str, bool]:
            """从输入框取文本，展开 @path/to/file.md 引用。

            @path 语法: @后跟路径，路径字符为非空白、非@。
            支持相对路径（相对当前工作目录）和绝对路径。
            多个 @path 都会展开；展开失败的引用替换为空字符串。

            Returns:
                (展开后的指令文本, 错误提示, from_file)。
                错误提示非空时表示有 @path 文件读不到，调用方应中止提交并提示。
                from_file 为 True 表示指令来自 @path 文件展开，后端应跳过语义匹配。
            """
            text = self.text_input.toPlainText().strip()
            if not text:
                return "", "", False

            # 没有任何 @path 引用，直接返回原文
            if "@" not in text:
                return text, "", False

            missing: list[str] = []
            expanded_any = False

            def _expand(match: re.Match) -> str:
                nonlocal expanded_any
                raw = match.group(1)
                # 去掉首尾可能的中文标点或常见分隔符（路径不应包含这些）
                path = raw.strip()
                if not path:
                    return ""
                if not os.path.exists(path):
                    missing.append(path)
                    return ""
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        expanded_any = True
                        return f.read()
                except Exception as exc:
                    missing.append(f"{path} ({exc})")
                    return ""

            # @path 匹配：@后跟非空白、非@的字符
            expanded = re.sub(r"@([^\s@]+)", _expand, text)

            if missing:
                return "", f"无法读取文件: {', '.join(missing)}", False

            return expanded.strip(), "", expanded_any

        def _submit_task(self) -> None:
            """提交任务（支持倒计时期间立即执行）"""
            # 停止 ASR 录音
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()

            # 如果在倒计时中，先停止倒计时
            if self._state == CardState.CONFIRMING:
                self.countdown_circle.stop()
                self.countdown_hint.setVisible(False)

            instruction, error, from_file = self._resolve_instruction()
            if error:
                self.hint_label.setText(error)
                self._reset_to_idle()
                return
            if not instruction:
                self.hint_label.setText("请输入任务指令")
                self._reset_to_idle()
                return

            self._state = CardState.RUNNING
            self._update_dock_border()
            self.text_input.setEnabled(False)
            self.submit_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.voice_button.setEnabled(False)
            if self._preset_btn:
                self._preset_btn.setEnabled(False)
            self.hint_label.setText("任务执行中...")

            if self.on_submit:
                self.on_submit(instruction, from_file)

        def _on_countdown_complete(self) -> None:
            """倒计时完成，自动执行任务"""
            # 停止 ASR 录音
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()

            self.countdown_hint.setVisible(False)
            instruction, error, from_file = self._resolve_instruction()
            if error:
                self.hint_label.setText(error)
                self._reset_to_idle()
                return
            if not instruction:
                self._reset_to_idle()
                return

            self._state = CardState.RUNNING
            self._update_dock_border()
            self.text_input.setEnabled(False)
            self.submit_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.voice_button.setEnabled(False)
            if self._preset_btn:
                self._preset_btn.setEnabled(False)
            self.hint_label.setText("任务执行中...")

            if self.on_submit:
                self.on_submit(instruction, from_file)

        def _cancel_countdown(self) -> None:
            """取消倒计时，回到输入状态"""
            self.countdown_circle.stop()
            self.countdown_hint.setVisible(False)
            self._reset_to_idle()

        def _reset_to_idle(self) -> None:
            """重置到 IDLE 状态"""
            self._state = CardState.IDLE
            self._set_dock_voice_recording(False)
            self._update_dock_border()
            self.text_input.setEnabled(True)
            self.submit_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.voice_button.setEnabled(True)
            self.voice_button.setText("🎤 语音输入")
            self.voice_button.setStyleSheet("background: #2563EB;")
            if self._preset_btn:
                self._preset_btn.setEnabled(True)
            self.hint_label.setText("等待任务输入")

        def _cancel_task(self) -> None:
            """取消任务（支持取消倒计时和运行中的任务）"""
            if self._state == CardState.CONFIRMING:
                # 取消倒计时
                self._cancel_countdown()
                self.hint_label.setText("已取消，可重新输入")
                return

            if self._state == CardState.RECORDING:
                # 录音中取消：只停录音，不触发任务取消回调（此时没有运行中的任务）
                self._asr_client.stop_recording()
                self._state = CardState.IDLE
                self._set_dock_voice_recording(False)
                self._update_dock_border()
                self.voice_button.setText("🎤 语音输入")
                self.voice_button.setStyleSheet("background: #2563EB;")
                self.cancel_button.setEnabled(False)
                self.hint_label.setText("已取消录音")
                return

            if self.on_cancel:
                self.on_cancel()

            self.cancel_button.setEnabled(False)
            self.hint_label.setText("任务已取消")

        def _handle_progress_update(self, snapshot: TaskProgressSnapshot) -> None:
            """处理进展更新"""
            self._progress_snapshot = snapshot
            self.prev_card["text"].setText(snapshot.previous_step or "暂无")
            self.current_card["text"].setText(snapshot.current_step or "暂无")
            self.status_message.setText(snapshot.status_message or "")

            # 根据状态更新 UI
            status = snapshot.status
            if status == PROGRESS_STATUS_RUNNING:
                self.hint_label.setText(f"步骤 {snapshot.current_index}/{snapshot.total_steps}")
            elif status == PROGRESS_STATUS_COMPLETED:
                self._handle_state_change(CardState.COMPLETED.value)
            elif status == PROGRESS_STATUS_FAILED:
                self._handle_state_change(CardState.FAILED.value)
            elif status == PROGRESS_STATUS_CANCELLED:
                self._handle_state_change(CardState.CANCELLED.value)

        def _on_confirmation_requested(self, message, on_confirm, on_cancel, on_alternate) -> None:
            """显示确认 UI（由 confirmation_requested signal 触发，在 Qt 线程执行）。"""
            self._confirm_callbacks = {
                "confirm": on_confirm,
                "cancel": on_cancel,
                "alternate": on_alternate,
            }
            self._confirm_label.setText(message)
            self._confirm_frame.show()
            self.status_message.setText("等待用户确认...")

        def _on_confirm_button(self, key: str) -> None:
            """确认/取消/换一个 按钮点击处理。"""
            cb = self._confirm_callbacks.pop(key, None)
            # 清空其他回调（一次只响应一个按钮）
            self._confirm_callbacks = {}
            self._confirm_frame.hide()
            self.status_message.setText("")
            if cb:
                try:
                    cb()
                except Exception as e:
                    print(f"[MainEntryCard] 确认回调异常: {e}")

        def _handle_state_change(self, state: str) -> None:
            """处理状态变化"""
            self._state = CardState(state)
            self._update_dock_border()

            if state == CardState.COMPLETED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                if self._preset_btn:
                    self._preset_btn.setEnabled(True)
                self.hint_label.setText("任务完成，可输入新任务")
                self.status_message.setStyleSheet("color: #10B981;")
            elif state == CardState.FAILED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                if self._preset_btn:
                    self._preset_btn.setEnabled(True)
                self.hint_label.setText("任务失败，可重新输入")
                self.status_message.setStyleSheet("color: #EF4444;")
            elif state == CardState.CANCELLED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                if self._preset_btn:
                    self._preset_btn.setEnabled(True)
                self.hint_label.setText("任务已取消，可重新输入")
                self.status_message.setStyleSheet("color: #64748B;")

        def closeEvent(self, event):
            """关闭事件"""
            # 持久化 dock 纵向位置
            self._save_dock_position()
            # 用 is_recording 而非状态判断，覆盖 RECORDING/CONFIRMING 任何残留录音
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()
            self._asr_client.disconnect()
            super().closeEvent(event)

        def _toggle_dock(self) -> None:
            """在 dock 形态与主卡片形态之间切换。"""
            if self._docked:
                # 展开：dock -> 主卡片
                self._docked = False
                dock_pos = self.pos()
                self._stack.setCurrentWidget(self._main_shell)
                self._update_dock_border()
                self._animate_dock_transition(
                    from_size=QtCore.QSize(self._DOCK_W, self._DOCK_H),
                    to_size=QtCore.QSize(self._FULL_W, self._FULL_H),
                    from_pos=dock_pos,
                    to_pos=self._compute_main_pos_from_dock(dock_pos),
                    fade_target=self._main_shell,
                )
            else:
                # 收起：主卡片 -> dock
                self._docked = True
                main_pos = self.pos()
                self._stack.setCurrentWidget(self._dock_shell)
                self._update_dock_border()
                self._animate_dock_transition(
                    from_size=QtCore.QSize(self._FULL_W, self._FULL_H),
                    to_size=QtCore.QSize(self._DOCK_W, self._DOCK_H),
                    from_pos=main_pos,
                    to_pos=self._compute_dock_pos_from_ratio(),
                    fade_target=None,
                )

        def _compute_dock_pos_from_ratio(self) -> QtCore.QPoint:
            """根据 _dock_y_ratio 算 dock 应贴的屏幕坐标。"""
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return QtCore.QPoint(0, 0)
            geo = screen.availableGeometry()
            x = geo.x() + geo.width() - self._DOCK_W - 12
            usable_h = max(1, geo.height() - self._DOCK_H)
            y = geo.y() + int(usable_h * self._dock_y_ratio)
            return QtCore.QPoint(x, y)

        def _compute_main_pos_from_dock(self, dock_pos: QtCore.QPoint) -> QtCore.QPoint:
            """主卡片右边贴 dock 左边（重叠 12px），垂直中心对齐 dock 中心。"""
            screen = QtGui.QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else None
            target_x = dock_pos.x() - self._FULL_W + 12
            target_y = dock_pos.y() + (self._DOCK_H - self._FULL_H) // 2
            if geo is not None:
                target_x = max(geo.x() + 4, target_x)
                target_y = max(geo.y() + 4, min(geo.y() + geo.height() - self._FULL_H - 4, target_y))
            return QtCore.QPoint(target_x, target_y)

        def _animate_dock_transition(
            self,
            from_size: QtCore.QSize,
            to_size: QtCore.QSize,
            from_pos: QtCore.QPoint,
            to_pos: QtCore.QPoint,
            fade_target: Optional[QtWidgets.QWidget],
        ) -> None:
            """dock <-> 主卡片切换的尺寸+位置+淡入动画。"""
            # 解除 setFixedSize 锁定，让 QPropertyAnimation 能改变 size
            self.setMinimumSize(0, 0)
            self.setMaximumSize(10000, 10000)

            group = QtCore.QParallelAnimationGroup(self)

            size_anim = QtCore.QPropertyAnimation(self, b"size", self)
            size_anim.setDuration(220)
            size_anim.setStartValue(from_size)
            size_anim.setEndValue(to_size)
            size_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            group.addAnimation(size_anim)

            pos_anim = QtCore.QPropertyAnimation(self, b"pos", self)
            pos_anim.setDuration(220)
            pos_anim.setStartValue(from_pos)
            pos_anim.setEndValue(to_pos)
            pos_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            group.addAnimation(pos_anim)

            if fade_target is not None:
                fade_target.setWindowOpacity(0.0)
                fade_anim = QtCore.QPropertyAnimation(fade_target, b"windowOpacity", self)
                fade_anim.setDuration(220)
                fade_anim.setStartValue(0.0)
                fade_anim.setEndValue(1.0)
                group.addAnimation(fade_anim)

            # 动画结束后用 setFixedSize 锁定终态，防止 layout 反向推大窗口
            group.finished.connect(lambda: self.setFixedSize(to_size))
            group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

        def _on_dock_drag_moved(self, new_pos: QtCore.QPoint) -> None:
            """dock 拖拽中：x 固定贴右边缘，y clamp 到屏幕内。"""
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            x = geo.x() + geo.width() - self._DOCK_W - 12
            y = new_pos.y()
            y = max(geo.y() + 4, min(geo.y() + geo.height() - self._DOCK_H - 4, y))
            self.move(x, y)

        def _save_dock_position(self) -> None:
            """拖拽释放 / 关闭时把当前 dock y 存成 ratio。"""
            if not self._docked:
                return
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            usable_h = max(1, geo.height() - self._DOCK_H)
            ratio = (self.pos().y() - geo.y()) / usable_h
            self._dock_y_ratio = max(0.0, min(1.0, ratio))
            self._settings.setValue("dock_y_ratio", self._dock_y_ratio)

        def _move_dock_to_edge(self) -> None:
            """启动时把 dock 放到记忆位置。"""
            self.move(self._compute_dock_pos_from_ratio())

        def _update_dock_border(self) -> None:
            """根据 CardState 更新 dock 边框颜色与是否启用动态环绕。

            RECORDING 蓝色动态环绕；其他状态静态纯色边框。
            RUNNING 时 dock 不可见（CONFIRMING 已自动展开），颜色仅作兜底。
            """
            state_border = {
                CardState.IDLE: ("#9CA3AF", False),
                CardState.RECORDING: ("#3B82F6", True),
                CardState.CONFIRMING: ("#F59E0B", False),
                CardState.RUNNING: ("#3B82F6", False),
                CardState.COMPLETED: ("#10B981", False),
                CardState.FAILED: ("#EF4444", False),
                CardState.CANCELLED: ("#64748B", False),
            }
            color_hex, animated = state_border.get(self._state, ("#9CA3AF", False))
            self._dock_shell.set_border(QtGui.QColor(color_hex), animated)

        def _set_dock_voice_recording(self, recording: bool) -> None:
            """dock 形态下语音键颜色切换：录音中红，非录音蓝。"""
            if recording:
                self._dock_voice_btn.setStyleSheet("background: #EF4444;")
            else:
                self._dock_voice_btn.setStyleSheet("background: rgba(59, 166, 217, 0.25);")

        def _flash_dock_border_red(self) -> None:
            """ASR 连接失败时，dock 边框短暂变红 200ms 后恢复当前状态色。"""
            self._dock_shell.set_border(QtGui.QColor("#EF4444"), False)
            QtCore.QTimer.singleShot(200, self._update_dock_border)

        def _safe_close(self) -> None:
            """安全关闭窗口（通过信号调用）"""
            self.close()

    return MainEntryWindow