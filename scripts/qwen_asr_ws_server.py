"""Qwen ASR WebSocket 流式语音识别服务

支持实时流式音频输入，客户端可以边说边收到识别结果。
"""

import os
import uuid
import json
import asyncio
import base64
import tempfile
import wave
import numpy as np
from typing import Optional
from datetime import datetime

import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from qwen_asr import Qwen3ASRModel

# 初始化模型
model = Qwen3ASRModel.from_pretrained(
    "./Qwen3-ASR-1.7B",
    dtype=torch.bfloat16,
    device_map="cuda:0",
    max_inference_batch_size=32,
    max_new_tokens=256,
)

app = FastAPI(title="Qwen ASR WebSocket Service")


class AudioSession:
    """WebSocket 音频会话管理"""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.audio_buffer: list[np.ndarray] = []
        self.buffer_duration: float = 0.0
        self.sample_rate: int = 16000
        self.min_chunk_duration: float = 0.3  # 最小处理时长（秒）- 降低以更快响应
        self.max_chunk_duration: float = 10.0  # 最大缓冲时长（秒）
        self.silence_threshold: float = 0.015  # 静音检测阈值 - 稍微提高以减少噪音误判
        self.silence_timeout: float = 1.0  # 静音超时阈值（秒）- 最后语音后多久处理
        self.last_speech_time: Optional[float] = None  # 最后检测到语音的时间（绝对时间）
        self.last_chunk_time: Optional[float] = None  # 最后收到音频块的时间
        self.session_id: str = str(uuid.uuid4())[:8]
        self.full_transcript: list[str] = []

    def _decode_audio(self, audio_base64: str) -> np.ndarray:
        """解码 base64 音频数据为 numpy 数组"""
        audio_bytes = base64.b64decode(audio_base64)
        # PCM 16-bit 数据转 numpy
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        # 转为 float32 并归一化
        return audio_array.astype(np.float32) / 32767.0

    def _detect_speech(self, audio_data: np.ndarray) -> bool:
        """检测音频中是否有语音"""
        if len(audio_data) == 0:
            return False
        rms = np.sqrt(np.mean(audio_data**2))
        return rms > self.silence_threshold

    def _save_buffer_to_wav(self) -> str:
        """将缓冲区音频保存为临时 WAV 文件"""
        if not self.audio_buffer:
            return None

        # 合并音频数据
        combined = np.concatenate(self.audio_buffer)

        # 创建临时文件
        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_path = temp_file.name

        # 转为 16-bit PCM
        audio_int16 = (combined * 32767).astype(np.int16)

        with wave.open(temp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        return temp_path

    def _should_process(self, has_speech: bool) -> bool:
        """判断是否应该处理当前缓冲区"""
        now = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0

        # 条件1: 缓冲达到最大时长
        if self.buffer_duration >= self.max_chunk_duration:
            return True

        # 条件2: 有足够语音 + 随后的静音超时
        if self.buffer_duration >= self.min_chunk_duration:
            if not has_speech and self.last_speech_time:
                # 这里简化处理，实际应该在异步任务中检测超时
                return True

        return False

    async def process_buffer(self):
        """处理缓冲区音频并返回识别结果"""
        if self.buffer_duration < self.min_chunk_duration:
            return

        temp_path = self._save_buffer_to_wav()
        if not temp_path:
            return

        try:
            # 调用模型识别
            results = model.transcribe(
                audio=temp_path,
                language="Chinese",
            )

            if results and results[0].text.strip():
                text = results[0].text.strip()
                self.full_transcript.append(text)

                # 发送识别结果（静音超时触发，标识一句话结束）
                await self.websocket.send_json({
                    "type": "transcript.final",
                    "transcript": text,
                    "language": results[0].language,
                    "session_id": self.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "silence_triggered": True,
                })

        except Exception as e:
            await self.websocket.send_json({
                "type": "error",
                "message": str(e),
            })

        finally:
            # 清理临时文件和缓冲区
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self.audio_buffer = []
            self.buffer_duration = 0.0
            self.last_speech_time = None
            self.last_chunk_time = None

    def add_audio_chunk(self, audio_base64: str) -> tuple[float, bool]:
        """添加音频数据块到缓冲区

        使用时间戳判断静音：
        - 记录最后收到音频块的时间
        - 记录最后检测到语音的时间
        - 后台任务根据时间差判断是否处理
        """
        audio_data = self._decode_audio(audio_base64)
        chunk_duration = len(audio_data) / self.sample_rate

        has_speech = self._detect_speech(audio_data)
        current_time = asyncio.get_event_loop().time()

        # 记录最后收到音频块的时间（无论有无语音）
        self.last_chunk_time = current_time

        # 无论有无语音，都添加到缓冲区（保持连续性）
        # 但只更新 last_speech_time 如果有语音
        if has_speech:
            self.last_speech_time = current_time

        # 添加到缓冲区（包括静音片段，保证音频连续）
        self.audio_buffer.append(audio_data)
        self.buffer_duration += chunk_duration

        return chunk_duration, has_speech

    def reset_buffer(self):
        """重置缓冲区"""
        self.audio_buffer = []
        self.buffer_duration = 0.0
        self.last_speech_time = None
        self.last_chunk_time = None
        """重置缓冲区"""
        self.audio_buffer = []
        self.buffer_duration = 0.0


@app.websocket("/asr/stream")
async def websocket_asr(websocket: WebSocket):
    """WebSocket ASR 流式识别端点"""
    await websocket.accept()

    session = AudioSession(websocket)

    await websocket.send_json({
        "type": "session.created",
        "session_id": session.session_id,
        "message": "WebSocket 连接已建立，可以开始发送音频数据",
    })

    # 后台任务：定时检查静音超时并处理缓冲区
    process_task = None

    async def check_and_process():
        """定时检查并处理缓冲区

        使用时间差判断静音超时：
        - current_time - last_speech_time >= silence_timeout → 静音超时
        - 有足够缓冲 → 处理
        """
        while True:
            await asyncio.sleep(0.1)  # 每0.1秒检查一次（更频繁）

            current_time = asyncio.get_event_loop().time()

            # 检查是否有足够缓冲
            if session.buffer_duration >= session.min_chunk_duration:

                # 判断静音超时：当前时间 - 最后语音时间 >= 阈值
                if session.last_speech_time is not None:
                    silence_elapsed = current_time - session.last_speech_time

                    if silence_elapsed >= session.silence_timeout:
                        print(
                            f"[ASR] 静音超时处理: "
                            f"缓冲={session.buffer_duration:.1f}s, "
                            f"静音={silence_elapsed:.1f}s"
                        )
                        await session.process_buffer()

                # 兜底：如果很久没收到任何音频块，也处理
                elif session.last_chunk_time is not None:
                    chunk_elapsed = current_time - session.last_chunk_time
                    if chunk_elapsed >= session.silence_timeout * 2:
                        print(f"[ASR] 无语音超时处理: 缓冲={session.buffer_duration:.1f}s")
                        await session.process_buffer()

            # 强制处理：缓冲过长
            if session.buffer_duration >= session.max_chunk_duration:
                print(f"[ASR] 强制处理: 缓冲过长={session.buffer_duration:.1f}s")
                await session.process_buffer()

    try:
        # 启动后台处理任务
        process_task = asyncio.create_task(check_and_process())

        while True:
            # 接收消息
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            if msg_type == "session.update":
                # 更新会话配置
                config = message.get("session", {})
                if "sample_rate" in config:
                    session.sample_rate = config["sample_rate"]
                if "silence_threshold" in config:
                    session.silence_threshold = config["silence_threshold"]
                if "min_chunk_duration" in config:
                    session.min_chunk_duration = config["min_chunk_duration"]

                await websocket.send_json({
                    "type": "session.updated",
                    "session_id": session.session_id,
                })

            elif msg_type == "input_audio_buffer.append":
                # 接收音频数据块
                audio_base64 = message.get("audio", "")
                if audio_base64:
                    chunk_duration, has_speech = session.add_audio_chunk(audio_base64)

                    # 如果缓冲过长，立即处理
                    if session.buffer_duration >= session.max_chunk_duration:
                        await session.process_buffer()

            elif msg_type == "input_audio_buffer.commit":
                # 客户端请求立即处理当前缓冲区
                await session.process_buffer()

            elif msg_type == "input_audio_buffer.clear":
                # 清空缓冲区
                session.reset_buffer()
                await websocket.send_json({
                    "type": "input_audio_buffer.cleared",
                    "session_id": session.session_id,
                })

    except WebSocketDisconnect:
        print(f"WebSocket 断开: session={session.session_id}")

    except Exception as e:
        print(f"WebSocket 错误: {e}")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })

    finally:
        # 处理剩余缓冲区
        if session.buffer_duration > 0:
            await session.process_buffer()

        # 发送完整转写结果
        if session.full_transcript:
            await websocket.send_json({
                "type": "session.finished",
                "session_id": session.session_id,
                "full_transcript": " ".join(session.full_transcript),
            })

        # 取消后台任务
        if process_task:
            process_task.cancel()


# 保留原有 HTTP 接口（兼容性）
@app.post("/transcribe")
async def transcribe_http(file: bytes = None):
    """HTTP 接口（保留兼容）"""
    # ... 原有 HTTP 实现可保留
    pass


@app.get("/")
async def root():
    """服务信息"""
    return {
        "service": "Qwen ASR WebSocket Service",
        "websocket_endpoint": "/asr/stream",
        "http_endpoint": "/transcribe",
        "model": "Qwen3-ASR-1.7B",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8585)