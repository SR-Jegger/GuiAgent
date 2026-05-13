"""Qwen ASR WebSocket 客户端 - 实时语音转文字

连接 Linux Qwen ASR WebSocket 服务，实时发送麦克风音频并接收识别结果。
"""

import json
import base64
import threading
import time
import signal
import sys
from websocket import WebSocketApp
import pyaudio


class AudioRecorder:
    """麦克风音频捕获"""

    def __init__(self, rate: int = 16000, chunk_size: int = 3200):
        self.rate = rate
        self.chunk_size = chunk_size
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.is_recording = False

    def start_recording(self, callback):
        """开始录音，实时回调音频数据"""
        def audio_callback(in_data, frame_count, time_info, status):
            if self.is_recording:
                callback(in_data)
            return (in_data, pyaudio.paContinue)

        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            stream_callback=audio_callback,
        )
        self.is_recording = True
        self.stream.start_stream()

    def stop_recording(self):
        """停止录音"""
        self.is_recording = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()


class QwenASRClient:
    """Qwen ASR WebSocket 客户端"""

    def __init__(self, server_url: str = "ws://192.168.137.2:8585/asr/stream"):
        self.server_url = server_url
        self.ws: WebSocketApp = None
        self.is_connected = False
        self.session_id = None
        self.full_transcript: list[str] = []

    def connect(self):
        """建立 WebSocket 连接"""
        self.ws = WebSocketApp(
            self.server_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        # 后台运行 WebSocket
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

    def _on_open(self, ws):
        """连接建立"""
        print("[连接] WebSocket 已连接")
        self.is_connected = True

        # 发送会话配置
        config = {
            "type": "session.update",
            "session": {
                "sample_rate": 16000,
                "silence_threshold": 0.01,
                "min_chunk_duration": 2.0,
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
                print(f"[会话] ID: {self.session_id}")

            elif msg_type == "transcript.update":
                transcript = data.get("transcript", "")
                if transcript:
                    print(f"[识别] {transcript}")
                    self.full_transcript.append(transcript)

            elif msg_type == "session.finished":
                full_text = data.get("full_transcript", "")
                print(f"[完成] 完整转写: {full_text}")

            elif msg_type == "error":
                print(f"[错误] {data.get('message')}")

        except Exception as e:
            print(f"[解析错误] {e}")

    def _on_error(self, ws, error):
        """WebSocket 错误"""
        print(f"[WebSocket 错误] {error}")
        self.is_connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        """连接关闭"""
        print(f"[关闭] WebSocket 已断开 (code={close_status_code})")
        self.is_connected = False

    def send_audio_chunk(self, audio_data: bytes):
        """发送音频数据块"""
        if self.is_connected and self.ws:
            # Base64 编码
            encoded = base64.b64encode(audio_data).decode("utf-8")
            message = {
                "type": "input_audio_buffer.append",
                "audio": encoded,
            }
            self.ws.send(json.dumps(message))

    def commit_buffer(self):
        """请求立即处理缓冲区"""
        if self.is_connected and self.ws:
            self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

    def clear_buffer(self):
        """清空缓冲区"""
        if self.is_connected and self.ws:
            self.ws.send(json.dumps({"type": "input_audio_buffer.clear"}))

    def close(self):
        """关闭连接"""
        if self.ws:
            self.ws.close()


class RealTimeASRSystem:
    """实时语音识别系统"""

    def __init__(self, server_url: str = "ws://192.168.137.2:8585/asr/stream"):
        self.recorder = AudioRecorder()
        self.client = QwenASRClient(server_url)
        self.is_running = False

    def start(self):
        """启动系统"""
        print("=" * 50)
        print("实时语音识别系统启动")
        print(f"服务地址: {self.client.server_url}")
        print("=" * 50)

        # 连接 WebSocket
        self.client.connect()

        # 等待连接建立
        time.sleep(2)

        if not self.client.is_connected:
            print("[错误] 无法连接到服务器")
            return

        # 定义音频回调
        def audio_callback(audio_data: bytes):
            if self.client.is_connected:
                self.client.send_audio_chunk(audio_data)

        # 开始录音
        self.recorder.start_recording(audio_callback)
        self.is_running = True

        print("[就绪] 开始说话吧... (Ctrl+C 退出)")

    def stop(self):
        """停止系统"""
        print("\n[停止] 正在停止...")
        self.is_running = False
        self.recorder.stop_recording()

        # 等待最后的结果
        time.sleep(1)
        self.client.close()

        # 打印完整结果
        print("\n" + "=" * 50)
        print("完整转写结果:")
        if self.client.full_transcript:
            for line in self.client.full_transcript:
                print(line)
        else:
            print("（无识别结果）")
        print("=" * 50)


def signal_handler(sig, frame):
    """信号处理"""
    print("\n收到退出信号")
    if "system" in globals():
        system.stop()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    system = RealTimeASRSystem(server_url="ws://192.168.137.2:8585/asr/stream")

    try:
        system.start()
        while system.is_running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        system.stop()