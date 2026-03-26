# 规则匹配引擎 (Rule Matcher)

## 概述

规则匹配引擎是一个用于固定任务场景的快速定位模块，通过在 VLM 推理之前匹配预定义规则，实现毫秒级响应。

## 架构位置

```
  用户指令 → [fast_path_node 规则匹配] → 匹配成功 → [execution_node 直接执行]
                                      ↓ 匹配失败
                                [capture_node → reasoning_node → VLM 推理]
```

## 目录结构

```
GuiAgent/
├── rules/                      # 规则文件目录
│   └── quick_actions.json      # 快速操作规则
├── rule_matcher.py             # 规则匹配引擎
├── agent_graph.py              # (已集成 Fast Path 节点)
└── test_rule_matcher.py        # 测试脚本
```

## 使用方法

### 1. 命令行测试规则匹配

```bash
# 列出所有规则
python rule_matcher.py list

# 测试指令匹配
python rule_matcher.py test "关闭窗口"

# 显示统计信息
python rule_matcher.py stats
```

### 2. 在代码中使用

```python
from rule_matcher import RuleMatcher

matcher = RuleMatcher("./rules")

# 匹配指令
result = matcher.match("关闭窗口")
if result:
    print(f"匹配规则：{result['rule_name']}")
    print(f"动作链：{result['actions']}")
```

### 3. 运行完整 Agent

```bash
# 使用默认规则目录
python run_agent.py --mdpath test_md/test_ui1.md

# 指定规则目录
python run_agent.py --mdpath test_md/test_ui1.md --rules_dir ./rules
```

## 规则 JSON 格式

```json
{
  "version": "1.0",
  "rules": [
    {
      "id": "unique_rule_id",
      "name": "规则名称",
      "description": "规则描述",
      "trigger": {
        "patterns": [
          ".*关闭。*",
          ".*退出.*"
        ]
      },
      "actions": [
        {
          "type": "hotkey",
          "keys": ["alt", "f4"]
        }
      ],
      "enabled": true
    }
  ]
}
```

### 动作类型

| 类型 | 参数 | 说明 |
|------|------|------|
| `hotkey` | `keys: ["ctrl", "c"]` | 快捷键组合 |
| `scroll` | `pixels: 3` | 滚轮滚动 (正上负下) |
| `click` | `target: {...}` | 点击 (需配合 template) |
| `type` | `text: "{{match_group_1}}"` | 输入文本 (支持变量) |
| `key` | `keys: ["enter"]` | 单个按键 |

### 变量替换

在 `text` 字段中可使用变量：

- `{{match_group_1}}` - 第 1 个正则捕获组
- `{{match_group_2}}` - 第 2 个正则捕获组
- `{{full_match}}` - 完整匹配文本

示例:

```json
{
  "trigger": {
    "patterns": ["在 (?:B 站|bilibili) 搜索 (.+)"]
  },
  "actions": [
    {"type": "type", "text": "搜索：{{match_group_1}}"}
  ]
}
```

指令 "在 B 站搜索 发射邓总" → 动作 `{"type": "type", "text": "搜索：发射邓总"}`

## 已内置规则

| ID | 名称 | 触发指令 | 动作 |
|----|------|---------|------|
| `close_current_window` | 关闭当前窗口 | "关闭窗口"、"退出" | Alt+F4 |
| `minimize_window` | 最小化窗口 | "最小化"、"显示桌面" | Win+D |
| `scroll_down` | 向下滚动 | "向下滚动"、"往下翻" | 滚轮向下 |
| `scroll_up` | 向上滚动 | "向上滚动"、"往上翻" | 滚轮向上 |
| `copy_text` | 复制文本 | "复制"、"拷贝" | Ctrl+C |
| `paste_text` | 粘贴文本 | "粘贴" | Ctrl+V |

## 添加新规则

### 方法 1: 直接编辑 JSON

在 `rules/quick_actions.json` 中添加规则。

### 方法 2: 使用命令行

```python
from rule_matcher import RuleMatcher

matcher = RuleMatcher("./rules")
matcher.add_rule({
    "id": "my_rule",
    "name": "我的规则",
    "trigger": {"patterns": [".*我的指令.*"]},
    "actions": [{"type": "hotkey", "keys": ["ctrl", "s"]}],
    "enabled": True
})
matcher.save_rules()
```

## 性能对比

| 场景 | 纯 VLM 方案 | 规则 + VLM 混合 |
|------|-----------|----------------|
| 固定任务 (如关闭窗口) | 3-10 秒 | <100ms |
| 泛化任务 (未见过) | 3-10 秒 | 3-10 秒 (fallback 到 VLM) |
| 准确率 | 80-95% | 固定场景 100% |

## 未来扩展

1. **应用上下文感知** - 根据当前活动窗口过滤规则
2. **规则优先级** - 支持规则冲突时的优先级判断
3. **规则学习** - 从历史日志中自动发现可规则化的任务
4. **模板联动** - 规则中引用 template 库中的 UI 元素

## 故障排查

**问题：规则不匹配**
- 检查 `enabled` 字段是否为 `true`
- 检查正则表达式是否正确
- 运行 `python rule_matcher.py test "指令"` 查看匹配详情

**问题：规则文件不加载**
- 确保 `rules/quick_actions.json` 存在且格式正确
- 检查 JSON 语法错误
