"""主入口交互卡片

服务启动时显示的任务入口卡片，包含：
- 任务输入区（文字 + 语音）
- 执行进展区
"""

from __future__ import annotations

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
        on_submit: Callable[[str], None],
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

    class MainEntryWindow(QtWidgets.QWidget):
        snapshot_received = QtCore.Signal(object)
        state_changed = QtCore.Signal(str)
        request_close = QtCore.Signal()
        start_countdown_requested = QtCore.Signal()  # 新增：请求启动倒计时信号

        def __init__(
            self,
            on_submit: Callable[[str], None],
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

            self._init_window()
            self._build_ui()

            # 连接信号
            self.snapshot_received.connect(self._handle_progress_update)
            self.state_changed.connect(self._handle_state_change)
            self.request_close.connect(self._safe_close)
            self.start_countdown_requested.connect(self._handle_start_countdown)  # 新增

        def _init_window(self) -> None:
            self.setWindowFlags(
                QtCore.Qt.Tool
                | QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.resize(450, 380)

            shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(34)
            shadow.setOffset(0, 10)
            shadow.setColor(QtGui.QColor(3, 12, 24, 180))
            self.setGraphicsEffect(shadow)

        def _build_ui(self) -> None:
            root_layout = QtWidgets.QVBoxLayout(self)
            root_layout.setContentsMargins(0, 0, 0, 0)

            shell = QtWidgets.QFrame()
            shell.setObjectName("MainCardShell")
            root_layout.addWidget(shell)

            layout = QtWidgets.QVBoxLayout(shell)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(12)

            # === 标题区 ===
            title_label = QtWidgets.QLabel("GUI Agent - 任务执行入口")
            title_label.setObjectName("CardTitle")
            layout.addWidget(title_label)

            # === 输入区 ===
            input_section = QtWidgets.QVBoxLayout()
            input_section.setSpacing(6)
            layout.addLayout(input_section)

            input_hint = QtWidgets.QLabel("任务指令：")
            input_hint.setObjectName("SectionHint")
            input_section.addWidget(input_hint)

            # 文本输入框
            self.text_input = QtWidgets.QTextEdit()
            self.text_input.setObjectName("TextInput")
            self.text_input.setPlaceholderText("输入任务指令，或使用语音输入...")
            self.text_input.setMaximumHeight(80)
            input_section.addWidget(self.text_input)

            # 按钮行（含倒计时光圈）
            button_row = QtWidgets.QHBoxLayout()
            button_row.setSpacing(8)
            input_section.addLayout(button_row)

            self.voice_button = QtWidgets.QPushButton("🎤 语音输入")
            self.voice_button.setObjectName("VoiceButton")
            self.voice_button.clicked.connect(self._toggle_voice_recording)
            button_row.addWidget(self.voice_button)

            # 倒计时光圈组件
            self.countdown_circle = CountdownCircle(duration=2000)
            self.countdown_circle.on_complete = self._on_countdown_complete
            button_row.addWidget(self.countdown_circle)

            # 倒计时提示文本
            self.countdown_hint = QtWidgets.QLabel()
            self.countdown_hint.setObjectName("CountdownHint")
            self.countdown_hint.setVisible(False)
            button_row.addWidget(self.countdown_hint, 1)

            self.submit_button = QtWidgets.QPushButton("✓ 确认执行")
            self.submit_button.setObjectName("SubmitButton")
            self.submit_button.clicked.connect(self._submit_task)
            button_row.addWidget(self.submit_button, 1)

            # === 分隔线 ===
            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.HLine)
            separator.setObjectName("Separator")
            layout.addWidget(separator)

            # === 进展区 ===
            progress_section = QtWidgets.QVBoxLayout()
            progress_section.setSpacing(6)
            layout.addLayout(progress_section)

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

            # === 底部状态提示 ===
            footer_row = QtWidgets.QHBoxLayout()
            footer_row.setSpacing(8)
            layout.addLayout(footer_row)

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

        def _build_step_card(self, title: str, highlighted: bool):
            frame = QtWidgets.QFrame()
            frame.setObjectName("CurrentStepFrame" if highlighted else "StepFrame")
            layout = QtWidgets.QVBoxLayout(frame)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(3)

            title_label = QtWidgets.QLabel(title)
            title_label.setObjectName("CurrentStepTitle" if highlighted else "StepTitle")
            layout.addWidget(title_label)

            text_label = QtWidgets.QLabel("暂无")
            text_label.setWordWrap(True)
            text_label.setObjectName("CurrentStepText" if highlighted else "StepText")
            layout.addWidget(text_label)

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
                    border-radius: 12px;
                    background: rgba(18, 32, 51, 0.96);
                    border: 1px solid rgba(93, 120, 158, 0.20);
                }
                #CurrentStepFrame {
                    border-radius: 12px;
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
            """

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

        def _move_to_bottom_right(self) -> None:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            self.move(
                geo.x() + geo.width() - self.width() - 22,
                geo.y() + geo.height() - self.height() - 26,
            )

        def _toggle_voice_recording(self) -> None:
            """切换语音录音状态"""
            if self._state == CardState.RECORDING:
                # 停止录音，启动倒计时自动执行
                text = self._asr_client.stop_recording()
                self._state = CardState.CONFIRMING
                self.voice_button.setText("🎤 语音输入")
                self.voice_button.setStyleSheet("")
                self.voice_button.setEnabled(False)
                self.cancel_button.setEnabled(True)  # 启用取消按钮

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
                    self.voice_button.setText("🔴 正在录音...")
                    self.voice_button.setStyleSheet("background: #EF4444;")
                    self.hint_label.setText("正在录音，说话结束后自动执行...")
            else:
                # IDLE 或其他状态：开始录音（带 final 回调）
                success = self._asr_client.start_recording(
                    callback=self._on_transcript_update,
                    on_final=self._on_transcript_final,
                )
                if success:
                    self._state = CardState.RECORDING
                    self.voice_button.setText("🔴 正在录音...")
                    self.voice_button.setStyleSheet("background: #EF4444;")
                    self.hint_label.setText("正在录音，说话结束后自动执行...")
                    self.voice_button.setStyleSheet("background: #EF4444;")
                    self.hint_label.setText("正在录音，点击停止...")
                else:
                    self.hint_label.setText("语音服务连接失败")

        def _on_transcript_update(self, text: str) -> None:
            """语音转写结果回调（实时更新）"""
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

            print(f"[MainEntryCard] 检测到静音，自动触发倒计时: {text[:30]}...")

            # 保存文本，发射信号在主线程处理
            self._countdown_text = text
            self.start_countdown_requested.emit()

        @QtCore.Slot()
        def _handle_start_countdown(self) -> None:
            """在主线程处理倒计时启动"""
            # 停止录音状态，进入倒计时
            self._state = CardState.CONFIRMING
            self.voice_button.setText("🎤 语音输入")
            self.voice_button.setStyleSheet("")
            self.voice_button.setEnabled(False)
            self.cancel_button.setEnabled(True)

            # 显示倒计时提示
            self.countdown_hint.setVisible(True)
            self.countdown_hint.setText(f"识别: {self._countdown_text[:30]}...")
            self.hint_label.setText("倒计时中，点击确认立即执行")

            # 启动倒计时光圈
            self.countdown_circle.start()

        def _submit_task(self) -> None:
            """提交任务（支持倒计时期间立即执行）"""
            # 停止 ASR 录音
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()

            # 如果在倒计时中，先停止倒计时
            if self._state == CardState.CONFIRMING:
                self.countdown_circle.stop()
                self.countdown_hint.setVisible(False)

            instruction = self.text_input.toPlainText().strip()
            if not instruction:
                self.hint_label.setText("请输入任务指令")
                self._reset_to_idle()
                return

            self._state = CardState.RUNNING
            self.text_input.setEnabled(False)
            self.submit_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.voice_button.setEnabled(False)
            self.hint_label.setText("任务执行中...")

            if self.on_submit:
                self.on_submit(instruction)

        def _on_countdown_complete(self) -> None:
            """倒计时完成，自动执行任务"""
            # 停止 ASR 录音
            if self._asr_client.is_recording:
                self._asr_client.stop_recording()

            self.countdown_hint.setVisible(False)
            instruction = self.text_input.toPlainText().strip()

            if not instruction:
                self._reset_to_idle()
                return

            self._state = CardState.RUNNING
            self.text_input.setEnabled(False)
            self.submit_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.voice_button.setEnabled(False)
            self.hint_label.setText("任务执行中...")

            if self.on_submit:
                self.on_submit(instruction)

        def _cancel_countdown(self) -> None:
            """取消倒计时，回到输入状态"""
            self.countdown_circle.stop()
            self.countdown_hint.setVisible(False)
            self._reset_to_idle()

        def _reset_to_idle(self) -> None:
            """重置到 IDLE 状态"""
            self._state = CardState.IDLE
            self.text_input.setEnabled(True)
            self.submit_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.voice_button.setEnabled(True)
            self.voice_button.setText("🎤 语音输入")
            self.voice_button.setStyleSheet("")
            self.hint_label.setText("等待任务输入")

        def _cancel_task(self) -> None:
            """取消任务（支持取消倒计时和运行中的任务）"""
            if self._state == CardState.CONFIRMING:
                # 取消倒计时
                self._cancel_countdown()
                self.hint_label.setText("已取消，可重新输入")
                return

            if self._state == CardState.RECORDING:
                self._asr_client.stop_recording()
                self._state = CardState.IDLE
                self.voice_button.setText("🎤 语音输入")
                self.voice_button.setStyleSheet("")

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

        def _handle_state_change(self, state: str) -> None:
            """处理状态变化"""
            self._state = CardState(state)

            if state == CardState.COMPLETED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                self.hint_label.setText("任务完成，可输入新任务")
                self.status_message.setStyleSheet("color: #10B981;")
            elif state == CardState.FAILED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                self.hint_label.setText("任务失败，可重新输入")
                self.status_message.setStyleSheet("color: #EF4444;")
            elif state == CardState.CANCELLED.value:
                self.text_input.setEnabled(True)
                self.submit_button.setEnabled(True)
                self.cancel_button.setEnabled(False)
                self.voice_button.setEnabled(True)
                self.hint_label.setText("任务已取消，可重新输入")
                self.status_message.setStyleSheet("color: #64748B;")

        def closeEvent(self, event):
            """关闭事件"""
            if self._state == CardState.RECORDING:
                self._asr_client.stop_recording()
            self._asr_client.disconnect()
            super().closeEvent(event)

        def _safe_close(self) -> None:
            """安全关闭窗口（通过信号调用）"""
            self.close()

    return MainEntryWindow