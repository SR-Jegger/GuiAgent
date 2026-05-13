"""实时语音转文字服务 - 从麦克风捕获音频并实时转写"""

import queue
import threading
import time
import numpy as np
import sounddevice as sd
import requests
from pathlib import Path
import tempfile
import wave


class RealtimeTranscriber:
    """实时语音转写器"""

    def __init__(
        self,
        whisper_url: str = "http://192.168.137.2:8686/transcribe",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration: float = 3.0,
        silence_threshold: float = 0.02,
        min_speech_duration: float = 1.0,
    ):
        self.whisper_url = whisper_url
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_duration = chunk_duration
        self.silence_threshold = silence_threshold
        self.min_speech_duration = min_speech_duration

        # 音频缓冲队列
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.running = False

        # 转写结果
        self.full_transcript: list[str] = []

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """音频流回调函数"""
        if status:
            print(f"音频状态: {status}")
        self.audio_queue.put(indata.copy())

    def _capture_audio(self):
        """持续捕获麦克风音频"""
        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            callback=self._audio_callback,
        ):
            print("[麦克风] 正在监听...")
            while self.running:
                sd.sleep(100)

    def _save_audio_chunk(self, audio_data: np.ndarray) -> Path:
        """将音频数据保存为临时WAV文件"""
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_path = Path(temp_file.name)

        # 转换为16位整数格式
        audio_int16 = (audio_data * 32767).astype(np.int16)

        with wave.open(str(temp_path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        return temp_path

    def _transcribe_chunk(self, audio_path: Path) -> str | None:
        """发送音频块到Whisper API进行转写"""
        try:
            with open(audio_path, "rb") as f:
                response = requests.post(
                    self.whisper_url,
                    files={"file": (audio_path.name, f, "audio/wav")},
                    timeout=30,
                )

            if response.status_code == 200:
                result = response.json()
                return result.get("text", "")
            else:
                print(f"[错误] API返回: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"[错误] 请求失败: {e}")
            return None
        finally:
            # 清理临时文件
            audio_path.unlink(missing_ok=True)

    def _detect_speech(self, audio_data: np.ndarray) -> bool:
        """检测音频中是否有语音"""
        rms = np.sqrt(np.mean(audio_data**2))
        return rms > self.silence_threshold

    def _process_audio(self):
        """处理音频队列并转写"""
        audio_buffer: list[np.ndarray] = []
        buffer_duration = 0.0
        last_speech_time = 0.0

        while self.running:
            try:
                chunk = self.audio_queue.get(timeout=0.5)
                chunk_duration = len(chunk) / self.sample_rate

                has_speech = self._detect_speech(chunk)

                if has_speech:
                    audio_buffer.append(chunk)
                    buffer_duration += chunk_duration
                    last_speech_time = time.time()

                # 检查是否应该发送转写请求
                should_transcribe = False

                # 条件1: 缓冲达到最小语音时长
                if buffer_duration >= self.min_speech_duration:
                    # 条件2: 检测到静音超过chunk_duration
                    if not has_speech and (time.time() - last_speech_time) >= self.chunk_duration:
                        should_transcribe = True
                    # 条件3: 缓冲过长（强制发送）
                    elif buffer_duration >= self.chunk_duration * 3:
                        should_transcribe = True

                if should_transcribe and audio_buffer:
                    # 合并音频块
                    combined_audio = np.concatenate(audio_buffer, axis=0)

                    # 保存并发送转写
                    temp_path = self._save_audio_chunk(combined_audio)
                    print(f"[转写] 发送 {buffer_duration:.1f}秒音频...")

                    text = self._transcribe_chunk(temp_path)
                    if text:
                        print(f"[结果] {text}")
                        self.full_transcript.append(text)

                    # 重置缓冲
                    audio_buffer = []
                    buffer_duration = 0.0

            except queue.Empty:
                # 队列空，检查是否有待处理的语音
                if audio_buffer and buffer_duration >= self.min_speech_duration:
                    combined_audio = np.concatenate(audio_buffer, axis=0)
                    temp_path = self._save_audio_chunk(combined_audio)
                    print(f"[转写] 发送 {buffer_duration:.1f}秒音频（静音检测）...")

                    text = self._transcribe_chunk(temp_path)
                    if text:
                        print(f"[结果] {text}")
                        self.full_transcript.append(text)

                    audio_buffer = []
                    buffer_duration = 0.0

    def start(self):
        """启动实时转写服务"""
        print("=" * 50)
        print("实时语音转写服务启动")
        print(f"Whisper API: {self.whisper_url}")
        print(f"采样率: {self.sample_rate}Hz")
        print(f"语音检测阈值: {self.silence_threshold}")
        print("=" * 50)
        print("按 Ctrl+C 停止")

        self.running = True

        # 启动音频捕获线程
        capture_thread = threading.Thread(target=self._capture_audio)
        capture_thread.daemon = True
        capture_thread.start()

        # 主线程处理音频
        try:
            self._process_audio()
        except KeyboardInterrupt:
            print("\n[停止] 正在停止服务...")
            self.running = False
            capture_thread.join(timeout=2)

        # 打印完整转写结果
        print("\n" + "=" * 50)
        print("完整转写结果:")
        for line in self.full_transcript:
            print(line)
        print("=" * 50)

    def stop(self):
        """停止转写服务"""
        self.running = False


def main():
    """主入口"""
    transcriber = RealtimeTranscriber(
        whisper_url="http://192.168.137.2:8686/transcribe",
        sample_rate=16000,  # Whisper推荐采样率
        chunk_duration=3.0,  # 每次发送的音频长度
        silence_threshold=0.02,  # 语音检测阈值
        min_speech_duration=1.0,  # 最小语音片段长度
    )
    transcriber.start()


if __name__ == "__main__":
    main()