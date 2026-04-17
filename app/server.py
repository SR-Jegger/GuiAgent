"""
FastAPI server for GUI Agent - Hot-start mode

This server keeps the agent graph compiled and ready,
allowing for fast task execution without cold-start overhead.

Architecture:
- Routing Layer (this file): HTTP request/response handling
- Application Service Layer: Business logic (app/services/)
  - AgentApplicationService: Task management
  - SkillLearningService: Skill learning operations

Usage:
    uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import os
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Import services
from app.services import AgentApplicationService, TaskStatus, SkillLearningService
from nodes.fast_path_node import get_ocr_locator  # OCR 预加载


# ============================================================================
# Request/Response Models
# ============================================================================

class TaskRequest(BaseModel):
    """创建任务时所需的请求参数"""
    task_name: Optional[str] = "default_task"
    instruction: str
    max_steps: int = 50
    max_retries: int = 3
    add_info: Optional[str] = None
    rules_dir: str = "./rules"


class TaskResponse(BaseModel):
    """任务操作的响应数据结构"""
    task_id: str
    status: str
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict] = None
    error: Optional[str] = None


class ApproveRequest(BaseModel):
    """Request body for approve endpoint"""
    modifications: Optional[Dict] = None


class RejectRequest(BaseModel):
    """Request body for reject endpoint"""
    reason: str = ""


class SkillUpdateRequest(BaseModel):
    """Request body for skill update"""
    enabled: Optional[bool] = None
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_patterns: Optional[List[str]] = None
    app_context: Optional[List[str]] = None
    actions: Optional[List[Dict]] = None


# ============================================================================
# Service Instances
# ============================================================================

# Global service instances
agent_service = AgentApplicationService(max_concurrent=1)
skill_service = SkillLearningService()


# ============================================================================
# Lifespan Management
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown.
    初始化服务层，预加载模型和 OCR。
    """
    # Startup
    print("\n" + "=" * 60)
    print("GUI Agent Server Starting...")
    print("=" * 60)

    # Initialize AgentApplicationService
    await agent_service.initialize()

    # Initialize SkillLearningService
    await skill_service.initialize()

    # Preload OCR model (hot-start optimization)
    print("[Server] Preloading OCR model...")
    get_ocr_locator()

    print("=" * 60)
    print("Server Ready!")
    print("=" * 60 + "\n")

    yield  # Server runs

    # Shutdown
    print("\n[Server] Shutting down...")
    await agent_service.shutdown()
    print("[Server] Services stopped")


# ============================================================================
# FastAPI Application
# ============================================================================

def create_app() -> FastAPI:
    """Create and configure FastAPI application"""

    app = FastAPI(
        title="GUI Agent Server",
        description="Hot-start GUI automation agent server",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files for dashboard
    from fastapi.staticfiles import StaticFiles
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ========================================================================
    # Task API Endpoints (AgentApplicationService)
    # ========================================================================

    @app.post("/api/v1/tasks", response_model=TaskResponse)
    async def create_task(request: TaskRequest):
        """
        Create a new GUI automation task.

        Returns immediately with task_id. Use GET /api/v1/tasks/{task_id} to check status.
        """
        task_id = await agent_service.submit_task(
            instruction=request.instruction,
            task_name=request.task_name,
            max_steps=request.max_steps,
            max_retries=request.max_retries,
            add_info=request.add_info,
            rules_dir=request.rules_dir,
        )

        status = await agent_service.get_task_status(task_id)
        return TaskResponse(
            task_id=task_id,
            status=status["status"],
            created_at=status["created_at"],
        )

    @app.get("/api/v1/tasks", response_model=list[TaskResponse])
    async def list_tasks(status: Optional[TaskStatus] = None):
        """List all tasks, optionally filtered by status"""
        tasks = await agent_service.list_tasks(status_filter=status)
        return [TaskResponse(**t) for t in tasks]

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str):
        """Get task status and result"""
        status = await agent_service.get_task_status(task_id)
        if not status:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskResponse(**status)

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        """Cancel a running or pending task"""
        success = await agent_service.cancel_task(task_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Task cannot be cancelled (already completed/failed or not found)"
            )
        return {"success": True, "task_id": task_id}

    @app.get("/api/v1/health")
    async def health_check():
        """Health check endpoint"""
        tasks = await agent_service.list_tasks()
        running = sum(1 for t in tasks if t["status"] == TaskStatus.RUNNING.value)
        return {
            "status": "healthy",
            "tasks_total": len(tasks),
            "tasks_running": running,
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/dashboard/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the dashboard HTML page"""
        dashboard_path = os.path.join(os.path.dirname(__file__), "static", "dashboard", "index.html")
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "r", encoding="utf-8") as f:
                return f.read()
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # ========================================================================
    # Skill Learning API Endpoints (SkillLearningService)
    # ========================================================================

    @app.get("/api/v1/skills/candidates")
    async def list_candidate_skills():
        """List all candidate skills awaiting approval"""
        result = await skill_service.list_candidate_skills()
        return result

    @app.get("/api/v1/skills/candidates/{cluster_id}")
    async def get_candidate_skill(cluster_id: str):
        """Get details of a specific candidate skill"""
        cluster = await skill_service.get_candidate_skill(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found")
        return cluster

    @app.post("/api/v1/skills/candidates/{cluster_id}/approve")
    async def approve_candidate_skill(cluster_id: str, request: ApproveRequest = None):
        """
        Approve a candidate skill.

        The skill will be converted to a rule and added to the skill library.
        """
        result = await skill_service.approve_candidate_skill(
            cluster_id,
            modifications=request.modifications if request else None
        )

        if not result.get("success"):
            if result.get("error") == "Cluster not found":
                raise HTTPException(status_code=404, detail="Cluster not found")
            elif "not a candidate" in result.get("error", ""):
                raise HTTPException(status_code=400, detail=result["error"])
            else:
                raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.post("/api/v1/skills/candidates/{cluster_id}/reject")
    async def reject_candidate_skill(cluster_id: str, request: RejectRequest = None):
        """Reject a candidate skill"""
        result = await skill_service.reject_candidate_skill(
            cluster_id,
            reason=request.reason if request else ""
        )

        if not result.get("success"):
            raise HTTPException(status_code=404, detail="Cluster not found")

        return result

    @app.post("/api/v1/skills/cluster")
    async def trigger_clustering(min_cluster_size: int = 3, full_scan: bool = False):
        """
        Manually trigger the clustering process.

        This scans operation logs and identifies new candidate skills.
        """
        result = await skill_service.trigger_clustering(min_cluster_size, full_scan)

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.get("/api/v1/skills/stats")
    async def get_skill_stats():
        """Get statistics about skill learning"""
        return await skill_service.get_skill_stats()

    @app.get("/api/v1/skills")
    async def list_skills(cluster_type: str = None):
        """List all approved skills"""
        result = await skill_service.list_skills(cluster_type=cluster_type)
        if not result.get("success", True):  # Default True if no error field
            raise HTTPException(status_code=500, detail=result.get("error"))
        return result

    @app.patch("/api/v1/skills/{skill_id}")
    async def update_skill(skill_id: str, request: SkillUpdateRequest):
        """
        Update a skill's properties.

        Supports updating:
        - enabled: Enable/disable the skill
        - name: Skill name
        - description: Skill description
        - trigger_patterns: Regex patterns for matching
        - app_context: Application context filters
        - actions: Action sequence
        """
        result = await skill_service.update_skill(
            skill_id=skill_id,
            enabled=request.enabled,
            name=request.name,
            description=request.description,
            trigger_patterns=request.trigger_patterns,
            app_context=request.app_context,
            actions=request.actions,
        )

        if not result.get("success"):
            if "not found" in result.get("error", "").lower():
                raise HTTPException(status_code=404, detail=result["error"])
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.delete("/api/v1/skills/{skill_id}")
    async def delete_skill(skill_id: str):
        """Delete a skill from storage"""
        result = await skill_service.delete_skill(skill_id)

        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error"))

        return result

    # ====================================================================
    # LLM-Enhanced Sequence Clustering
    # ====================================================================

    @app.post("/api/v1/skills/cluster/sequences/llm")
    async def trigger_llm_sequence_clustering(
        similarity_threshold: float = 0.75,
        min_cluster_size: int = 2,
        embedding_model: str = None,
        use_llm_pattern: bool = True,
        llm_model: str = "local_qwen8b",
    ):
        """
        Trigger LLM-enhanced sequence clustering.

        This combines sequence detection with semantic embeddings:
        1. Groups operations into sequences by instruction
        2. Uses sentence embeddings for semantic similarity
        3. Clusters similar sequences using DBSCAN
        4. Extracts patterns using LLM (optional) or heuristic
        """
        result = await skill_service.trigger_llm_sequence_clustering(
            similarity_threshold=similarity_threshold,
            min_cluster_size=min_cluster_size,
            embedding_model=embedding_model,
            use_llm_pattern=use_llm_pattern,
            llm_model=llm_model,
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.post("/api/v1/skills/candidates/{cluster_id}/review")
    async def review_candidate_with_llm(cluster_id: str):
        """
        Review a candidate skill using LLM.

        The LLM will evaluate the candidate on:
        - Quality: Pattern clarity and consistency
        - Safety: Risk level assessment
        - Reusability: Generalization quality
        """
        result = await skill_service.review_candidate(cluster_id)

        if not result.get("success"):
            if result.get("error") == "Cluster not found":
                raise HTTPException(status_code=404, detail="Cluster not found")
            elif "LLM components not available" in result.get("error", ""):
                raise HTTPException(status_code=503, detail=result["error"])
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.post("/api/v1/skills/candidates/{cluster_id}/auto-approve")
    async def auto_approve_with_llm(cluster_id: str):
        """
        Auto-approve a candidate skill using LLM review.

        If the LLM review recommends auto-approval (high confidence, low risk),
        the skill will be automatically approved and added to the skill library.
        """
        result = await skill_service.auto_approve_with_llm(cluster_id)

        if not result.get("success") and result.get("decision") != "approved_but_generation_failed":
            if result.get("error") == "Cluster not found":
                raise HTTPException(status_code=404, detail="Cluster not found")
            elif "LLM components not available" in result.get("error", ""):
                raise HTTPException(status_code=503, detail=result["error"])
            elif "not a candidate" in result.get("error", ""):
                raise HTTPException(status_code=400, detail=result["error"])
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    @app.get("/api/v1/skills/review-queue")
    async def get_review_queue():
        """
        Get candidates that require human review.

        Returns candidates that have been reviewed by LLM but
        flagged for human review (low confidence or high risk).
        """
        return await skill_service.get_review_queue()

    @app.get("/api/v1/skills/llm-stats")
    async def get_llm_skill_stats():
        """Get statistics about LLM-enhanced skill learning"""
        return await skill_service.get_llm_skill_stats()

    return app


# Create app instance
app = create_app()