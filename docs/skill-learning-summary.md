# GuiAgent 技能学习机制 - 完整总结

**文档日期**: 2026-04-02

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           技能学习完整流程                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐                                                       │
│  │ VLM 执行操作  │  ← 用户运行任务，VLM推理执行动作                         │
│  └──────┬───────┘                                                       │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ OperationLogger.log()    │  ← 自动记录操作到 JSONL                    │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌──────────────────────────────────┐                                   │
│  │ data/logs/operation_logs.jsonl   │  ← 操作日志持久化存储               │
│  └──────┬───────────────────┬───────┘                                   │
│         │                 │                                             │
│         ↓                 ↓                                             │
│  ┌───────────────────────────────────────────────┐                       │
│  │ ClusterEngine.scan_and_cluster()              │  ← 手动触发聚类        │
│  │   ├─ Step 1: 新日志匹配已有聚类（增量）         │                       │
│  │   └─ Step 2: 未聚类日志创建新聚类（全量）       │                       │
│  └──────┬────────────────────────────────────────┘                       │
│         ↓                                                               │
│  ┌───────────────────────────────────────┐                              │
│  │ data/clusters/operation_clusters.json │  ← 候选池，等待审核            │
│  │   status: "candidate"                 │                              │
│  └──────┬────────────────────────────────┘                              │
│         │ POST /api/v1/skills/candidates/{id}/approve                   │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ SkillGenerator.generate()│  ← 生成技能规则                            │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌─────────────────────────────────┐                                    │
│  │ rules/learned_skills.json       │  ← 技能库，Fast Path 使用           │
│  └──────┬──────────────────────────┘                                    │
│         │ RuleMatcher.load_all_rules()                                  │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ Fast Path 匹配生效        │  ← 新任务直接命中规则，无需 VLM            │
│  │ source: "learned"         │                                         │
│  │ confidence: 0.75          │                                         │
│  └──────────────────────────┘                                           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 二、五个核心阶段

### Phase 1: 操作记录器

**功能**：自动记录每次 VLM 执行的操作，为后续学习提供数据基础。

**核心文件**：`learning/operation_logger.py`

**触发位置**：`nodes/execution_node.py` 中，VLM 执行成功后自动调用。

**记录内容**：

| 字段 | 说明 | 示例 |
|------|------|------|
| `log_id` | 唯一标识符 | `"a3bb328d-..."` |
| `timestamp` | 时间戳 | `"2026-04-02T10:15:13"` |
| `instruction` | 用户指令 | `"双击打开Edge Dev浏览器"` |
| `instruction_hash` | MD5 哈希（快速比对） | `"522e5b4d"` |
| `app_context` | 应用上下文 | `{"active_window": "Program Manager"}` |
| `action_structure` | 动作结构序列 | `["double_click"]` |
| `actions` | 具体动作详情 | `[{"action": "double_click", "coordinate": [230, 751]}]` |
| `success` | 是否成功 | `true` |
| `source` | 来源 | `"vlm"` 或 `"fast_path"` |

**日志示例**：

```json
{
    "log_id": "a3bb328d-2d9a-46b5-aa6e-1d316ce0f977",
    "timestamp": "2026-04-02T10:15:13.780745",
    "instruction": "双击打开Edge Dev浏览器",
    "instruction_hash": "522e5b4d",
    "task_name": "自动化任务-任务指派",
    "app_context": {
        "active_window": "Program Manager",
        "window_title": "Program Manager"
    },
    "action_structure": ["double_click"],
    "actions": [{"action": "double_click", "coordinate": [230, 751]}],
    "success": true,
    "source": "vlm",
    "step_id": 1
}
```

**关键细节**：
- 只记录 `source="vlm"` 的操作，Fast Path 命中的不记录（已经是规则）
- 使用 JSONL 格式（每行一个 JSON），便于追加和流式读取
- 日志永久保留，支持回溯和重新聚类

---

### Phase 2: 聚类引擎

**功能**：识别日志中重复出现的同类操作，生成候选技能。

**核心文件**：`learning/cluster_engine.py`, `learning/similarity.py`

**相似度判定条件**：

两个操作被判定为"相同"需要同时满足三个条件：

| 维度 | 阈值 | 计算方法 | 说明 |
|------|------|----------|------|
| 应用上下文 | > 0.3 | 窗口标题 Jaccard 相似度 | 确保是同一应用 |
| 指令相似度 | > 0.6 | 分词后 Jaccard 相似度 | 语义相近的指令 |
| 动作结构 | > 0.8 | 动作类型 Jaccard 相似度 | 操作序列相似 |

**Jaccard 相似度计算**：

```python
def jaccard_similarity(set1: set, set2: set) -> float:
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0

# 示例
tokenize_instruction("发送消息给张三")  # {"发送", "消息", "给", "张三"}
tokenize_instruction("发送消息给李四")  # {"发送", "消息", "给", "李四"}
# Jaccard = 3/5 = 0.6
```

**增量聚类策略**：

```python
def scan_and_cluster(min_cluster_size=3, full_scan=False):
    # Step 1: 只处理新日志，匹配已有聚类（性能优化）
    new_logs = load_logs_after(last_scan_log_id)
    for log in new_logs:
        if matches_existing_cluster(log):
            add_to_cluster(log)

    # Step 2: 处理所有未聚类日志，创建新聚类（避免遗漏）
    unclustered = get_all_unclustered_logs()  # 包括旧的未聚类日志
    for log1 in unclustered:
        similar = find_similar(log1, unclustered)
        if len(similar) >= min_cluster_size:
            create_cluster(similar)

    # 更新扫描标记
    last_scan_log_id = get_latest_log_id()
```

**关键细节**：
- `last_scan_log_id` 标记上次扫描位置，支持增量加载
- Step 1 只处理新日志（快速），Step 2 处理所有未聚类日志（完整）
- 避免"遗漏问题"：之前未聚类的日志也会在 Step 2 被处理

**聚类结果数据结构**：

```json
{
    "cluster_id": "cluster_fada9d3a",
    "pattern": {
        "instruction_pattern": "双击打开Edge\\s*Dev浏览器",
        "app_context": "Program Manager",
        "action_structure": ["double_click"]
    },
    "members": ["log_id_1", "log_id_2", "log_id_3"],
    "sample_instructions": ["双击打开Edge Dev浏览器", ...],
    "sample_actions": [...],
    "count": 3,
    "status": "candidate"
}
```

**状态流转**：

```
candidate（候选）→ approved（已批准）或 rejected（已拒绝）
```

---

### Phase 3: 候选池管理与审核 API

**功能**：提供人工审核接口，确认候选技能是否入库。

**核心文件**：`app/server.py`

**API 端点**：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/skills/cluster` | POST | 触发聚类 |
| `/api/v1/skills/candidates` | GET | 列出候选技能 |
| `/api/v1/skills/candidates/{id}` | GET | 获取候选详情 |
| `/api/v1/skills/candidates/{id}/approve` | POST | 批准候选 |
| `/api/v1/skills/candidates/{id}/reject` | POST | 拒绝候选 |
| `/api/v1/skills/stats` | GET | 统计信息 |
| `/api/v1/skills` | GET | 列出已入库技能 |

**聚类 API 参数**：

```
POST /api/v1/skills/cluster?min_cluster_size=3&full_scan=true
```

- `min_cluster_size`：最小聚类大小，默认 3
- `full_scan`：是否全量扫描，默认 false（增量扫描）

**审核流程**：

```python
# 批准流程
@app.post("/api/v1/skills/candidates/{cluster_id}/approve")
async def approve_candidate_skill(cluster_id: str):
    # 1. 验证状态
    if cluster.status != "candidate":
        raise HTTPException(...)

    # 2. 更新聚类状态
    cluster.status = "approved"

    # 3. 生成技能规则
    skill = SkillGenerator().generate_skill(cluster)

    # 4. 保存到技能库
    SkillGenerator().save_skill(skill)

    return {"success": True, "skill_id": skill["id"]}
```

---

### Phase 4: 技能生成器

**功能**：从批准的聚类生成可复用的技能规则。

**核心文件**：`learning/skill_generator.py`

**生成内容**：

| 字段 | 生成方式 | 说明 |
|------|----------|------|
| `id` | `f"learned_{uuid}"` | 唯一标识符 |
| `name` | 应用名+动作结构 | `"Program Manager_双击"` |
| `description` | 包含操作数量 | `"Auto-learned from 3 operations"` |
| `source` | 固定值 | `"learned"` |
| `trigger.patterns` | 从指令生成正则 | `["双击打开Edge\\s*Dev浏览器"]` |
| `trigger.app_context` | 从聚类提取 | `["Program Manager"]` |
| `actions` | 泛化后的动作 | 见下方 |
| `confidence` | 基于数量计算 | 0.65 ~ 1.0 |

**指令转正则规则**：

| 原始指令 | 生成的正则 | 场景 |
|----------|-----------|------|
| `双击打开Edge Dev浏览器` | `双击打开Edge\s*Dev浏览器` | 完全相同 |
| `发送消息给张三` | `发送消息给.*` | 末尾变化 |
| `点击确定按钮提交` | `点击.*按钮提交` | 中间变化 |

**动作泛化规则**：

```python
# 点击动作：保留坐标（后续可改进为相对坐标）
{"type": "click", "coordinate": [230, 751]}

# 输入动作：短文本保留，长文本参数化
if len(text) < 10:
    {"type": "type", "text": "你好"}  # 保留
else:
    {"type": "type", "text": "{{text_param}}"}  # 参数化
```

**置信度计算**：

```python
def _calculate_confidence(cluster):
    count = cluster.get("count", 0)
    # 3次 = 0.65, 10次以上 = 1.0
    return min(count / 10, 1.0) * 0.5 + 0.5
```

**生成的技能示例**：

```json
{
    "id": "learned_1a2b3c4d",
    "name": "Program Manager_双击",
    "description": "Auto-learned skill from 3 operations",
    "source": "learned",
    "trigger": {
        "patterns": ["双击打开Edge\\s*Dev浏览器"],
        "app_context": ["Program Manager"]
    },
    "actions": [
        {"type": "double_click", "coordinate": [230, 751]}
    ],
    "enabled": true,
    "confidence": 0.75
}
```

---

### Phase 5: 技能库集成

**功能**：将生成的技能加载到 Fast Path 匹配流程。

**核心文件**：`rule_matcher.py`, `nodes/fast_path_node.py`

**规则加载**：

```python
def load_rules_file(filepath):
    for rule in rules:
        # 标记来源
        is_learned = (filename == "learned_skills.json")
        rule["source"] = "learned" if is_learned else "manual"

        # 预编译正则，建立 rule_id -> patterns 映射
        patterns = rule.get("trigger", {}).get("patterns", [])
        self.rules_cache[rule["id"]] = [re.compile(p) for p in patterns]
```

**匹配流程**：

```python
def _match_rule(rule, instruction, app_context):
    # 1. 检查应用上下文
    if app_context not in rule["trigger"]["app_context"]:
        return None

    # 2. 在 rules_cache 中根据 rule_id 获取预编译的正则列表
    patterns = self.rules_cache.get(rule["id"], [])
    for pattern in patterns:
        if pattern.search(instruction):
            return build_match_result(rule)
```

**匹配结果**：

```python
{
    "rule_id": "learned_1a2b3c4d",
    "rule_name": "Program Manager_双击",
    "source": "learned",      # 标记为学习获得
    "confidence": 0.75,       # 置信度
    "actions": [...]
}
```

**Fast Path 日志输出**：

```
[FAST_PATH] Matched: Program Manager_双击 [LEARNED]
[FAST_PATH] Confidence: 0.75
```

---

## 三、文件结构

```
learning/
├── __init__.py
├── operation_logger.py    # Phase 1: 操作记录
├── similarity.py          # Phase 2: 相似度计算
├── cluster_engine.py      # Phase 2: 聚类引擎
└── skill_generator.py     # Phase 4: 技能生成

utils/
└── app_context.py         # Phase 1: 应用上下文检测

data/
├── logs/
│   └── operation_logs.jsonl    # 运行时生成
└── clusters/
    └── operation_clusters.json # 运行时生成

rules/
└── learned_skills.json    # 学习到的技能

nodes/
├── execution_node.py      # Phase 1: 添加日志记录
└── fast_path_node.py      # Phase 5: 显示技能来源

app/
└── server.py              # Phase 3: 审核API

rule_matcher.py            # Phase 5: 加载学习技能
```

---

## 四、关键设计决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 聚类触发 | 手动 API | 用户可控，避免不必要开销 |
| 日志保留 | 永久保留 | 支持回溯、重新聚类、审计 |
| 聚类策略 | 增量+全量混合 | 性能优化 + 避免遗漏 |
| 审核机制 | 人工审核 | 避免错误规则入库 |
| 正则生成 | 从原始指令生成 | 比聚类 pattern 更准确 |
| 相似度算法 | Jaccard | 简单高效，可扩展为语义相似度 |

---

## 五、使用流程

```bash
# 1. 运行任务，自动记录
python run_agent.py --mdpath test_md/test_ui1.md

# 2. 触发聚类
curl -X POST http://localhost:8000/api/v1/skills/cluster

# 3. 查看候选
curl http://localhost:8000/api/v1/skills/candidates

# 4. 批准
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_xxx/approve

# 5. 验证生效
curl http://localhost:8000/api/v1/skills

# 6. 再次运行相同任务，观察 Fast Path 命中
# 日志: [FAST_PATH] Matched: xxx [LEARNED] Confidence: 0.75
```

---

## 六、当前限制与改进方向

| 限制 | 当前行为 | 改进方向 |
|------|----------|----------|
| 坐标硬编码 | 直接使用样本坐标 | 相对坐标、UI 元素定位 |
| 文本参数化 | 仅长文本参数化 | NER 智能识别变量 |
| 触发模式 | 简单正则泛化 | NLP 提取意图模板 |
| 验证机制 | 无 | 执行后验证是否成功 |
| 测试覆盖 | 无 | 添加单元测试 |
| 定时聚类 | 需手动触发 | 可选后台定时任务 |

---

## 七、已修复的 Bug

### Bug 1: `extract_pattern_from_instructions` 重复拼接

相同指令生成的 pattern 重复：

```
修复前: "双击打开Edge\ Dev浏览器.*双击打开Edge\ Dev浏览器"
修复后: "双击打开Edge\ Dev浏览器"
```

**修复位置**：`learning/similarity.py`

**修复内容**：添加相同指令检查和重叠检查

```python
# 添加相同指令检查
if len(set(instructions)) == 1:
    return re.escape(instructions[0])

# 添加重叠检查
if prefix == suffix:
    return re.escape(prefix)
```

### Bug 2: `_instruction_to_pattern` 多余反斜杠

**修复位置**：`learning/skill_generator.py`

```
修复前: r"\\s*" (匹配反斜杠+s)
修复后: r"\s*" (匹配空白字符)
```

---

## 八、数据字典

### operation_logs.jsonl 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| log_id | string | 是 | UUID 格式的唯一标识 |
| timestamp | string | 是 | ISO 8601 格式时间戳 |
| instruction | string | 是 | 用户输入的指令文本 |
| instruction_hash | string | 是 | 指令的 MD5 哈希（前8位） |
| task_name | string | 否 | 任务名称 |
| app_context | object | 否 | 应用上下文信息 |
| action_structure | array | 是 | 动作类型序列 |
| actions | array | 是 | 具体动作列表 |
| success | boolean | 是 | 操作是否成功 |
| source | string | 是 | 来源：vlm / fast_path |
| step_id | integer | 否 | 步骤编号 |

### operation_clusters.json 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| cluster_id | string | 是 | 聚类唯一标识 |
| pattern | object | 是 | 聚类模式（instruction_pattern, app_context, action_structure） |
| members | array | 是 | 成员 log_id 列表 |
| sample_instructions | array | 是 | 示例指令（最多5个） |
| sample_actions | array | 是 | 示例动作（最多10个） |
| count | integer | 是 | 成员数量 |
| status | string | 是 | 状态：candidate / approved / rejected |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |

### learned_skills.json 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 技能唯一标识 |
| name | string | 是 | 技能名称 |
| description | string | 否 | 描述信息 |
| source | string | 是 | 来源：learned / manual |
| trigger | object | 是 | 触发条件（patterns, app_context） |
| actions | array | 是 | 动作序列 |
| enabled | boolean | 是 | 是否启用 |
| confidence | number | 是 | 置信度（0.5-1.0） |
| cluster_id | string | 否 | 来源聚类ID |
| created_at | string | 否 | 创建时间 |

---

*文档生成时间: 2026-04-02*