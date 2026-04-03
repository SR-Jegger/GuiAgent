# GuiAgent 技能学习机制实现文档

**日期**: 2026-04-01

---

## 一、项目背景

### 1.1 项目概述

GuiAgent 是一个基于 VLM（视觉语言模型）的 GUI 自动化项目，使用 LangGraph 构建状态图执行流程。

**已实现的核心功能：**
- VLM 视觉推理 + 动作执行循环
- 多步骤任务分解（sub_steps）
- Fast Path 规则匹配加速
- FastAPI 热启动服务
- 任务取消机制
- LangGraph 状态管理

### 1.2 改进方向分析

通过代码分析，识别出以下改进方向：

| 优先级 | 改进项 | 说明 |
|--------|--------|------|
| P0 | 测试覆盖 | 添加单元测试/集成测试/E2E测试 |
| P0 | 规则系统扩展 | 解决坐标硬编码、触发条件单一问题 |
| **P1** | **技能学习机制** | **自动从VLM操作中学习，生成可复用规则** |
| P1 | 验证机制 | 动作执行后验证是否成功 |
| P2 | 上下文传递 | 多步骤任务全局上下文传递 |
| P3 | 日志系统 | 结构化日志 |
| P3 | 安全性 | API key 环境变量管理 |
| P3 | 错误恢复 | 智能恢复策略 |

**用户选择优先实现：技能学习机制**

---

## 二、技能学习机制设计

### 2.1 设计目标

```
VLM 成功操作
    ↓
操作记录器（自动记录）
    ↓
聚类引擎（识别重复模式）
    ↓
候选池（达到阈值入池）
    ↓
人工审核（API接口）
    ↓
技能生成器（生成规则）
    ↓
技能库（Fast Path使用）
```

### 2.2 核心价值

- **减少VLM调用**：高频操作自动优化为规则
- **降低成本**：规则执行比VLM推理便宜
- **提升速度**：Fast Path直接执行，无需等待模型推理
- **持续学习**：系统越用越智能

---

## 三、Phase 1: 操作记录器

### 3.1 功能说明

自动记录每次 VLM 执行的操作，为后续学习提供数据基础。

### 3.2 新建文件

#### `learning/__init__.py`
```python
"""
Learning module for GUI Agent skill acquisition.
"""
from learning.operation_logger import OperationLogger
from learning.cluster_engine import ClusterEngine
from learning.skill_generator import SkillGenerator

__all__ = ["OperationLogger", "ClusterEngine", "SkillGenerator"]
```

#### `learning/operation_logger.py`

**核心类：`OperationLogger`**

```python
class OperationLogger:
    def __init__(self, log_dir: str = "data/logs"):
        self.log_file = os.path.join(log_dir, "operation_logs.jsonl")
    
    def log(self, instruction: str, actions: list, success: bool, ...):
        """记录单次操作到 JSONL 文件"""
        entry = {
            "log_id": uuid,
            "timestamp": datetime,
            "instruction": instruction,
            "instruction_hash": md5_hash,
            "app_context": {...},
            "action_structure": ["click", "type", ...],
            "actions": [...],
            "success": True,
            "source": "vlm"
        }
        self._append_log(entry)
    
    def log_from_state(self, state: dict, actions: list, ...):
        """从 Agent 状态提取信息并记录"""
```

**日志数据结构：**
```json
{
    "log_id": "uuid",
    "timestamp": "2026-04-01T10:30:00",
    "instruction": "发送消息给张三",
    "instruction_hash": "abc123",
    "task_name": "wechat_send",
    "app_context": {
        "active_window": "微信",
        "window_title": "微信 - 张三"
    },
    "action_structure": ["click", "type", "click"],
    "actions": [
        {"type": "click", "coordinate": [100, 200]},
        {"type": "type", "text": "你好"}
    ],
    "success": true,
    "source": "vlm"
}
```

#### `utils/app_context.py`

**跨平台应用上下文检测：**

```python
def get_active_window_info() -> dict:
    """
    获取当前活动窗口信息
    支持 Windows, macOS, Linux (X11)
    """
    import pywinctl
    window = pywinctl.getActiveWindow()
    return {
        "window_title": window.title,
        "process_id": pid,
        "process_name": process_name
    }
```

### 3.3 修改文件

#### `nodes/execution_node.py`

```python
# 新增导入
try:
    from learning import OperationLogger
    _logger_available = True
except ImportError:
    _logger_available = False

# 在 history 更新后添加日志记录
if not fast_path_matched:
    history_entry = {...}
    history.append(history_entry)
    
    # Log operation for skill learning
    if _logger_available and executed_actions:
        try:
            logger = OperationLogger()
            logger.log_from_state(
                state=state,
                actions=executed_actions,
                success=True,
                source="vlm",
            )
        except Exception as e:
            print(f"[EXECUTION] Warning: Failed to log operation: {e}")
```

---

## 四、Phase 2: 聚类引擎

### 4.1 功能说明

识别日志中重复出现的同类操作，生成候选技能。

### 4.2 新建文件

#### `learning/cluster_engine.py`

**核心类：`ClusterEngine`**

```python
class ClusterEngine:
    def scan_and_cluster(self, min_cluster_size: int = 3) -> list:
        """
        扫描日志并聚类
        
        流程：
        1. 加载成功VLM操作
        2. 尝试将新日志添加到已有聚类
        3. 对剩余日志创建新聚类
        """
        # Step 1: 加载日志
        logs = self.logger.load_logs(limit=10000)
        vlm_logs = [log for log in logs if log.success and log.source == "vlm"]
        
        # Step 2: 更新已有聚类
        for cluster in self.clusters:
            for log in unclustered:
                if self._matches_cluster_pattern(log, cluster.pattern):
                    cluster.members.append(log.log_id)
                    cluster.count += 1
        
        # Step 3: 创建新聚类
        for log1 in unclustered:
            similar = [log1]
            for log2 in unclustered[i+1:]:
                if is_same_operation(log1, log2):
                    similar.append(log2)
            if len(similar) >= min_cluster_size:
                new_clusters.append(self._create_cluster(similar))
    
    def _matches_cluster_pattern(self, log: dict, pattern: dict) -> bool:
        """检查日志是否匹配聚类模式"""
        # 动作结构匹配
        if log.action_structure != pattern.action_structure:
            return False
        # 应用上下文匹配
        if pattern.app_context not in log.app_context.active_window:
            return False
        # 指令模式匹配（正则）
        if not re.match(pattern.instruction_pattern, log.instruction):
            return False
        return True
```

#### `learning/similarity.py`

**相似度计算工具：**

```python
def is_same_operation(op1: dict, op2: dict) -> bool:
    """
    判断两个操作是否"相同"
    
    条件：
    1. 应用上下文相似（同一应用）
    2. 指令语义相似 > 0.6
    3. 动作结构相似 > 0.8
    """
    # 应用上下文检查
    if instruction_similarity(title1, title2) < 0.3:
        return False
    
    # 指令相似度
    if instruction_similarity(instr1, instr2) < 0.6:
        return False
    
    # 动作结构相似度
    if jaccard_similarity(set(struct1), set(struct2)) < 0.8:
        return False
    
    return True

def instruction_similarity(instr1: str, instr2: str) -> float:
    """Jaccard 相似度计算"""
    tokens1 = tokenize_instruction(instr1)
    tokens2 = tokenize_instruction(instr2)
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)
```

### 4.3 聚类数据结构

```json
{
    "cluster_id": "cluster_abc123",
    "pattern": {
        "instruction_pattern": "发送消息给.*",
        "app_context": "微信",
        "action_structure": ["click", "type", "click"]
    },
    "members": ["log_id_1", "log_id_2", "log_id_3"],
    "sample_instructions": ["发送消息给张三", "发送消息给李四"],
    "sample_actions": [...],
    "count": 5,
    "status": "candidate"
}
```

---

## 五、Phase 3: 候选池管理与审核API

### 5.1 功能说明

提供人工审核接口，确认候选技能是否入库。

### 5.2 修改文件

#### `app/server.py`

**新增 API 端点：**

```python
# 列出候选技能
@app.get("/api/v1/skills/candidates")
async def list_candidate_skills():
    candidates = _cluster_engine.get_candidates()
    return {"total": len(candidates), "candidates": [...]}

# 获取候选详情
@app.get("/api/v1/skills/candidates/{cluster_id}")
async def get_candidate_skill(cluster_id: str):
    cluster = _cluster_engine.get_cluster(cluster_id)
    return cluster

# 批准候选
@app.post("/api/v1/skills/candidates/{cluster_id}/approve")
async def approve_candidate_skill(cluster_id: str, request: ApproveRequest):
    _cluster_engine.approve_cluster(cluster_id)
    # 生成技能规则
    generator = SkillGenerator()
    skill = generator.generate_skill(cluster)
    generator.save_skill(skill)
    return {"success": True, "skill_id": skill.id}

# 拒绝候选
@app.post("/api/v1/skills/candidates/{cluster_id}/reject")
async def reject_candidate_skill(cluster_id: str, request: RejectRequest):
    _cluster_engine.reject_cluster(cluster_id, reason=request.reason)
    return {"success": True}

# 手动触发聚类
@app.post("/api/v1/skills/cluster")
async def trigger_clustering(min_cluster_size: int = 3):
    new_clusters = _cluster_engine.scan_and_cluster(min_cluster_size)
    return {"new_clusters": len(new_clusters)}

# 统计信息
@app.get("/api/v1/skills/stats")
async def get_skill_stats():
    return {
        "clusters": _cluster_engine.get_stats(),
        "operations": logger.get_stats()
    }

# 列出已入库技能
@app.get("/api/v1/skills")
async def list_skills():
    return {"total": ..., "skills": [...]}
```

---

## 六、Phase 4: 技能生成器

### 6.1 功能说明

从批准的聚类生成可复用的技能规则，处理坐标合并、参数提取等。

### 6.2 新建文件

#### `learning/skill_generator.py`

**核心类：`SkillGenerator`**

```python
class SkillGenerator:
    def generate_skill(self, cluster: dict) -> dict:
        """从聚类生成技能规则"""
        skill = {
            "id": f"learned_{uuid}",
            "name": self._generate_name(app_context, action_structure),
            "description": f"Auto-learned from {count} operations",
            "source": "learned",
            "trigger": {
                "patterns": self._generate_trigger_patterns(instructions),
                "app_context": [app_context]
            },
            "actions": self._generate_actions(sample_actions),
            "enabled": True,
            "confidence": self._calculate_confidence(cluster)
        }
        return skill
    
    def _generate_trigger_patterns(self, instructions: list) -> list:
        """将指令转换为正则模式"""
        for instr in instructions:
            pattern = re.escape(instr)
            # 数字泛化：第3次 → 第\d+次
            pattern = re.sub(r"\\d+", r"\\d+", pattern)
            # 引号内容泛化：'张三' → '.*?'
            pattern = re.sub(r'"[^"]*"', r'".*?"', pattern)
        return patterns
    
    def _generate_actions(self, actions: list) -> list:
        """生成动作序列"""
        for action in actions:
            if action.type == "click":
                return {"type": "click", "coordinate": [...]}
            elif action.type == "type":
                text = action.text
                if len(text) < 10:
                    return {"type": "type", "text": text}  # 短文本保持
                else:
                    return {"type": "type", "text": "{{text_param}}"}  # 参数化
    
    def _calculate_confidence(self, cluster: dict) -> float:
        """计算置信度（基于操作数量）"""
        count = cluster.count
        return min(count / 10, 1.0) * 0.5 + 0.5
```

### 6.3 生成的技能示例

**输入聚类：**
```json
{
    "pattern": {
        "instruction_pattern": "发送消息给.*",
        "app_context": "微信",
        "action_structure": ["click", "type", "click"]
    },
    "sample_instructions": ["发送消息给张三", "发送消息给李四"],
    "sample_actions": [
        {"type": "click", "coordinate": [100, 200]},
        {"type": "type", "text": "你好"},
        {"type": "click", "coordinate": [300, 400]}
    ],
    "count": 5
}
```

**输出技能：**
```json
{
    "id": "learned_1a2b3c4d",
    "name": "微信_点击_输入_点击",
    "description": "Auto-learned skill from 5 operations",
    "source": "learned",
    "trigger": {
        "patterns": ["发送消息给.*"],
        "app_context": ["微信"]
    },
    "actions": [
        {"type": "click", "coordinate": [100, 200]},
        {"type": "type", "text": "{{text_param}}"},
        {"type": "click", "coordinate": [300, 400]}
    ],
    "enabled": true,
    "confidence": 0.75
}
```

---

## 七、Phase 5: 技能库集成

### 7.1 功能说明

将生成的技能集成到 Fast Path 匹配流程。

### 7.2 修改文件

#### `rule_matcher.py`

```python
def load_rules_file(self, filepath: str) -> bool:
    """加载规则文件，自动标记来源"""
    filename = os.path.basename(filepath)
    is_learned = (filename == "learned_skills.json")
    
    for rule in rules:
        if "source" not in rule:
            rule["source"] = "learned" if is_learned else "manual"
        self.rules.append(rule)

def _build_match_result(self, rule: Dict, match: re.Match) -> Dict:
    """构建匹配结果，包含来源和置信度"""
    return {
        "rule_id": rule["id"],
        "rule_name": rule.get("name"),
        "source": rule.get("source", "manual"),
        "confidence": rule.get("confidence", 1.0),
        "actions": actions,
    }
```

#### `nodes/fast_path_node.py`

```python
if result:
    rule_source = result.get("source", "manual")
    rule_confidence = result.get("confidence", 1.0)
    
    source_label = "LEARNED" if rule_source == "learned" else "MANUAL"
    print(f"[FAST_PATH] Matched: {result['rule_name']} [{source_label}]")
    print(f"[FAST_PATH] Confidence: {rule_confidence}")
    
    return {
        "fast_path_matched": True,
        "actions": vlm_actions,
        "rule_source": rule_source,
        "rule_confidence": rule_confidence,
    }
```

---

## 八、文件结构

### 8.1 新建文件

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
```

### 8.2 修改文件

```
nodes/execution_node.py    # Phase 1: 添加日志记录
app/server.py              # Phase 3: 添加审核API
rule_matcher.py            # Phase 5: 加载学习技能
nodes/fast_path_node.py    # Phase 5: 显示技能来源
```

---

## 九、数据流图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           完整数据流                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐                                                       │
│  │ VLM 执行操作  │                                                       │
│  └──────┬───────┘                                                       │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ OperationLogger.log()    │                                           │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌──────────────────────────────────┐                                   │
│  │ data/logs/operation_logs.jsonl   │                                   │
│  └──────┬───────────────────────────┘                                   │
│         │ POST /api/v1/skills/cluster                                   │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ ClusterEngine.scan()     │                                           │
│  │   ├─ 更新已有聚类         │                                           │
│  │   └─ 创建新聚类           │                                           │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌───────────────────────────────────────┐                              │
│  │ data/clusters/operation_clusters.json │                              │
│  │   status: "candidate"                 │                              │
│  └──────┬────────────────────────────────┘                              │
│         │ POST /api/v1/skills/candidates/{id}/approve                   │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ SkillGenerator.generate()│                                           │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌─────────────────────────────────┐                                    │
│  │ rules/learned_skills.json       │                                    │
│  └──────┬──────────────────────────┘                                    │
│         │ RuleMatcher.load_all_rules()                                  │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ Fast Path 匹配生效        │                                           │
│  │ source: "learned"         │                                           │
│  │ confidence: 0.75          │                                           │
│  └──────────────────────────┘                                           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 十、使用方法

### 10.1 自动记录

```bash
# 运行任务，VLM操作自动记录
python run_agent.py --mdpath test_md/test_ui1.md

# 查看日志
cat data/logs/operation_logs.jsonl
```

### 10.2 触发聚类

```bash
# 手动触发聚类
curl -X POST http://localhost:8000/api/v1/skills/cluster

# 查看聚类结果
cat data/clusters/operation_clusters.json
```

### 10.3 审核候选

```bash
# 列出候选
curl http://localhost:8000/api/v1/skills/candidates

# 查看详情
curl http://localhost:8000/api/v1/skills/candidates/cluster_abc

# 批准
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_abc/approve

# 拒绝
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_abc/reject \
  -H "Content-Type: application/json" \
  -d '{"reason": "不准确"}'
```

### 10.4 验证生效

```bash
# 查看已入库技能
cat rules/learned_skills.json

# 再次运行相同任务，观察是否命中 Fast Path
# 日志应显示: [FAST_PATH] Matched: xxx [LEARNED]
```

---

## 十一、当前限制与改进方向

| 限制 | 当前行为 | 改进方向 |
|------|----------|----------|
| 坐标硬编码 | 直接使用样本坐标 | 相对坐标、元素定位 |
| 文本参数化 | 仅长文本参数化 | 智能识别变量部分 |
| 触发模式 | 简单正则泛化 | NLP提取意图模板 |
| 验证机制 | 无 | 添加执行后验证 |
| 测试覆盖 | 无 | 添加单元测试 |

---

## 十二、后续优化建议

1. **测试覆盖** - 添加单元测试、集成测试
2. **验证机制** - 执行动作后验证是否成功
3. **坐标泛化** - 支持相对坐标、元素定位
4. **定时聚类** - 后台自动触发聚类任务
5. **技能统计** - 记录使用次数和成功率，低成功率自动降级

---

## 十三、依赖

```txt
pywinctl>=0.0.5      # 跨平台窗口检测
psutil>=5.9.0        # 进程信息（可选）
sentence-transformers  # 语义相似度（可选，用于高级匹配）
```

---

*文档生成时间: 2026-04-01*