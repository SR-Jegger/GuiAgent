# LLM 序列聚类流程说明

本文档描述了 GuiAgent 中 LLM 序列聚类的完整流程。

---

## 流程概览

```
原始日志 logs
    ↓
[阶段1] 按 instruction_hash 分组
    ↓
判断是否序列操作（连续step_id + 时间间隔）
    ↓
分割执行实例 → 形成序列列表 sequences
    ↓
[阶段2] 计算语义向量
    ↓
DBSCAN 聚类（第一次相似度验证：整体指令）
    ↓
[阶段3] 验证：按 action_structure 分组
    ↓
验证：按 instruction 前缀分组
    ↓
前缀语义相似度验证（第二次相似度验证：前缀）
    ↓
合并相似前缀组
    ↓
[阶段4] 提取 pattern（LLM 或启发式）
    ↓
生成聚类结果 → 保存到 operation_clusters.json
```

---

## 阶段 1：分组（Grouping）

**入口方法**: `_group_logs_into_sequences(logs)`

**代码位置**: `learning/llm_cluster_engine.py`

### 步骤

#### 1.1 按 instruction_hash 分组

```python
key = (instruction_hash, task_name)
```

相同指令文本的操作被分到同一组。这是第一步粗分组，确保语义完全相同的操作先在一起。

#### 1.2 判断是否为序列操作

**方法**: `_is_sequence_operation()`

判断条件：
- 检查是否有连续的 step_id（如 step 7 → step 8）
- 时间间隔 < 30 秒（`MAX_SEQUENCE_GAP_SECONDS`）

如果满足条件，进入下一步；否则跳过（视为单操作）。

#### 1.3 分割执行实例

**方法**: `_split_into_instances()`

根据以下条件划分不同的执行实例：
- step_id 重置（如从 8 降到 1）
- 时间间隔 > 30 秒

每个实例形成一条序列记录。

### 输出

序列列表，每条序列包含：
- `instruction`: 指令文本
- `instruction_hash`: 指令哈希
- `actions`: 动作列表
- `action_structure`: 动作结构（如 `['left_click', 'type']`）
- `app_context`: 应用上下文

---

## 阶段 2：语义聚类（Semantic Clustering）

**入口方法**: `cluster_sequences()`

**代码位置**: `learning/llm_cluster_engine.py`

### 步骤

#### 2.1 构建语义文本

**方法**: `_build_sequence_semantic_text()`

```python
text = f"在{app_context}中{instruction}，执行动作序列：{actions}"
```

将序列信息转换为自然语言文本，便于语义模型理解。

#### 2.2 计算语义向量

**方法**: `embedding_model.encode_batch()`

使用 `gte-multilingual-base` 模型计算文本的向量表示。

#### 2.3 DBSCAN 聚类

```python
DBSCAN(
    eps=1 - similarity_threshold,  # 默认 0.25
    min_samples=min_cluster_size,  # 默认 2
    metric='cosine'
)
```

- `eps`: 邻域半径，由相似度阈值推导
- `metric='cosine'`: 使用余弦距离
- 自动发现聚类数量，无需预设

### 输出

DBSCAN 标签，语义相似的序列被分到同一聚类。

---

## 阶段 3：验证（Validation）

**入口方法**: `_validate_sequence_cluster()`

**代码位置**: `learning/llm_cluster_engine.py`

### 3.1 按 action_structure 分组

```python
# 示例
('left_click', 'type') → [seq1, seq2, ...]
('left_click', 'type', 'key') → [seq3, seq4, ...]
```

不同动作结构的序列不会混在一起。

### 3.2 按 instruction 前缀分组

**方法**: `_group_by_instruction_prefix()`

提取指令的结构前缀，初步分组：

```python
# 示例
"打击/侦察目标一栏输入xxx" → 前缀: "打击/侦察目标一栏输入"
"打击/侦察目标中输入xxx" → 前缀: "打击/侦察目标中输入"
"任务名称一栏输入xxx" → 前缀: "任务名称一栏输入"
```

**前缀提取方法**: `_extract_instruction_prefix()`

根据常见动作动词（输入、点击、选择等）的位置来确定前缀边界。

### 3.3 前缀语义相似度验证（关键优化）

**方法**: `_merge_similar_prefix_groups()`

**这是核心优化点！** 对初步分组的前缀进行语义相似度验证，合并相似的前缀组。

#### 为什么需要这一步？

仅靠整体指令的语义聚类，会出现以下问题：

| 指令对 | 相似度 | 0.75阈值 | 应聚类? |
|--------|--------|----------|---------|
| "任务名称一栏输入" vs "打击/侦察目标一栏输入" | 0.72 | 不聚类 | **否** ✓ |
| "打击/侦察目标一栏输入" vs "打击/侦察目标中输入" | 0.73 | 不聚类 | **是** ✗ |

问题：同类操作但表达略有不同时，整体相似度可能低于阈值，导致被分开。

#### 解决方案：两次相似度验证

```
第一次验证（整体指令语义相似度）：
    - DBSCAN 根据整体指令的语义向量聚类
    - 阈值可以宽松（如 0.70）
    - 把语义相似的操作初步聚在一起

第二次验证（前缀语义相似度）：
    - 对前缀组进行合并检查
    - 计算 "打击/侦察目标一栏输入" vs "打击/侦察目标中输入" 的相似度
    - 如果 > 阈值（如 0.75），则合并这两个组
    - "任务名称一栏输入" vs "打击/侦察目标一栏输入" 相似度低，保持分开
```

#### 实际效果

```
前缀对                              相似度    是否合并
"在打击/侦察目标一栏输入" vs "打击/侦察目标中输入"  0.95    ✓ 合并
"在打击/侦察目标一栏输入" vs "打击/侦察目标设为"    0.89    ✓ 合并
"任务名称一栏输入" vs "打击/侦察目标..."           0.60    ✗ 不合并
```

#### 合并算法

使用 **Union-Find（并查集）** 算法处理前缀合并：

```python
def _merge_similar_prefix_groups(self, prefix_groups, threshold=0.75):
    # 1. 计算所有前缀对的相似度
    # 2. 相似度 >= threshold 的前缀对加入合并列表
    # 3. 使用 Union-Find 将相似前缀连通
    # 4. 按连通分量合并序列组
```

### 3.4 过滤小聚类

```python
if len(sequences) >= min_cluster_size:
    # 创建聚类
```

序列数量不足的组被过滤掉。

### 输出

验证后的聚类列表，确保：
- 动作结构一致
- 指令意图相似（通过两次相似度验证）

---

## 阶段 4：创建聚类 & 提取 Pattern

**入口方法**: `_create_sequence_cluster()`

**代码位置**: `learning/llm_cluster_engine.py`

### 4.1 提取 instruction pattern

**方法**: `_extract_pattern()`

支持两种模式：

#### 模式 1：LLM 提取（推荐）

**条件**: `use_llm_pattern=True` 且 `llm_client` 可用

**方法**: `extract_pattern_with_llm()`

**代码位置**: `learning/llm_pattern_extractor.py`

**Prompt 设计要点**：
- 优先宽松模式：只保留关键触发前缀
- 单捕获组：使用 `(.*)` 捕获所有可变内容
- 不过度细分：避免生成过于严格的正则表达式

```python
# 示例
输入: [
    "打击/侦察目标一栏输入温哥华基地",
    "打击/侦察目标中输入巴厘岛北部",
    "打击/侦察目标设为洛杉矶市政府"
]
LLM 输出: "打击/侦察目标(.*)"
```

#### 模式 2：简单启发式（Fallback）

**方法**: `extract_pattern_from_instructions()`

**代码位置**: `learning/similarity.py`

**算法**：
1. 找最长公共前缀
2. 找最长公共后缀（如有）
3. 生成正则表达式模式

```python
# 示例
输入: ["打击/侦察目标一栏输入温哥华", "打击/侦察目标一栏输入墨西哥"]
输出: "打击/侦察目标一栏输入(.*)"
```

**注意**：如果指令前缀差异大（如"在打击/侦察目标..."和"打击/侦察目标..."），最长公共前缀可能为空，返回 `(.*)`。这时应使用 LLM 提取。

### 4.2 统计最常见的 action_structure

```python
structure_counts = {}
for seq in sequences:
    struct = tuple(seq.get("action_structure", []))
    structure_counts[struct] = structure_counts.get(struct, 0) + 1
```

### 4.3 统计最常见的 app_context

收集所有应用上下文，取出现频率最高的。

### 4.4 收集样本数据

- `sample_instructions`: 指令样本（前 5 条）
- `sample_actions`: 动作样本（前 15 个）
- `sample_sequences`: 完整序列样本（前 3 条）

### 输出

完整的聚类字典，包含：

```json
{
  "cluster_id": "cluster_seq_llm_xxxxx",
  "cluster_type": "sequence_llm",
  "pattern": {
    "instruction_pattern": "打击/侦察目标(.*)",
    "app_context": "React App - 个人",
    "action_structure": ["left_click", "type"]
  },
  "count": 5,
  "sample_instructions": [...],
  "sample_sequences": [...],
  "status": "candidate"
}
```

---

## 关键设计说明

| 设计点 | 说明 | 目的 |
|--------|------|------|
| instruction_hash 分组 | 相同指令先分一组 | 确保完全相同的操作在一起 |
| 语义聚类（第一次验证） | DBSCAN + embedding 模型 | 不同文本但语义相似的序列初步聚类 |
| 前缀语义验证（第二次验证） | 计算前缀相似度 | 合并表达略有不同但意图相同的操作 |
| LLM pattern 提取 | 使用 LLM 生成正则表达式 | 处理复杂模式，生成合理的宽松模式 |
| action_structure 验证 | 按动作结构分组 | 确保操作结构一致 |

---

## 两次相似度验证详解

### 为什么需要两次验证？

**单次验证的问题**：

如果只用整体指令的语义相似度：
- 阈值太高（0.75）：同类但表达不同的操作被分开
- 阈值太低（0.70）：不同类型的操作被错误合并

**两次验证的优势**：

```
整体相似度 < 0.75 的操作 → 可能是同类（表达不同）或不同类（意图不同）

如何区分？
→ 再看前缀相似度：
   - 前缀相似度高（> 0.75）→ 同类，合并
   - 前缀相似度低（< 0.75）→ 不同类，分开
```

### 示例

| 操作类型 | 整体相似度 | 前缀相似度 | 最终结果 |
|----------|-----------|-----------|---------|
| 打击/侦察目标一栏输入 vs 打击/侦察目标中输入 | 0.73 | 0.95 | **合并** ✓ |
| 任务名称一栏输入 vs 打击/侦察目标一栏输入 | 0.72 | 0.68 | **分开** ✓ |

---

## 参数配置

### 聚类参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `similarity_threshold` | 0.75 | 整体指令语义相似度阈值 |
| `min_cluster_size` | 2 | 最小聚类大小 |
| `use_llm_pattern` | True | 是否使用 LLM 提取模式 |
| `llm_model` | "local_qwen8b" | LLM 模型名称 |
| `MAX_SEQUENCE_GAP_SECONDS` | 30 | 序列内操作最大时间间隔 |

### 前缀验证参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `prefix_similarity_threshold` | 0.75 | 前缀语义相似度阈值 |

---

## Dashboard 使用

在 Dashboard 的 **LLM 语义聚类** 区域，可以配置以下参数：

1. **相似度阈值**：控制整体指令的语义相似度要求
2. **最小聚类大小**：控制聚类的最小序列数量
3. **使用 LLM 提取模式**：勾选后使用 LLM 生成更智能的正则表达式
4. **LLM 模型**：选择用于 pattern 提取的模型

---

## 相关文件

- 聚类引擎: `learning/llm_cluster_engine.py`
- 相似度计算: `learning/similarity.py`
- 模式提取: `learning/llm_pattern_extractor.py`
- LLM 客户端: `learning/llm_client.py`
- 聚类结果: `data/clusters/operation_clusters.json`
- 模型配置: `nodes/model_config.json`