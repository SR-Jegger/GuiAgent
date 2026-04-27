import tkinter as tk
from pynput import mouse
import threading
import pyautogui

# 屏幕分辨率（用于归一化计算）
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()


def normalize_coordinate(x, y, width=SCREEN_WIDTH, height=SCREEN_HEIGHT):
    """将像素坐标转换为归一化坐标 (0-1000 范围)"""
    x_norm = int(x / width * 1000)
    y_norm = int(y / height * 1000)
    return x_norm, y_norm


class MouseTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("鼠标坐标追踪器")
        self.root.geometry("280x100")
        self.root.attributes('-topmost', True)  # 始终置顶
        self.root.configure(bg='#1a1a2e')

        # 原始坐标显示标签
        self.label_raw = tk.Label(
            self.root,
            text="像素坐标\nX: 0  Y: 0",
            font=("Consolas", 14, "bold"),
            fg="#00ff88",
            bg="#1a1a2e",
            justify="center"
        )
        self.label_raw.pack(expand=True, fill="both")

        # 归一化坐标显示标签
        self.label_norm = tk.Label(
            self.root,
            text="归一化坐标 (0-1000)\nX: 0  Y: 0",
            font=("Consolas", 14, "bold"),
            fg="#ff8800",
            bg="#1a1a2e",
            justify="center"
        )
        self.label_norm.pack(expand=True, fill="both")

        # 提示标签
        self.tip = tk.Label(
            self.root,
            text="Ctrl+C 复制 | 右键关闭 | 屏幕: {SCREEN_WIDTH}x{SCREEN_HEIGHT}",
            font=("微软雅黑", 9),
            fg="#888888",
            bg="#1a1a2e"
        )
        self.tip.pack(side="bottom", pady=2)

        # 当前坐标
        self.current_x = 0
        self.current_y = 0

        # 绑定右键关闭
        self.root.bind("<Button-3>", lambda e: self.root.quit())
        self.root.bind("<Control-c>", self.copy_coordinates)

        # 启动鼠标监听线程
        self.listener = mouse.Listener(on_move=self.on_move)
        self.listener.start()

    def on_move(self, x, y):
        """鼠标移动回调"""
        self.current_x = x
        self.current_y = y
        # 使用 after 在主线程更新 UI
        self.root.after(0, self.update_display)

    def update_display(self):
        """更新显示"""
        # 更新原始坐标
        self.label_raw.config(text=f"像素坐标\nX: {self.current_x}  Y: {self.current_y}")

        # 更新归一化坐标
        x_norm, y_norm = normalize_coordinate(self.current_x, self.current_y)
        self.label_norm.config(text=f"归一化坐标 (0-1000)\nX: {x_norm}  Y: {y_norm}")

    def copy_coordinates(self, event=None):
        """复制坐标到剪贴板（同时复制两种格式）"""
        x_norm, y_norm = normalize_coordinate(self.current_x, self.current_y)

        # 复制两种格式
        text_to_copy = f"像素: ({self.current_x}, {self.current_y})\n归一化: ({x_norm}, {y_norm})"
        self.root.clipboard_clear()
        self.root.clipboard_append(text_to_copy)

        self.tip.config(text="已复制到剪贴板！", fg="#00ff88")
        self.root.after(1500, lambda: self.tip.config(
            text=f"Ctrl+C 复制 | 右键关闭 | 屏幕: {SCREEN_WIDTH}x{SCREEN_HEIGHT}", fg="#888888"
        ))

    def run(self):
        """运行应用"""
        self.root.mainloop()
        self.listener.stop()


# 检查依赖并运行
print("正在启动鼠标坐标追踪器...")
print(f"屏幕分辨率: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
print("请确保已安装 pynput: pip install pynput")
print("=" * 40)

try:
    tracker = MouseTracker()
    tracker.run()
except Exception as e:
    print(f"错误: {e}")
    print("请先安装依赖: pip install pynput")