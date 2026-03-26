text = """
<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [50, 950]}}</tool_call>
"""

from utils import extract_tool_calls

res = extract_tool_calls(text)
print(res[0]["arguments"]["action"])

action_history= []

# reason 的 Action
# TEMPLATE  已完成操作：+target