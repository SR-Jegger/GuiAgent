
import tkinter as tk
from pynput import mouse
import threading

class MouseTracker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("鼠标坐标追踪器")
        self.root.geometry("220x80")
        self.root.attributes('-topmost', True)  # 始终置顶
        self.root.configure(bg='#1a1a2e')
        
        # 移除窗口边框（可选）
        # self.root.overrideredirect(True)
        
        # 坐标显示标签
        self.label = tk.Label(
            self.root,
            text="X: 0\nY: 0",
            font=("Consolas", 24, "bold"),
            fg="#00ff88",
            bg="#1a1a2e",
            justify="center"
        )
        self.label.pack(expand=True, fill="both")
        
        # 提示标签
        self.tip = tk.Label(
            self.root,
            text="按 Ctrl+C 复制坐标 | 右键关闭",
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
        self.label.config(text=f"X: {self.current_x}\nY: {self.current_y}")
        
    def copy_coordinates(self, event=None):
        """复制坐标到剪贴板"""
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{self.current_x}, {self.current_y}")
        self.tip.config(text="已复制到剪贴板！", fg="#00ff88")
        self.root.after(1500, lambda: self.tip.config(
            text="按 Ctrl+C 复制坐标 | 右键关闭", fg="#888888"
        ))
        
    def run(self):
        """运行应用"""
        self.root.mainloop()
        self.listener.stop()

# 检查依赖并运行
print("正在启动鼠标坐标追踪器...")
print("请确保已安装 pynput: pip install pynput")
print("=" * 40)

try:
    tracker = MouseTracker()
    tracker.run()
except Exception as e:
    print(f"错误: {e}")
    print("请先安装依赖: pip install pynput")
