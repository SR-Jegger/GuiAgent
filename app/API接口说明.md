# GUI Agent API 接口说明

## 1. 任务执行接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/v1/tasks` | POST | 创建新的 GUI 自动化任务，返回 task_id |
| `/api/v1/tasks` | GET | 列出所有任务，可按状态过滤 |
| `/api/v1/tasks/{task_id}` | GET | 获取指定任务的状态和结果 |
| `/api/v1/tasks/{task_id}/cancel` | POST | 取消正在运行或待执行的任务 |
| `/api/v1/health` | GET | 健康检查，返回服务器状态 |

---

## 2. 技能学习接口（基础版）

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/v1/skills/candidates` | GET | 列出所有待审批的候选技能 |
| `/api/v1/skills/candidates/{cluster_id}` | GET | 获取指定候选技能的详情 |
| `/api/v1/skills/candidates/{cluster_id}/approve` | POST | 批准候选技能，写入 learned_skills.json |
| `/api/v1/skills/candidates/{cluster_id}/reject` | POST | 拒绝候选技能 |
| `/api/v1/skills/cluster` | POST | 手动触发聚类扫描，发现新候选技能 |
| `/api/v1/skills/stats` | GET | 获取技能学习统计信息 |
| `/api/v1/skills` | GET | 列出所有已批准的技能 |

---

## 3. LLM 增强技能学习接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/v1/skills/cluster/llm` | POST | 使用语义嵌入进行 LLM 增强聚类 |
| `/api/v1/skills/candidates/{cluster_id}/review` | POST | 用 LLM 评估候选技能（质量、安全性、复用性） |
| `/api/v1/skills/candidates/{cluster_id}/auto-approve` | POST | LLM 自动审批（高置信度+低风险时自动批准） |
| `/api/v1/skills/review-queue` | GET | 获取需要人工审批的候选队列 |
| `/api/v1/skills/llm-stats` | GET | 获取 LLM 技能学习统计信息 |

---

## 典型工作流程

1. 任务执行 → 操作日志记录
2. `/skills/cluster` 或 `/skills/cluster/llm` → 发现候选技能
3. `/skills/candidates/{id}/review` → LLM 评估
4. `/skills/candidates/{id}/approve` 或 `/auto-approve` → 批准并写入技能库