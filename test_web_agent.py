import requests

class WebAgentClient:
    def __init__(self, server_url="http://localhost:3001"):
        self.server_url = server_url

    def navigate(self, url: str) -> dict:
        """导航到指定 URL"""
        r = requests.post(f"{self.server_url}/navigate", json={"url": url})
        return r.json()

    def execute(self, instruction: str) -> dict:
        """执行操作指令"""
        r = requests.post(f"{self.server_url}/execute", json={"instruction": instruction})
        return r.json()

    def execute_with_nav(self, instruction: str, target_url: str) -> dict:
        """先导航再执行"""
        r = requests.post(
            f"{self.server_url}/execute",
            json={"instruction": instruction, "targetUrl": target_url}
        )
        return r.json()

    def get_status(self) -> dict:
        """获取当前页面状态"""
        r = requests.get(f"{self.server_url}/status")
        return r.json()

# ==================== 使用示例 ====================

agent = WebAgentClient()

# 方法 1: 先在浏览器中打开 http://localhost:3000
print("导航到目标页面...")
result = agent.navigate("http://localhost:3000")
print(f"导航结果：{result}")

# 然后执行操作
print("执行操作...")
result = agent.execute("点击页面上标号为 0 的按钮")
print(f"操作结果：{result}")

