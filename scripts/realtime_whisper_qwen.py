import json
import base64
import threading
import time
from websocket import WebSocketApp
import dashscope

import pyaudio
import threading
 
class AudioRecorder:
    def __init__(self, rate=16000, chunksize=3200):
        self.rate = rate  # 采样率
        self.chunksize = chunksize  # 每次读取的数据量
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.is_recording = False
        
    def start_recording(self, callback):
        """开始录音并实时回调数据"""
        def audio_callback(in_data, frame_count, time_info, status):
            if self.is_recording:
                callback(in_data)
            return (in_data, pyaudio.paContinue)
        
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunksize,
            stream_callback=audio_callback
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
 
# # 使用示例
# def process_audio_data(data):
#     print(f"收到音频数据长度: {len(data)} bytes")
 
# recorder = AudioRecorder()
# recorder.start_recording(process_audio_data)

class QwenASRStreamer:
    def __init__(self):
        self.ws = None
        self.is_connected = False
        
    def connect(self):
        """建立WebSocket连接"""
        headers = {
            'Authorization': f'Bearer {os.environ["DASHSCOPE_API_KEY"]}',
            'X-DashScope-SSE': 'enable'
        }
        
        self.ws = WebSocketApp(
            'wss://dashscope.aliyuncs.com/api/v1/services/ai_audio/asr/streaming',
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # 在后台运行WebSocket
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
    
    def on_open(self, ws):
        """连接建立时的回调"""
        print("WebSocket连接已建立")
        self.is_connected = True
        
        # 发送会话配置
        session_config = {
            "event_id": "session_init",
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {
                    "language": "zh"  # 指定中文识别
                },
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 800
                }
            }
        }
        ws.send(json.dumps(session_config))
    
    def on_message(self, ws, message):
        """收到服务器消息的回调"""
        try:
            data = json.loads(message)
            if data.get('type') == 'transcript.update':
                transcript = data.get('transcript', '')
                if transcript:
                    print(f"实时识别结果: {transcript}")
            elif data.get('type') == 'session.finished':
                print("会话结束")
        except Exception as e:
            print(f"处理消息出错: {e}")
    
    def on_error(self, ws, error):
        """错误处理"""
        print(f"WebSocket错误: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """连接关闭处理"""
        print("WebSocket连接关闭")
        self.is_connected = False
    
    def send_audio_chunk(self, audio_data):
        """发送音频数据块"""
        if self.is_connected and self.ws:
            encoded_data = base64.b64encode(audio_data).decode('utf-8')
            audio_event = {
                "event_id": f"audio_{int(time.time()*1000)}",
                "type": "input_audio_buffer.append",
                "audio": encoded_data
            }
            self.ws.send(json.dumps(audio_event))
    
    def close(self):
        """关闭连接"""
        if self.ws:
            self.ws.close()
        
import os
import signal
import sys
 
class RealTimeASRSystem:
    def __init__(self):
        self.recorder = AudioRecorder()
        self.streamer = QwenASRStreamer()
        self.is_running = False
    
    def start(self):
        """启动整个系统"""
        print("启动实时语音识别系统...")
        self.streamer.connect()
        
        # 等待连接建立
        time.sleep(2)
        
        def audio_callback(audio_data):
            if self.streamer.is_connected:
                self.streamer.send_audio_chunk(audio_data)
        
        self.recorder.start_recording(audio_callback)
        self.is_running = True
        
        print("系统已启动，开始说话吧...")
    
    def stop(self):
        """停止系统"""
        print("停止系统...")
        self.is_running = False
        self.recorder.stop_recording()
        self.streamer.close()
        print("系统已停止")
 
# 信号处理，方便优雅退出
def signal_handler(sig, frame):
    print('收到退出信号')
    if 'system' in globals():
        system.stop()
    sys.exit(0)
 
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    
    system = RealTimeASRSystem()
    try:
        system.start()
        # 保持主线程运行
        while system.is_running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        system.stop()