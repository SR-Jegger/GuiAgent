# 任务拆解功能说明

## 概述

任务拆解功能将复杂的多步骤任务自动分解为多个子步骤，每个子步骤独立经过完整的 Agent 流程（fast_path -> capture -> reasoning -> judge -> execution）。

## 架构设计

### 流程图

```
START -> task_decomposer -> fast_path -> execution -> continue_handler -> fast_path (next step)
                              |                    |
                              v                    v
                          capture -> reasoning -> judge -> template_match
```

### 核心组件

1. **task_decomposer_node** (任务拆解节点)
   - 解析任务指令，自动识别多步骤任务
   - 将任务拆分为结构化子步骤列表
   - 保存全局任务指令作为上下文

2. **fast_path_node** (快速路径节点)
   - 对每个子步骤进行规则匹配
   - 匹配成功直接执行动作
   - 匹配失败进入 VLM 推理流程

3. **reasoning_node** (推理节点)
   - 使用 VLM 分析截图和规划动作
   - 将全局任务指令作为上下文传递

4. **continue_handler** (继续处理器)
   - 子步骤执行完成后判断是否有下一步
   - 更新子步骤索引和状态
   - 路由到下一个子步骤或结束任务

## 状态扩展

在 `AgentState` 中新增了以下字段：

```python
# Task decomposition (for multi-step tasks)
sub_steps: list[dict]  # 子步骤列表
# 结构：[{"step_id": 1, "description": "...", "status": "pending"}, ...]

current_step_index: int  # 当前执行子步骤的索引 (0-based)

global_task_instruction: str  # 原始完整任务指令（作为上下文传递）
```

## 使用方法

### 1. 创建多步骤任务文件

```markdown
## 自动化任务 - 任务指派

在当前打开的无人作战指挥系统页面，对第一架飞机执行指派任务
任务名称一栏输入 侦察任务
侦察目标 输入 麦当劳基地
点击确认然后在地图上随机点击一个靠地图右侧的位置执行指派
```

每行作为一个独立的子步骤。

### 2. 运行 Agent

```bash
python agent_graph.py --mdpath test_md/test_ui8.md
```

### 3. 执行流程示例

对于上述任务，执行流程为：

```
Step 1: "在当前打开的无人作战指挥系统页面，对第一架飞机执行指派任务"
  -> fast_path (no match) -> capture -> reasoning -> judge -> execution

Step 2: "任务名称一栏输入 侦察任务"
  -> fast_path (rule match) -> execution

Step 3: "侦察目标 输入 麦当劳基地"
  -> fast_path (rule match) -> execution

Step 4: "点击确认然后在地图上随机点击一个靠地图右侧的位置执行指派"
  -> fast_path (no match) -> capture -> reasoning -> judge -> execution

-> Task Complete
```

## 规则匹配

每个子步骤首先经过 `fast_path` 节点进行规则匹配：

1. **匹配成功**：直接执行预定义动作（快速路径）
2. **匹配失败**：进入 `capture -> reasoning -> judge` 流程（VLM 路径）

规则文件位于 `rules/` 目录，格式如下：

```json
{
  "id": "open_file_explorer",
  "name": "打开文件资源管理器",
  "trigger": {
    "patterns": [".*打开文件资源管理器.*"]
  },
  "actions": [
    {"type": "hotkey", "keys": ["win", "e"]}
  ]
}
```

## 上下文传递

对于多步骤任务：
- **当前子步骤指令**：作为主要输入传递给 VLM
- **全局任务指令**：作为额外上下文附加到消息中

这样 VLM 可以理解：
1. 当前需要执行的具体动作
2. 整体任务的目标和进度

## 注意事项

1. 任务拆解基于简单的行分割逻辑
2. 单行任务不会被拆解
3. 每个子步骤执行完成后自动进入下一步
4. 任何子步骤触发 `stop` 信号时结束整个任务
