"""
LLM Reviewer - Automatic skill candidate review using LLM.

This module uses LLM to evaluate candidate skills on multiple dimensions:
- Quality: Is the pattern clear and consistent?
- Safety: Does the operation involve risky actions?
- Reusability: Can the skill generalize to similar instructions?

Based on the review, candidates are either:
- Auto-approved (high confidence, low risk)
- Flagged for human review (low confidence or high risk)

Usage:
    from learning.llm_reviewer import LLMReviewer

    reviewer = LLMReviewer(llm_client)
    result = reviewer.review_candidate(cluster)
"""

import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from learning.llm_client import LLMClient


# ============================================================================
# Prompts
# ============================================================================

SKILL_REVIEW_PROMPT = """
你是一个 GUI 自动化技能学习系统的质量审核员。请评估以下候选技能的质量。

## 候选技能信息
- 聚类 ID: {cluster_id}
- 样本数量：{count}
- 应用上下文：{app_context}
- 聚类类型：{cluster_type}
- 提取的触发模式：{trigger_pattern}

## 样本指令
{sample_instructions}

## 样本动作序列
{sample_actions}

## 评估任务
请从以下维度进行评估：

### 1. 质量评估 (Quality)
- 样本指令是否表达相同的用户意图？
- 动作序列是否一致且可复现？
- 触发模式是否合理泛化？
- 评分：0-100

### 2. 安全评估 (Safety)
- 操作是否涉及敏感行为（删除文件、支付、授权、系统设置）？
- 是否有潜在的数据丢失风险？
- 是否需要用户确认步骤？
- 风险等级：low/medium/high
- 评分：0-100（分数越高越安全）

### 3. 可复用性评估 (Reusability)
- 触发模式是否能覆盖合理的指令变体？
- 是否过度特化（只能匹配完全相同的指令）？
- 是否过度泛化（可能误匹配无关指令）？
- 评分：0-100

## 输出格式（JSON）
```json
{{
    "quality": {{
        "score": 85,
        "reason": "样本指令意图一致，动作序列稳定",
        "concerns": []
    }},
    "safety": {{
        "score": 90,
        "risk_level": "low",
        "concerns": [],
        "requires_confirmation": false
    }},
    "reusability": {{
        "score": 80,
        "generalization_quality": "good",
        "pattern_feedback": "模式合理，建议增加参数槽位"
    }},
    "recommendation": {{
        "auto_approve": true,
        "confidence": 0.85,
        "reason": "质量高、风险低、可复用性好"
    }}
}}
```

## 风险等级说明
- **low**: 常规操作（打开应用、点击、输入、导航等），无风险
- **medium**: 可能影响系统状态（保存文件、发送消息、创建/修改数据）
- **high**: 高风险操作（删除、支付、授权、系统设置、敏感数据访问）

## 自动批准条件
同时满足以下条件可自动批准：
- quality.score >= 70
- safety.risk_level == "low"
- recommendation.confidence >= 0.75
"""

SKILL_REVIEW_PROMPT_EN = """
You are a quality reviewer for a GUI automation skill learning system.

## Candidate Skill Information
- Cluster ID: {cluster_id}
- Sample Count: {count}
- App Context: {app_context}
- Cluster Type: {cluster_type}
- Trigger Pattern: {trigger_pattern}

## Sample Instructions
{sample_instructions}

## Sample Actions
{sample_actions}

## Evaluation Tasks
Evaluate the candidate on the following dimensions:

### 1. Quality
- Do sample instructions express the same intent?
- Is the action sequence consistent and reproducible?
- Is the trigger pattern reasonably generalized?
- Score: 0-100

### 2. Safety
- Does the operation involve sensitive actions (delete, payment, authorization, system settings)?
- Is there potential data loss risk?
- Should user confirmation be required?
- Risk Level: low/medium/high
- Score: 0-100 (higher = safer)

### 3. Reusability
- Can the trigger pattern cover reasonable instruction variations?
- Is it over-specialized (only matches identical instructions)?
- Is it over-generalized (may match unrelated instructions)?
- Score: 0-100

## Output Format (JSON)
```json
{{
    "quality": {{
        "score": 85,
        "reason": "...",
        "concerns": []
    }},
    "safety": {{
        "score": 90,
        "risk_level": "low",
        "concerns": [],
        "requires_confirmation": false
    }},
    "reusability": {{
        "score": 80,
        "generalization_quality": "good",
        "pattern_feedback": "..."
    }},
    "recommendation": {{
        "auto_approve": true,
        "confidence": 0.85,
        "reason": "..."
    }}
}}
```
"""


# ============================================================================
# LLM Reviewer
# ============================================================================

class LLMReviewer:
    """
    Reviews candidate skills using LLM.

    Features:
    - Multi-dimensional evaluation (quality, safety, reusability)
    - Auto-approve vs human-review routing
    - Review history tracking
    """

    def __init__(
        self,
        llm_client: LLMClient,
        auto_approve_threshold: float = 0.75,
        min_quality_score: int = 70,
        language: Optional[str] = None,
    ):
        """
        Initialize the LLM reviewer.

        Args:
            llm_client: LLM client for review
            auto_approve_threshold: Minimum confidence for auto-approve
            min_quality_score: Minimum quality score for auto-approve
            language: Language hint ("zh" or "en", auto-detected if None)
        """
        self.llm_client = llm_client
        self.auto_approve_threshold = auto_approve_threshold
        self.min_quality_score = min_quality_score
        self.language = language

        # Review history
        self._review_history: List[Dict] = []

    def review_candidate(self, cluster: Dict) -> Dict[str, Any]:
        """
        Review a candidate skill.

        Args:
            cluster: Cluster dict with pattern, samples, etc.

        Returns:
            Review result dict with decision and review details
        """
        pattern = cluster.get("pattern", {})

        # Format sample instructions
        sample_instructions = cluster.get("sample_instructions", [])
        formatted_instructions = "\n".join(f"- {instr}" for instr in sample_instructions)

        # Format sample actions
        sample_actions = cluster.get("sample_actions", [])
        formatted_actions = json.dumps(sample_actions, ensure_ascii=False, indent=2)

        # Detect language
        if self.language is None:
            has_chinese = any(
                '\u4e00' <= c <= '\u9fff'
                for instr in sample_instructions
                for c in instr
            )
            language = "zh" if has_chinese else "en"
        else:
            language = self.language

        # Select prompt
        prompt_template = SKILL_REVIEW_PROMPT if language == "zh" else SKILL_REVIEW_PROMPT_EN

        # Format prompt
        prompt = prompt_template.format(
            cluster_id=cluster.get("cluster_id", "unknown"),
            count=cluster.get("count", 0),
            app_context=pattern.get("app_context", "unknown"),
            cluster_type=cluster.get("cluster_type", "unknown"),
            trigger_pattern=pattern.get("instruction_pattern", "not extracted"),
            sample_instructions=formatted_instructions,
            sample_actions=formatted_actions,
        )

        # Call LLM
        messages = [{"role": "user", "content": prompt}]

        try:
            review_result = self.llm_client.chat_json(messages=messages)

            # Validate result structure
            review_result = self._validate_review_result(review_result)

            # Make routing decision
            decision = self._make_decision(review_result, cluster)

            # Add metadata
            review_result["reviewed_at"] = datetime.now().isoformat()
            review_result["cluster_id"] = cluster.get("cluster_id")
            review_result["decision"] = decision

            # Store in history
            self._review_history.append(review_result)

            print(f"[LLMReviewer] Reviewed cluster {cluster.get('cluster_id')}: "
                  f"decision={decision}, confidence={review_result.get('recommendation', {}).get('confidence', 0)}")

            return review_result

        except Exception as e:
            print(f"[LLMReviewer] Review failed: {e}")
            return {
                "decision": "requires_human_review",
                "reason": f"Review failed: {e}",
                "reviewed_at": datetime.now().isoformat(),
                "cluster_id": cluster.get("cluster_id"),
                "error": str(e),
            }

    def _validate_review_result(self, result: Dict) -> Dict:
        """Validate and fill missing fields in review result."""
        # Ensure required sections exist
        if "quality" not in result:
            result["quality"] = {"score": 50, "reason": "Not evaluated", "concerns": []}
        if "safety" not in result:
            result["safety"] = {"score": 50, "risk_level": "medium", "concerns": [], "requires_confirmation": True}
        if "reusability" not in result:
            result["reusability"] = {"score": 50, "generalization_quality": "fair", "pattern_feedback": ""}
        if "recommendation" not in result:
            result["recommendation"] = {"auto_approve": False, "confidence": 0.5, "reason": "Not evaluated"}

        # Ensure recommendation has required fields
        rec = result["recommendation"]
        if "auto_approve" not in rec:
            rec["auto_approve"] = False
        if "confidence" not in rec:
            rec["confidence"] = 0.5
        if "reason" not in rec:
            rec["reason"] = "Not specified"

        return result

    def _make_decision(
        self,
        review_result: Dict,
        cluster: Dict
    ) -> str:
        """
        Make routing decision based on review result.

        Returns one of:
        - "auto_approved": Can be automatically approved
        - "requires_human_review": Needs human review
        - "rejected": Should be rejected (high risk)
        """
        quality = review_result.get("quality", {})
        safety = review_result.get("safety", {})
        recommendation = review_result.get("recommendation", {})

        # Check for high risk
        if safety.get("risk_level") == "high":
            return "rejected"

        # Check auto-approve conditions
        quality_score = quality.get("score", 0)
        confidence = recommendation.get("confidence", 0)
        risk_level = safety.get("risk_level", "medium")

        if (quality_score >= self.min_quality_score and
            risk_level == "low" and
            confidence >= self.auto_approve_threshold):
            return "auto_approved"

        # Default to human review
        return "requires_human_review"

    def batch_review(
        self,
        clusters: List[Dict],
        progress_callback=None
    ) -> List[Dict]:
        """
        Review multiple candidates in batch.

        Args:
            clusters: List of cluster dicts
            progress_callback: Optional callback(current, total) for progress

        Returns:
            List of review results
        """
        results = []
        for i, cluster in enumerate(clusters):
            result = self.review_candidate(cluster)
            results.append(result)

            if progress_callback:
                progress_callback(i + 1, len(clusters))

        return results

    def get_review_stats(self) -> Dict:
        """
        Get statistics about reviewed candidates.

        Returns:
            Stats dict with counts and averages
        """
        if not self._review_history:
            return {"total": 0}

        total = len(self._review_history)
        auto_approved = sum(1 for r in self._review_history if r.get("decision") == "auto_approved")
        human_review = sum(1 for r in self._review_history if r.get("decision") == "requires_human_review")
        rejected = sum(1 for r in self._review_history if r.get("decision") == "rejected")

        avg_quality = sum(r.get("quality", {}).get("score", 0) for r in self._review_history) / total
        avg_confidence = sum(r.get("recommendation", {}).get("confidence", 0) for r in self._review_history) / total

        return {
            "total": total,
            "auto_approved": auto_approved,
            "requires_human_review": human_review,
            "rejected": rejected,
            "avg_quality_score": round(avg_quality, 2),
            "avg_confidence": round(avg_confidence, 2),
        }

    def clear_history(self):
        """Clear review history."""
        self._review_history = []


# ============================================================================
# Helper Functions
# ============================================================================

def create_reviewer(
    llm_client: Optional[LLMClient] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> LLMReviewer:
    """
    Create an LLMReviewer instance.

    Args:
        llm_client: Existing LLM client (if provided, other connection params ignored)
        base_url: API base URL (if llm_client not provided)
        api_key: API key (if llm_client not provided)
        model: Model name (if llm_client not provided)
        **kwargs: Additional arguments for LLMReviewer

    Returns:
        Configured LLMReviewer instance
    """
    if llm_client is None:
        from learning.llm_client import create_llm_client
        llm_client = create_llm_client(base_url=base_url, api_key=api_key, model=model)

    return LLMReviewer(llm_client, **kwargs)
