"""Qwen ASR WebSocket 流式语音识别服务 v2

重构版:事件驱动静音检测、多客户端并发、空文本不发 final、
短音频按有无语音决定丢弃或 padding 识别。
"""

import asyncio
import base64
import json
import time
import uuid
from datetime import datetime
from typing import Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from qwen_asr import Qwen3ASRModel


model = Qwen3ASRModel.LLM(
    model="./Qwen3-ASR-1.7B",
    max_inference_batch_size=32,
    max_new_tokens=256,
    dtype="bfloat16",
    tensor_parallel_size=1,
    gpu_memory_utilization=0.8,
    trust_remote_code=True,
)

app = FastAPI(title="Qwen ASR WebSocket Service v2")


class AudioSession:
    """单个 WebSocket 连接的音频会话,各 session 互相独立。"""

    SAMPLE_RATE: int = 16000
    DEFAULT_SILENCE_THRESHOLD: float = 0.03
    DEFAULT_SILENCE_TIMEOUT: float = 1.0
    MIN_CHUNK_DURATION: float = 0.5  # Qwen3-ASR 要求 >= 0.5s
    MIN_AUDIO_SAMPLES: int = int(0.5 * SAMPLE_RATE)

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id: str = str(uuid.uuid4())[:8]

        self.silence_threshold: float = self.DEFAULT_SILENCE_THRESHOLD
        self.silence_timeout: float = self.DEFAULT_SILENCE_TIMEOUT
        self.min_chunk_duration: float = self.MIN_CHUNK_DURATION

        self.audio_buffer: list[np.ndarray] = []
        self.buffer_duration: float = 0.0
        self.last_speech_time: Optional[float] = None
        self.last_chunk_time: Optional[float] = None

        # 并发保护:处理期间禁止再次进入,避免 silence 与 commit 同时处理
        self._processing: bool = False

    def update_config(self, config: dict) -> None:
        if "silence_threshold" in config:
            self.silence_threshold = float(config["silence_threshold"])
        if "silence_timeout" in config:
            self.silence_timeout = float(config["silence_timeout"])
        if "min_chunk_duration" in config:
            self.min_chunk_duration = float(config["min_chunk_duration"])

    def add_audio_chunk(self, audio_base64: str) -> None:
        audio_bytes = base64.b64decode(audio_base64)
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        chunk_duration = len(audio_array) / self.SAMPLE_RATE

        has_speech = self._detect_speech(audio_array)
        now = time.monotonic()

        self.last_chunk_time = now
        if has_speech:
            self.last_speech_time = now

        self.audio_buffer.append(audio_array)
        self.buffer_duration += chunk_duration

    def check_silence_timeout(self) -> bool:
        """事件驱动:每个 append 后调用,判断是否触发静音处理。"""
        if self._processing:
            return False
        if self.buffer_duration < self.min_chunk_duration:
            return False
        if self.last_speech_time is None:
            return False
        return (time.monotonic() - self.last_speech_time) >= self.silence_timeout

    def reset_buffer(self) -> None:
        self.audio_buffer = []
        self.buffer_duration = 0.0
        self.last_speech_time = None
        self.last_chunk_time = None

    def _detect_speech(self, audio: np.ndarray) -> bool:
        if len(audio) == 0:
            return False
        rms = float(np.sqrt(np.mean(audio**2)))
        return rms > self.silence_threshold

    async def process_buffer(self) -> None:
        """snapshot 当前缓冲并立即清空,识别后发 final(文本非空时)。"""
        if self._processing:
            return
        self._processing = True

        # 立即 snapshot 并清空,处理期间新到达的音频进入新缓冲不被吞
        buffer_to_process = self.audio_buffer
        duration_to_process = self.buffer_duration
        self.audio_buffer = []
        self.buffer_duration = 0.0
        self.last_speech_time = None
        self.last_chunk_time = None

        try:
            if not buffer_to_process:
                return

            combined = np.concatenate(buffer_to_process)

            # 短音频:无语音直接丢弃,有语音 padding 到 0.5s 识别
            if duration_to_process < self.MIN_CHUNK_DURATION:
                if not self._detect_speech(combined):
                    return
                if len(combined) < self.MIN_AUDIO_SAMPLES:
                    combined = np.pad(combined, (0, self.MIN_AUDIO_SAMPLES - len(combined)))

            try:
                results = await asyncio.to_thread(
                    model.transcribe,
                    audio=(combined, self.SAMPLE_RATE),
                    language="Chinese",
                )
            except Exception as e:
                await self._send_error(f"识别失败: {e}")
                return

            text = ""
            if results and results[0].text:
                text = results[0].text.strip()

            # 空文本不发 final,避免客户端误触发倒计时
            if text:
                await self.websocket.send_json({
                    "type": "transcript.final",
                    "transcript": text,
                    "session_id": self.session_id,
                    "timestamp": datetime.now().isoformat(),
                })
        finally:
            self._processing = False

    async def _send_error(self, message: str) -> None:
        try:
            await self.websocket.send_json({
                "type": "error",
                "message": message,
                "session_id": self.session_id,
            })
        except Exception:
            pass


@app.websocket("/asr/stream")
async def websocket_asr(websocket: WebSocket) -> None:
    await websocket.accept()
    session = AudioSession(websocket)

    await websocket.send_json({
        "type": "session.created",
        "session_id": session.session_id,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await session._send_error("无效的 JSON")
                continue

            msg_type = message.get("type")

            if msg_type == "session.update":
                config = message.get("session", {}) or {}
                session.update_config(config)
                await websocket.send_json({
                    "type": "session.updated",
                    "session_id": session.session_id,
                })

            elif msg_type == "input_audio_buffer.append":
                audio_base64 = message.get("audio", "")
                if not audio_base64:
                    continue
                session.add_audio_chunk(audio_base64)
                if session.check_silence_timeout():
                    await session.process_buffer()

            elif msg_type == "input_audio_buffer.commit":
                await session.process_buffer()

            elif msg_type == "input_audio_buffer.clear":
                session.reset_buffer()
                await websocket.send_json({
                    "type": "input_audio_buffer.cleared",
                    "session_id": session.session_id,
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await session._send_error(str(e))
    finally:
        session.reset_buffer()


@app.get("/")
async def root() -> dict:
    return {
        "service": "Qwen ASR WebSocket Service v2",
        "websocket_endpoint": "/asr/stream",
        "model": "Qwen3-ASR-1.7B",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8585)
