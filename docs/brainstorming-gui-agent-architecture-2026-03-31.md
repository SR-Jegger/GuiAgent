# GUI Agent 架构优化与插件化扩展 - Brainstorming 对话记录

**日期**: 2026-03-31

---

## 一、项目现状分析

### 已实现的核心能力
- VLM 视觉推理 + 动作执行循环
- 多步骤任务分解（sub_steps）
- Fast Path 规则匹配加速
- FastAPI 热启动服务
- 任务取消机制
- LangGraph 状态管理

### 当前规则系统的局限性

| 限制 | 具体表现 |
|------|---------|
| **触发条件单一** | 只能用正则匹配文本指令，不支持屏幕状态检测 |
| **动作类型固定** | hotkey/click/scroll/type，新增类型要改代码 |
| **坐标硬编码** | 固定坐标如 `[104, 964]`，不同分辨率无法适配 |
| **无动态参数** | 无法从指令中提取参数（如"点击第3个按钮"） |
| **无自定义逻辑** | 不支持复杂条件判断或调用外部 API |

---

## 二、优化方向确认

用户最关心：
1. **架构与扩展性** — 新增应用支持困难、缺少插件/扩展机制
2. **准确性与可靠性**

---

## 三、核心架构范式选择

### 主流三种范式

| 范式 | 代表 | 特点 | 适用场景 |
|------|------|------|---------|
| **端到端 VLM** | OpenAI Operator、Claude Computer Use | VLM 直接输出动作，简单直接 | 通用场景，灵活性最高 |
| **分层规划 + 执行** | Adept ACT-1、MultiOn | 高层规划器 + 低层执行器分离 | 复杂多步骤任务 |
| **技能库 + 调度器** | Apprentice、OS-Copilot | 预定义技能 + LLM 调度选择 | 高频重复任务效率高 |

### 确定方案：混合架构

```
用户指令
    ↓
意图理解层（LLM）→ 任务分解 + 技能匹配
    ↓
┌─────────────────────────────────────┐
│  技能层                              │
│  ├─ 高频技能（规则/Fast Path）       │  ← 已有技能直接执行
│  ├─ 学习技能（历史成功操作）          │  ← 复用历史经验
│  └─ 通用技能（VLM 推理）              │  ← 无技能时降级
└─────────────────────────────────────┘
    ↓
执行层（动作执行 + 验证）
    ↓
结果反馈 → 成功则记录为新技能
```

---

## 四、技能定义模型

### 技能完整结构设计

```
Skill = {
    "id": "excel_insert_row",
    "name": "Excel插入行",

    // 触发条件（多种）
    "triggers": [
        {"type": "text_pattern", "value": ".*Excel.*插入.*行.*"},
        {"type": "screen_detector", "value": "excel_window_present"},
        {"type": "context", "value": "app=excel"}
    ],

    // 参数定义（从指令提取）
    "params": [
        {"name": "row_number", "type": "int", "extract": "第(\d+)行"}
    ],

    // 执行序列
    "actions": [
        {"type": "hotkey", "keys": ["ctrl", "shift", "+"], "condition": "row_number==null"},
        {"type": "click", "target": "row_{row_number}", "condition": "row_number!=null"},
        {"type": "hotkey", "keys": ["ctrl", "shift", "+"]}
    ],

    // 验证条件（执行后检查）
    "validation": {
        "type": "screen_check",
        "expect": "new_row_visible"
    },

    // 失败恢复
    "fallback": "vlm_reasoning"
}
```

### 触发条件类型（全部支持）
- **文本匹配** — 正则匹配用户指令
- **屏幕状态检测** — 检测窗口、UI 元素、文字出现（OCR/图像识别）
- **上下文状态** — 当前应用、历史操作、环境变量
- **组合条件** — 以上条件可组合（AND/OR）

---

## 五、动作执行模型扩展

| 类型 | 具体动作 | 实现难度 |
|------|---------|---------|
| **基础交互** | click/hotkey/type/scroll/drag | ✅ 已有 |
| **应用操作** | open_app/switch_app/close_app | ⚠️ 部分有 |
| **智能定位** | click_by_text/click_by_icon（OCR/图像匹配定位） | ❌ 缺失 |
| **数据操作** | read_text/write_file/call_api | ❌ 缺失 |
| **流程控制** | wait_until/repeat/condition_branch | ❌ 缺失 |
| **子任务调用** | call_skill（调用另一个技能） | ❌ 缺失 |

---

## 六、验证与自学习机制

### 确定策略：自动学习 + 人工审核

**筛选门槛设计：**

```
VLM 成功操作
    ↓
是否值得进入候选池？
    ├─ 同类操作重复成功 ≥ 3 次 → 进入候选池
    ├─ 用户显式标记"这个操作很好" → 进入候选池
    ├─ 操作耗时明显优于历史平均 → 进入候选池
    └─ 其他 → 仅记录日志，不入池

候选池
    ↓
人工审核
    ├─ 确认入库 → 生成技能规则
    ├─ 优化后入库 → 人工简化动作序列
    ├─ 标记为"场景特定" → 仅在相似上下文触发
    └─ 拒绝 → 从候选池移除
```

### 同类操作识别策略

采用折中方案：**指令 + 应用上下文 + 动作结构**

不需要存储完整截图对比（计算量太大），而是提取**轻量状态指纹**：

```
操作记录 = {
    "instruction": "发送消息给张三",
    "instruction_intent": "send_message",
    "app_context": "WeChat",
    "action_structure": ["click", "type", "click"],
    "action_details": [...],
    "success": true,
    "timestamp": "..."
}
```

**聚类逻辑：**

```python
def is_same_operation(op1, op2):
    # 指令意图相似度
    intent_match = semantic_similarity(op1.instruction, op2.instruction) > 0.8

    # 应用相同
    app_match = op1.app_context == op2.app_context

    # 动作结构相同
    structure_match = op1.action_structure == op2.action_structure

    return intent_match and app_match and structure_match
```

---

## 七、实现架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    现有 Agent 执行流程                        │
│  capture → reasoning(VLM) → execution → result              │
└─────────────────────────────────────────────────────────────┘
                            ↓ 执行成功
┌─────────────────────────────────────────────────────────────┐
│                    新增：学习层                               │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │ 操作记录器    │ →  │ 聚类引擎     │ →  │ 候选池管理   │   │
│  │ OperationLog │    │ ClusterEngine│    │ CandidatePool│   │
│  └──────────────┘    └──────────────┘    └──────────────┘   │
│                                                ↓            │
│                                         ┌──────────────┐    │
│                                         │ 人工审核接口  │    │
│                                         │ ReviewAPI    │    │
│                                         └──────────────┘    │
│                                                ↓            │
│                                         ┌──────────────┐    │
│                                         │ 技能生成器    │    │
│                                         │ SkillGenerator│   │
│                                         └──────────────┘    │
│                                                ↓            │
│                                         ┌──────────────┐    │
│                                         │ 技能库       │    │
│                                         │ SkillRegistry │   │
│                                         └──────────────┘    │
└─────────────────────────────────────────────────────────────┘
                            ↓ 新技能入库
┌─────────────────────────────────────────────────────────────┐
│                    改造：Fast Path                            │
│  原有：正则匹配 → 执行                                        │
│  改造：多维度匹配(指令+应用+屏幕) → 技能调度 → 执行            │
└─────────────────────────────────────────────────────────────┘
```

---

## 八、核心模块设计

### 模块一：操作记录器

**集成点**：在 `execution_node` 执行完成后记录

**数据结构**：

```python
# 存储文件：data/operation_logs.jsonl（每行一条记录）

{
    "log_id": "uuid",
    "timestamp": "2024-01-15T10:30:00",

    # 指令信息
    "instruction": "发送消息给张三",
    "instruction_hash": "abc123",
    "task_name": "wechat_send",

    # 上下文
    "app_context": {
        "active_window": "微信",
        "window_title": "微信 - 张三",
        "process_name": "WeChat.exe"
    },

    # 动作信息
    "action_structure": ["click", "type", "click"],
    "actions": [
        {"type": "click", "coordinate": [100, 200]},
        {"type": "type", "text": "你好"},
        {"type": "click", "coordinate": [300, 400]}
    ],

    # 执行结果
    "success": true,
    "stop_flag": false,
    "source": "vlm",
    "step_id": 3
}
```

### 模块二：聚类引擎

**职责**：定期扫描日志，识别重复出现的同类操作

**数据结构**：

```python
# 聚类结果存储：data/operation_clusters.json

{
    "clusters": [
        {
            "cluster_id": "cluster_001",
            "pattern": {
                "instruction_pattern": "发送消息给*",
                "app_context": "微信",
                "action_structure": ["click", "type", "click"]
            },
            "members": ["log_id_1", "log_id_2", "log_id_3"],
            "count": 5,
            "status": "candidate"
        }
    ]
}
```

### 模块三：候选池与审核接口

```python
# FastAPI 新增路由

@app.get("/api/v1/skills/candidates")
async def list_candidate_skills():
    """列出待审核的候选技能"""

@app.post("/api/v1/skills/candidates/{cluster_id}/approve")
async def approve_candidate(cluster_id: str, modifications: Optional[dict] = None):
    """批准候选技能"""

@app.post("/api/v1/skills/candidates/{cluster_id}/reject")
async def reject_candidate(cluster_id: str, reason: str):
    """拒绝候选技能"""
```

### 模块四：技能生成器

从聚类生成可用的技能规则，处理坐标合并、参数槽位提取等。

### 模块五：技能库与匹配器改造

将原有 RuleMatcher 改造为 SkillMatcher，支持多维度匹配和参数提取。

---

## 九、总结

本次 Brainstorming 确定了以下核心设计决策：

1. **架构范式**：混合架构（技能库 + VLM 降级）
2. **触发条件**：全维度支持（文本/屏幕/上下文/组合）
3. **动作类型**：全类型支持（基础/智能定位/流程控制/子任务）
4. **学习策略**：自动学习 + 人工审核
5. **同类识别**：指令 + 应用上下文 + 动作结构

---

*对话记录保存于 2026-03-31*