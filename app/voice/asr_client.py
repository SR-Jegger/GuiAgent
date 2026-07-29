"""实时语音转写客户端

连接 WebSocket ASR 服务，实现实时流式语音转写。
"""

import json
import base64
import threading
import time
from typing import Callable, Optional
from websocket import WebSocketApp
import pyaudio

# 默认配置（可从 model_config.json 覆盖）
DEFAULT_ASR_SERVER_URL = "ws://192.168.137.2:8585/asr/stream"


class RealtimeASRClient:
    """实时语音转写客户端"""

    def __init__(
        self,
        server_url: str = DEFAULT_ASR_SERVER_URL,
        sample_rate: int = 16000,
        chunk_size: int = 3200,
    ):
        self.server_url = server_url
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        self.ws: Optional[WebSocketApp] = None
        self.is_connected = False
        self.is_recording = False
        self.session_id: Optional[str] = None

        # 音频设备
        self.audio = pyaudio.PyAudio()
        self.stream = None

        # 转写结果回调
        self._transcript_callback: Optional[Callable[[str], None]] = None
        self._final_callback: Optional[Callable[[str], None]] = None  # 一句话结束回调

        # WebSocket 线程
        self._ws_thread: Optional[threading.Thread] = None

        # 累积的转写文本
        self._accumulated_text: str = ""

    def connect(self) -> bool:
        """建立 WebSocket 连接"""
        if self.is_connected:
            return True

        try:
            self.ws = WebSocketApp(
                self.server_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            self._ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
            self._ws_thread.start()

            # 等待连接建立
            time.sleep(1.5)
            return self.is_connected

        except Exception as e:
            print(f"[ASRClient] 连接失败: {e}")
            return False

    def _on_open(self, ws):
        """连接建立"""
        print("[ASRClient] WebSocket 已连接")
        self.is_connected = True

        # 发送会话配置
        config = {
            "type": "session.update",
            "session": {
                "sample_rate": self.sample_rate,
                "silence_threshold": 0.03,
                "min_chunk_duration": 0.8,
            },
        }
        ws.send(json.dumps(config))

    def _on_message(self, ws, message):
        """收到服务器消息"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "session.created":
                self.session_id = data.get("session_id")
                print(f"[ASRClient] 会话 ID: {self.session_id}")

            elif msg_type == "transcript.update":
                # 中间结果（保留兼容）
                transcript = data.get("transcript", "")
                if transcript:
                    self._accumulated_text += transcript
                    if self._transcript_callback:
                        self._transcript_callback(self._accumulated_text)

            elif msg_type == "transcript.final":
                # 一句话结束（静音超时触发）
                transcript = data.get("transcript", "")
                if transcript:
                    self._accumulated_text += transcript
                    # 先更新文本显示
                    if self._transcript_callback:
                        self._transcript_callback(self._accumulated_text)
                    # 触发 final 回调（自动倒计时）
                    if self._final_callback:
                        self._final_callback(self._accumulated_text)

            elif msg_type == "session.finished":
                full_text = data.get("full_transcript", "")
                if full_text and self._transcript_callback:
                    self._transcript_callback(full_text)

            elif msg_type == "error":
                print(f"[ASRClient] 错误: {data.get('message')}")

        except Exception as e:
            print(f"[ASRClient] 解析消息失败: {e}")

    def _on_error(self, ws, error):
        """WebSocket 错误"""
        print(f"[ASRClient] WebSocket 错误: {error}")
        self.is_connected = False
        self.is_recording = False

    def _on_close(self, ws, close_status_code, close_msg):
        """连接关闭"""
        print(f"[ASRClient] WebSocket 关闭 (code={close_status_code})")
        self.is_connected = False
        self.is_recording = False

    def start_recording(
        self,
        callback: Callable[[str], None],
        on_final: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """开始录音并实时转写

        Args:
            callback: 转写结果更新回调（实时显示）
            on_final: 一句话结束回调（静音检测触发，用于自动倒计时）
        """
        if self.is_recording:
            return False

        if not self.is_connected:
            if not self.connect():
                return False

        self._transcript_callback = callback
        self._final_callback = on_final
        self._accumulated_text = ""

        try:
            def audio_callback(in_data, frame_count, time_info, status):
                if self.is_recording and self.is_connected and self.ws:
                    # 发送音频数据
                    encoded = base64.b64encode(in_data).decode("utf-8")
                    self.ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": encoded,
                    }))
                return (in_data, pyaudio.paContinue)

            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=audio_callback,
            )

            self.is_recording = True
            self.stream.start_stream()
            print("[ASRClient] 开始录音")
            return True

        except Exception as e:
            print(f"[ASRClient] 录音失败: {e}")
            self.is_recording = False
            return False

    def stop_recording(self) -> str:
        """停止录音，返回累积的转写文本"""
        if not self.is_recording:
            return self._accumulated_text

        self.is_recording = False

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        # 请求处理剩余缓冲区
        if self.is_connected and self.ws:
            self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        print("[ASRClient] 停止录音")
        return self._accumulated_text

    def disconnect(self):
        """断开 WebSocket 连接"""
        self.stop_recording()

        if self.ws:
            self.ws.close()
            self.ws = None

        self.is_connected = False

    def close(self):
        """完全关闭客户端"""
        self.disconnect()
        self.audio.terminate()


# 模块级别的单例（可选）
_asr_client: Optional[RealtimeASRClient] = None


def get_asr_client() -> RealtimeASRClient:
    """获取 ASR 客户端单例"""
    global _asr_client
    if _asr_client is None:
        _asr_client = RealtimeASRClient()
    return _asr_client