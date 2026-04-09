# LLM 增强型技能学习机制

本文档介绍如何使用大模型（LLM）增强 GUI Agent 的技能学习机制。

## 目录

- [概述](#概述)
- [架构设计](#架构设计)
- [配置指南](#配置指南)
- [API 使用](#api-使用)
- [工作流程](#工作流程)

---

## 概述

LLM 增强型技能学习机制利用大语言模型的语义理解能力，改进传统基于规则的聚类和审核流程：

### 改进点

| 组件 | 传统方法 | LLM 增强方法 |
|------|----------|-------------|
| **聚类引擎** | Jaccard 分词相似度 | 语义嵌入向量 + DBSCAN 聚类 |
| **模式提取** | 简单正则泛化 | LLM 理解意图，识别参数槽位 |
| **候选审核** | 人工审核 | LLM 多维评估 + 自动批准/人工复审路由 |

### 核心优势

1. **语义理解**: "打开浏览器" 和 "启动 Chrome" 能被正确聚类
2. **自动审核**: 高质量、低风险的候选技能可自动批准
3. **智能泛化**: LLM 提取的触发模式更准确，能识别可变参数

---

## 架构设计

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    LLM 增强的技能学习架构                                 │
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
│  ┌───────────────────────────────────────────────┐                       │
│  │ LLM_CLUSTER_ENGINE                            │  ← 语义嵌入聚类       │
│  │   ├─ Embedding: paraphrase-multilingual      │                       │
│  │   ├─ DBSCAN: 自动聚类数量检测                 │                       │
│  │   └─ LLM: 模式提取与泛化                      │                       │
│  └──────┬────────────────────────────────────────┘                       │
│         ↓                                                               │
│  ┌───────────────────────────────────────┐                              │
│  │ candidate_clusters.json               │                              │
│  └──────┬────────────────────────────────┘                              │
│         ↓                                                               │
│  ┌───────────────────────────────────────────────┐                       │
│  │ LLM_REVIEW_ENGINE                             │  ← 多维质量评估       │
│  │   ├─ Quality: 模式清晰度、样本一致性          │                       │
│  │   ├─ Safety: 风险等级评估                     │                       │
│  │   └─ Reusability: 泛化能力评估                │                       │
│  └──────┬────────────────────────────────────────┘                       │
│         ↓                                                               │
│  ┌─────────────────────────────┐                                        │
│  │ 自动批准 (高置信度)          │  ← 无需人工干预                        │
│  │ 人工复审 (低置信度/高风险)   │  ← 需要人工确认                        │
│  └──────┬──────────────────────┘                                        │
│         ↓                                                               │
│  ┌──────────────────────────┐                                           │
│  │ SkillGenerator.generate()│                                           │
│  └──────┬───────────────────┘                                           │
│         ↓                                                               │
│  ┌─────────────────────────────────┐                                    │
│  │ rules/learned_skills.json       │                                    │
│  └─────────────────────────────────┘                                    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 配置指南

### 1. 安装依赖

```bash
# 语义嵌入模型
pip install sentence-transformers

# OpenAI 客户端（已安装则跳过）
pip install openai
```

### 2. 配置环境变量

复制 `.env.example` 到 `.env` 并填写配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# OpenAI 官方 API
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-api-key
LLM_MODEL=gpt-4o-mini

# 或使用本地模型 (Ollama)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
# LLM_MODEL=qwen2.5:32b

# 嵌入模型（推荐支持中文的多语言模型）
LLM_EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2

# 聚类参数
LLM_SIMILARITY_THRESHOLD=0.75
LLM_MIN_CLUSTER_SIZE=3

# 审核参数
LLM_AUTO_APPROVE_THRESHOLD=0.75
LLM_MIN_QUALITY_SCORE=70
```

### 3. 验证配置

启动服务器后，访问健康检查端点：

```bash
# 启动服务器
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

# 检查 LLM 连接
curl http://localhost:8000/api/v1/skills/llm-stats
```

---

## API 使用

### LLM 聚类

```bash
# 触发 LLM 语义聚类
curl -X POST "http://localhost:8000/api/v1/skills/cluster/llm?similarity_threshold=0.75&min_cluster_size=3"
```

响应示例：

```json
{
  "success": true,
  "new_clusters": 2,
  "parameters": {
    "similarity_threshold": 0.75,
    "min_cluster_size": 3,
    "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2"
  },
  "clusters": [
    {
      "cluster_id": "cluster_llm_a1b2c3d4",
      "pattern": {
        "instruction_pattern": " (打开 | 启动).*浏览器",
        "app_context": "Program Manager",
        "action_structure": ["double_click"]
      },
      "count": 5,
      "cluster_type": "llm_semantic"
    }
  ]
}
```

### LLM 审核

```bash
# 审核单个候选技能
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_llm_a1b2c3d4/review

# 自动批准（如果 LLM 推荐）
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_llm_a1b2c3d4/auto-approve
```

响应示例：

```json
{
  "success": true,
  "cluster_id": "cluster_llm_a1b2c3d4",
  "review": {
    "quality": {
      "score": 85,
      "reason": "样本指令意图一致，动作序列稳定"
    },
    "safety": {
      "score": 95,
      "risk_level": "low",
      "concerns": []
    },
    "reusability": {
      "score": 80,
      "generalization_quality": "good"
    },
    "recommendation": {
      "auto_approve": true,
      "confidence": 0.85,
      "reason": "质量高、风险低、可复用性好"
    },
    "decision": "auto_approved"
  }
}
```

### 查看待审核队列

```bash
# 获取需要人工复审的候选技能
curl http://localhost:8000/api/v1/skills/review-queue
```

### 统计信息

```bash
# LLM 审核统计
curl http://localhost:8000/api/v1/skills/llm-stats
```

---

## 工作流程

### 完整技能学习流程

```bash
# 1. 运行任务，自动记录操作
python run_agent.py --mdpath test_md/test_ui1.md

# 2. 触发 LLM 语义聚类
curl -X POST http://localhost:8000/api/v1/skills/cluster/llm

# 3. 查看候选技能
curl http://localhost:8000/api/v1/skills/candidates

# 4. LLM 审核候选
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_xxx/review

# 5a. 如果 LLM 推荐自动批准
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_xxx/auto-approve

# 5b. 如果需要人工复审，查看审核队列
curl http://localhost:8000/api/v1/skills/review-queue

# 6. 人工批准（通过原有 API）
curl -X POST http://localhost:8000/api/v1/skills/candidates/cluster_xxx/approve

# 7. 验证技能已入库
curl http://localhost:8000/api/v1/skills
```

### 自动批准 vs 人工复审

LLM 审核结果分为三类：

| 决策 | 条件 | 后续处理 |
|------|------|----------|
| **auto_approved** | quality >= 70, risk = low, confidence >= 0.75 | 自动批准并生成技能 |
| **requires_human_review** | 置信度不足或中等风险 | 加入人工复审队列 |
| **rejected** | 高风险操作 | 拒绝，需人工重新评估 |

---

## 模块说明

### 核心模块

| 模块 | 功能 |
|------|------|
| `learning/llm_client.py` | 统一 LLM 客户端接口 |
| `learning/llm_cluster_engine.py` | 语义嵌入聚类引擎 |
| `learning/llm_pattern_extractor.py` | LLM 模式提取 |
| `learning/llm_reviewer.py` | LLM 多维审核 |

### 配置项说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LLM_SIMILARITY_THRESHOLD` | 0.75 | 语义相似度阈值，越高聚类越严格 |
| `LLM_MIN_CLUSTER_SIZE` | 3 | 最小聚类大小 |
| `LLM_AUTO_APPROVE_THRESHOLD` | 0.75 | 自动批准置信度阈值 |
| `LLM_MIN_QUALITY_SCORE` | 70 | 自动批准最低质量分 |
| `LLM_EMBEDDING_MODEL` | paraphrase-multilingual-MiniLM-L12-v2 | 嵌入模型 |

---

## 故障排除

### 问题：LLM 连接失败

```
[LLMClient] Connection validation failed: Connection refused
```

**解决方案**：
1. 检查 `LLM_BASE_URL` 和 `LLM_API_KEY` 配置
2. 确认本地模型服务已启动（如使用 Ollama）
3. 检查网络连接

### 问题：嵌入模型加载失败

```
ImportError: sentence-transformers not installed
```

**解决方案**：
```bash
pip install sentence-transformers
```

### 问题：聚类结果为空

**可能原因**：
1. 操作日志不足（需要至少 `min_cluster_size` 条相似操作）
2. 相似度阈值设置过高

**解决方案**：
- 运行更多任务，积累操作日志
- 降低 `similarity_threshold` 参数（如 0.65）

---

*文档更新时间：2026-04-03*
