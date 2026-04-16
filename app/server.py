"""
FastAPI server for GUI Agent - Hot-start mode

This server keeps the agent graph compiled and ready,
allowing for fast task execution without cold-start overhead.

Usage:
    uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import os
import uuid
import time
import json
from typing import Optional, Dict, Any, List
from enum import Enum

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent_graph import build_agent_graph_simple, run_agent_async, AgentState
from utils.utils import get_output_dir
from nodes.fast_path_node import get_ocr_locator  # OCR 预加载


# ============================================================================
# Helper Functions for LLM Skill Learning
# ============================================================================

def _save_llm_clusters(clusters: List[Dict]) -> None:
    """Save LLM-generated clusters to file."""
    clusters_dir = "data/clusters"
    os.makedirs(clusters_dir, exist_ok=True)

    # Load existing clusters
    existing_file = os.path.join(clusters_dir, "operation_clusters.json")
    if os.path.exists(existing_file):
        with open(existing_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"clusters": [], "last_scan": None, "last_scan_log_id": ""}

    # Add new clusters (avoid duplicates by cluster_id)
    existing_ids = {c.get("cluster_id") for c in data.get("clusters", [])}
    for cluster in clusters:
        if cluster.get("cluster_id") not in existing_ids:
            data["clusters"].append(cluster)

    # Save
    with open(existing_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_review_result(cluster_id: str, review_result: Dict) -> None:
    """Save LLM review result to file."""
    review_dir = "data/reviews"
    os.makedirs(review_dir, exist_ok=True)

    review_file = os.path.join(review_dir, f"{cluster_id}.json")
    with open(review_file, "w", encoding="utf-8") as f:
        json.dump(review_result, f, ensure_ascii=False, indent=2)


def _load_review_result(cluster_id: str) -> Optional[Dict]:
    """Load review result for a specific cluster."""
    review_file = f"data/reviews/{cluster_id}.json"
    if os.path.exists(review_file):
        with open(review_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _load_review_results() -> Dict[str, Dict]:
    """Load all review results."""
    review_dir = "data/reviews"
    results = {}

    if os.path.exists(review_dir):
        for filename in os.listdir(review_dir):
            if filename.endswith(".json"):
                cluster_id = filename[:-5]  # Remove .json
                with open(os.path.join(review_dir, filename), "r", encoding="utf-8") as f:
                    results[cluster_id] = json.load(f)

    return results


# ============================================================================
# Enums & Models
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "pending" # Task is created but not yet started
    RUNNING = "running" # Task is currently executing
    COMPLETED = "completed" # Task has completed successfully
    FAILED = "failed" # Task has failed
    CANCELLED = "cancelled" # Task has been cancelled


class TaskRequest(BaseModel):
    """ 创建任务时所需的请求参数 """
    task_name: Optional[str] = "default_task"
    instruction: str
    max_steps: int = 50
    max_retries: int = 3
    add_info: Optional[str] = None
    rules_dir: str = "./rules"


class TaskResponse(BaseModel):
    """ 任务操作的响应数据结构，返回 HTTP 响应数据 """
    task_id: str
    status: str
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict] = None
    error: Optional[str] = None


# ============================================================================
# Task Manager
# ============================================================================

class Task:
    """Represents a single task with its lifecycle"""

    def __init__(
        self,
        task_id: str,
        instruction: str,
        task_name: str = "default_task",
        max_steps: int = 50,
        max_retries: int = 3,
        add_info: Optional[str] = None,
        rules_dir: str = "./rules",
    ):
        self.task_id = task_id
        self.task_name = task_name
        self.instruction = instruction
        self.max_steps = max_steps
        self.max_retries = max_retries
        self.add_info = add_info
        self.rules_dir = rules_dir

        self.status = TaskStatus.PENDING
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None

        self.result: Optional[Dict] = None
        self.error: Optional[str] = None

        # For cancellation
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def cancel(self):
        """Request task cancellation"""
        self._stop_event.set()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """
    Manages all tasks lifecycle.

    Features:
    - Task submission and tracking
    - Concurrent execution control (GUI operations must be serial)
    - Task cancellation support
    """

    def __init__(self, max_concurrent: int = 1):
        self.tasks: Dict[str, Task] = {}
        self.max_concurrent = max_concurrent
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running_count = 0
        self._lock = asyncio.Lock()
        self._model_config: Optional[Dict] = None
        self._compiled_agent = None

    def load_model_config(self, config_path: str = "nodes/model_config.json"):
        """Load model configuration once at startup"""
        import json
        with open(config_path, 'r') as f:
            self._model_config = json.load(f)
        print(f"[TaskManager] Loaded model config from {config_path}")

    def compile_agent(self):
        """Compile the agent graph once at startup (hot-start optimization)"""
        # builder = build_agent_graph() --- IGNORE ---
        builder = build_agent_graph_simple()
        self._compiled_agent = builder.compile()
        print("[TaskManager] Agent graph compiled and cached")

    async def start(self):
        """Start worker tasks"""
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        print(f"[TaskManager] Started {self.max_concurrent} worker(s)")

    async def stop(self):
        """Stop all workers and cancel pending tasks"""
        # Cancel all pending tasks
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.cancel()

        # Wait for workers to finish
        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        print("[TaskManager] All workers stopped")

    async def submit(self, task: Task) -> str:
        """Submit a new task"""
        self.tasks[task.task_id] = task
        await self._queue.put(task)
        print(f"[TaskManager] Submitted task: {task.task_id}")
        return task.task_id

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        return self.tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False

        task.cancel()
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        print(f"[TaskManager] Cancelled task: {task_id}")
        return True

    async def _worker(self, worker_id: int):
        """Worker coroutine that processes tasks from the queue"""
        print(f"[Worker-{worker_id}] Started")

        while True:
            try:
                # Get task from queue
                task: Task = await self._queue.get()

                # Check if task was cancelled while pending
                if task.status == TaskStatus.CANCELLED:
                    self._queue.task_done()
                    continue

                # Execute task
                await self._execute_task(worker_id, task)

                self._queue.task_done()

            except asyncio.CancelledError:
                print(f"[Worker-{worker_id}] Stopped")
                break
            except Exception as e:
                print(f"[Worker-{worker_id}] Error: {e}")

    async def _execute_task(self, worker_id: int, task: Task):
        """Execute a single task"""
        print(f"[Worker-{worker_id}] Executing task: {task.task_id}")

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            # Run the agent with pre-compiled graph (hot-start)
            final_state = await run_agent_async(
                task_name=task.task_name,
                instruction=task.instruction,
                MODEL_CONFIG=self._model_config,
                max_steps=task.max_steps,
                max_retries=task.max_retries,
                add_info=task.add_info,
                rules_dir=task.rules_dir,
                stop_event=task._stop_event,
                compiled_agent=self._compiled_agent,  # Use cached agent
            )

            # Check if cancelled
            if task._stop_event.is_set():
                task.status = TaskStatus.CANCELLED
            elif final_state.get("execution_status") == "error":
                task.status = TaskStatus.FAILED
                task.error = final_state.get("error_message", "Unknown error")
            else:
                task.status = TaskStatus.COMPLETED
                task.result = {
                    "final_state": {
                        "step_id": final_state.get("step_id"),
                        "stop_flag": final_state.get("stop_flag"),
                        "output_dir": final_state.get("output_dir"),
                    }
                }

        except Exception as e:
            print(f"[Worker-{worker_id}] Task failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)

        finally:
            task.completed_at = time.time()
            print(f"[Worker-{worker_id}] Task {task.task_id} finished: {task.status.value}")


# ============================================================================
# FastAPI Application
# ============================================================================

# Global task manager instance
task_manager = TaskManager(max_concurrent=1)  # GUI operations must be serial


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown (replaces @app.on_event)
    FastAPI 的生命周期管理器，用于在服务器启动和关闭时运行初始化和清理代码
    """
    # Startup
    print("\n" + "=" * 60)
    print("GUI Agent Server Starting...")
    print("=" * 60)

    # Load model config
    task_manager.load_model_config()

    # Compile agent graph (hot-start optimization)
    task_manager.compile_agent()

    # Preload OCR model (hot-start optimization)
    print("[Server] Preloading OCR model...")
    get_ocr_locator()

    # Start workers
    await task_manager.start()

    print("=" * 60)
    print("Server Ready!")
    print("=" * 60 + "\n")

    yield  # Server runs

    # Shutdown
    print("\n[Server] Shutting down...")
    await task_manager.stop()


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
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ========================================================================
    # API Endpoints
    # ========================================================================

    @app.post("/api/v1/tasks", response_model=TaskResponse)
    async def create_task(request: TaskRequest):
        """
        Create a new GUI automation task.

        Returns immediately with task_id. Use GET /api/v1/tasks/{task_id} to check status.
        """
        task_id = str(uuid.uuid4())

        task = Task(
            task_id=task_id,
            task_name=request.task_name,
            instruction=request.instruction,
            max_steps=request.max_steps,
            max_retries=request.max_retries,
            add_info=request.add_info,
            rules_dir=request.rules_dir,
        )

        await task_manager.submit(task)

        return TaskResponse(
            task_id=task_id,
            status=task.status.value,
            created_at=task.created_at,
        )

    @app.get("/api/v1/tasks", response_model=list[TaskResponse])
    async def list_tasks(status: Optional[TaskStatus] = None):
        """List all tasks, optionally filtered by status"""
        tasks = []
        for task in task_manager.tasks.values():
            if status is None or task.status == status:
                tasks.append(TaskResponse(**task.to_dict()))
        return tasks

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str):
        """Get task status and result"""
        task = await task_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskResponse(**task.to_dict())

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        """Cancel a running or pending task"""
        success = await task_manager.cancel_task(task_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Task cannot be cancelled (already completed/failed or not found)"
            )
        return {"success": True, "task_id": task_id}

    @app.get("/api/v1/health")
    async def health_check():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "tasks_total": len(task_manager.tasks),
            "tasks_running": sum(
                1 for t in task_manager.tasks.values()
                if t.status == TaskStatus.RUNNING
            ),
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
    # Skill Learning API Endpoints
    # ========================================================================

    # Import learning modules
    try:
        from learning import ClusterEngine, OperationLogger
        from learning import LLMClusterEngine, LLMReviewer, create_llm_client
        _learning_available = True
        _llm_available = True
    except ImportError as e:
        _learning_available = False
        _llm_available = False
        print(f"[SERVER] Warning: learning module not available: {e}")

    # Global cluster engine instances (module-level for lazy initialization)
    _cluster_engine: Optional[ClusterEngine] = None
    _llm_cluster_engine: Optional[LLMClusterEngine] = None
    _llm_reviewer: Optional[LLMReviewer] = None

    def _get_llm_cluster_engine() -> Optional[LLMClusterEngine]:
        """Get or create LLM cluster engine (lazy initialization)."""
        nonlocal _llm_cluster_engine
        if not _learning_available:
            return None
        if _llm_cluster_engine is None:
            if _llm_available:
                llm_client = create_llm_client()
                _llm_cluster_engine = LLMClusterEngine(llm_client=llm_client)
            else:
                # Fallback without LLM
                _llm_cluster_engine = LLMClusterEngine(llm_client=None)
        return _llm_cluster_engine

    def _get_llm_reviewer() -> Optional[LLMReviewer]:
        """Get or create LLM reviewer (lazy initialization)."""
        nonlocal _llm_reviewer
        if not _learning_available:
            return None
        if _llm_reviewer is None:
            if _llm_available:
                llm_client = create_llm_client()
                _llm_reviewer = LLMReviewer(llm_client=llm_client)
            else:
                _llm_reviewer = None
        return _llm_reviewer

    if _learning_available:
        # Initialize rule-based cluster engine
        _cluster_engine = ClusterEngine()

        @app.get("/api/v1/skills/candidates")
        async def list_candidate_skills():
            """List all candidate skills awaiting approval"""
            candidates = _cluster_engine.get_candidates()
            return {
                "total": len(candidates),
                "candidates": [
                    {
                        "cluster_id": c["cluster_id"],
                        "pattern": c["pattern"],
                        "count": c["count"],
                        "sample_instructions": c.get("sample_instructions", []),
                        "created_at": c.get("created_at"),
                    }
                    for c in candidates
                ],
            }

        @app.get("/api/v1/skills/candidates/{cluster_id}")
        async def get_candidate_skill(cluster_id: str):
            """Get details of a specific candidate skill"""
            cluster = _cluster_engine.get_cluster(cluster_id)
            if not cluster:
                raise HTTPException(status_code=404, detail="Cluster not found")

            return cluster

        class ApproveRequest(BaseModel):
            """Request body for approve endpoint"""
            modifications: Optional[Dict] = None

        @app.post("/api/v1/skills/candidates/{cluster_id}/approve")
        async def approve_candidate_skill(cluster_id: str, request: ApproveRequest = None):
            """
            Approve a candidate skill.

            The skill will be converted to a rule and added to the skill library.
            """
            # Get the cluster
            cluster = _cluster_engine.get_cluster(cluster_id)
            if not cluster:
                raise HTTPException(status_code=404, detail="Cluster not found")

            if cluster.get("status") != "candidate":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cluster is not a candidate (status: {cluster.get('status')})"
                )

            # Approve the cluster
            success = _cluster_engine.approve_cluster(
                cluster_id,
                modifications=request.modifications if request else None
            )

            if not success:
                raise HTTPException(status_code=500, detail="Failed to approve cluster")

            # Generate skill rule (Phase 4 integration)
            try:
                from learning.skill_generator import SkillGenerator
                generator = SkillGenerator()
                skill = generator.generate_skill(cluster)

                # Save to learned skills file
                generator.save_skill(skill)

                return {
                    "success": True,
                    "cluster_id": cluster_id,
                    "skill_id": skill.get("id"),
                    "message": "Skill approved and added to library",
                }
            except Exception as e:
                print(f"[SERVER] Warning: Could not generate skill: {e}")
                return {
                    "success": True,
                    "cluster_id": cluster_id,
                    "message": "Cluster approved but skill generation failed",
                    "error": str(e),
                }

        class RejectRequest(BaseModel):
            """Request body for reject endpoint"""
            reason: str = ""

        @app.post("/api/v1/skills/candidates/{cluster_id}/reject")
        async def reject_candidate_skill(cluster_id: str, request: RejectRequest = None):
            """Reject a candidate skill"""
            success = _cluster_engine.reject_cluster(
                cluster_id,
                reason=request.reason if request else ""
            )

            if not success:
                raise HTTPException(status_code=404, detail="Cluster not found")

            return {"success": True, "cluster_id": cluster_id}

        @app.post("/api/v1/skills/cluster")
        async def trigger_clustering(min_cluster_size: int = 3, full_scan: bool = False):
            """
            Manually trigger the clustering process.

            This scans operation logs and identifies new candidate skills.

            Args:
                min_cluster_size: Minimum operations to form a cluster (default: 3)
                full_scan: If True, scan all logs instead of incremental (default: False)
            """
            try:
                new_clusters = _cluster_engine.scan_and_cluster(min_cluster_size, full_scan)
                return {
                    "success": True,
                    "new_clusters": len(new_clusters),
                    "scan_type": "full" if full_scan else "incremental",
                    "clusters": [
                        {
                            "cluster_id": c["cluster_id"],
                            "pattern": c["pattern"],
                            "count": c["count"],
                        }
                        for c in new_clusters
                    ],
                }
            except Exception as e:
                import traceback
                error_detail = f"Clustering failed: {e}\n{traceback.format_exc()}"
                print(f"[SERVER] {error_detail}")
                raise HTTPException(status_code=500, detail=error_detail)

        @app.get("/api/v1/skills/stats")
        async def get_skill_stats():
            """Get statistics about skill learning"""
            cluster_stats = _cluster_engine.get_stats()

            # Get operation log stats
            logger = OperationLogger()
            log_stats = logger.get_stats()

            # Get skill storage stats (SQLite)
            from learning.skill_generator import SkillGenerator
            generator = SkillGenerator()
            skill_stats = generator.get_stats()

            return {
                "clusters": cluster_stats,
                "operations": log_stats,
                "skills": skill_stats,
            }

        @app.get("/api/v1/skills")
        async def list_skills(cluster_type: str = None):
            """List all approved skills"""
            try:
                from learning.skill_generator import SkillGenerator
                generator = SkillGenerator()  # Default: use_sqlite=True
                skills = generator.list_skills(cluster_type=cluster_type)
                return {
                    "total": len(skills),
                    "skills": skills,
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to load skills: {e}")

        class SkillUpdateRequest(BaseModel):
            """Request body for skill update"""
            enabled: Optional[bool] = None
            name: Optional[str] = None
            description: Optional[str] = None
            trigger_patterns: Optional[List[str]] = None
            app_context: Optional[List[str]] = None
            actions: Optional[List[Dict]] = None

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

            Args:
                skill_id: The skill ID to update
                request: Update request with fields to modify

            Returns:
                Updated skill info
            """
            try:
                from learning.skill_generator import SkillGenerator
                generator = SkillGenerator()  # Default: use_sqlite=True

                # Get existing skill
                skill = generator.get_skill(skill_id)
                if not skill:
                    raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

                # Update fields
                updated_fields = []

                if request.enabled is not None:
                    generator.update_skill_enabled(skill_id, request.enabled)
                    skill["enabled"] = request.enabled
                    updated_fields.append("enabled")

                if request.name is not None:
                    skill["name"] = request.name
                    updated_fields.append("name")

                if request.description is not None:
                    skill["description"] = request.description
                    updated_fields.append("description")

                if request.trigger_patterns is not None:
                    skill["trigger"]["patterns"] = request.trigger_patterns
                    updated_fields.append("trigger_patterns")

                if request.app_context is not None:
                    skill["trigger"]["app_context"] = request.app_context
                    updated_fields.append("app_context")

                if request.actions is not None:
                    skill["actions"] = request.actions
                    updated_fields.append("actions")

                # Save updated skill to SQLite
                if updated_fields:
                    generator.save_skill(skill)

                return {
                    "success": True,
                    "skill_id": skill_id,
                    "updated_fields": updated_fields,
                    "skill": skill
                }

            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to update skill: {e}")

        @app.delete("/api/v1/skills/{skill_id}")
        async def delete_skill(skill_id: str):
            """
            Delete a skill from storage.

            Args:
                skill_id: The skill ID to delete

            Returns:
                Success status
            """
            try:
                from learning.skill_generator import SkillGenerator
                generator = SkillGenerator()
                success = generator.delete_skill(skill_id)

                if not success:
                    raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

                return {"success": True, "skill_id": skill_id}

            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to delete skill: {e}")

        # ====================================================================
        # LLM-Enhanced Sequence Clustering
        # ====================================================================

        @app.post("/api/v1/skills/cluster/sequences/llm")
        async def trigger_llm_sequence_clustering(
            similarity_threshold: float = 0.75,
            min_cluster_size: int = 2,  # Lower default for sequences
            embedding_model: str = None,  # Use local model by default
            use_llm_pattern: bool = True,  # Use LLM for pattern extraction
            llm_model: str = "local_qwen8b",  # LLM model for pattern extraction
        ):
            """
            Trigger LLM-enhanced sequence clustering.

            This combines sequence detection with semantic embeddings:
            1. Groups operations into sequences by instruction
            2. Uses sentence embeddings for semantic similarity
            3. Clusters similar sequences using DBSCAN
            4. Extracts patterns using LLM (optional) or heuristic

            Example: "输入网址访问百度" and "输入网址访问 Google"
            will be clustered together even though text differs.

            Args:
                similarity_threshold: Minimum semantic similarity (0-1, default 0.75)
                min_cluster_size: Minimum sequences to form a cluster (default 2)
                embedding_model: Model name or local path (default: auto-detect local)
                use_llm_pattern: Use LLM for pattern extraction (default True)
                llm_model: LLM model name from model_config.json (default "local_qwen8b")

            Returns:
                Clustering result with new sequence clusters
            """
            if not _llm_available:
                raise HTTPException(
                    status_code=503,
                    detail="LLM components not available."
                )

            try:
                logger = OperationLogger()
                logs = logger.load_logs(limit=1000)

                # Create engine with LLM pattern extraction
                llm_client = create_llm_client(model_name=llm_model) if use_llm_pattern else None
                engine = LLMClusterEngine(
                    llm_client=llm_client,
                    use_llm_pattern=use_llm_pattern
                )
                engine.similarity_threshold = similarity_threshold
                engine.min_cluster_size = min_cluster_size
                engine.embedding_model.clear_cache()  # Clear cache for fresh embeddings

                clusters = engine.cluster_sequences(logs, min_cluster_size=min_cluster_size)

                # Save clusters to file
                _save_llm_clusters(clusters)

                return {
                    "success": True,
                    "new_clusters": len(clusters),
                    "parameters": {
                        "similarity_threshold": similarity_threshold,
                        "min_cluster_size": min_cluster_size,
                        "embedding_model": engine.embedding_model.model_name_or_path,
                        "use_llm_pattern": use_llm_pattern,
                        "llm_model": llm_model if use_llm_pattern else None,
                    },
                    "clusters": [
                        {
                            "cluster_id": c["cluster_id"],
                            "cluster_type": c.get("cluster_type", "sequence_llm"),
                            "pattern": c["pattern"],
                            "count": c["count"],
                            "sample_instructions": c.get("sample_instructions", [])[:3],
                            "sample_sequences": c.get("sample_sequences", [])[:2],
                        }
                        for c in clusters
                    ],
                }
            except Exception as e:
                import traceback
                error_detail = f"LLM sequence clustering failed: {e}\n{traceback.format_exc()}"
                print(f"[SERVER] {error_detail}")
                raise HTTPException(status_code=500, detail=error_detail)

        @app.post("/api/v1/skills/candidates/{cluster_id}/review")
        async def review_candidate_with_llm(cluster_id: str):
            """
            Review a candidate skill using LLM.

            The LLM will evaluate the candidate on:
            - Quality: Pattern clarity and consistency
            - Safety: Risk level assessment
            - Reusability: Generalization quality

            Based on the review, the candidate may be:
            - Auto-approved (high confidence, low risk)
            - Flagged for human review (low confidence or high risk)

            Args:
                cluster_id: The candidate cluster ID

            Returns:
                Review result with decision and detailed scores
            """
            if not _llm_available:
                raise HTTPException(
                    status_code=503,
                    detail="LLM components not available."
                )

            # Get the cluster
            cluster = _cluster_engine.get_cluster(cluster_id)
            if not cluster:
                raise HTTPException(status_code=404, detail="Cluster not found")

            # Review with LLM
            reviewer = _get_llm_reviewer()
            if reviewer is None:
                raise HTTPException(status_code=503, detail="LLM reviewer not initialized")

            review_result = reviewer.review_candidate(cluster)

            # Save review result
            _save_review_result(cluster_id, review_result)

            return {
                "success": True,
                "cluster_id": cluster_id,
                "review": review_result,
            }

        @app.post("/api/v1/skills/candidates/{cluster_id}/auto-approve")
        async def auto_approve_with_llm(cluster_id: str):
            """
            Auto-approve a candidate skill using LLM review.

            If the LLM review recommends auto-approval (high confidence, low risk),
            the skill will be automatically approved and added to the skill library.

            Args:
                cluster_id: The candidate cluster ID

            Returns:
                Result with approval decision and skill info if approved
            """
            if not _llm_available:
                raise HTTPException(
                    status_code=503,
                    detail="LLM components not available."
                )

            # Get the cluster
            cluster = _cluster_engine.get_cluster(cluster_id)
            if not cluster:
                raise HTTPException(status_code=404, detail="Cluster not found")

            if cluster.get("status") != "candidate":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cluster is not a candidate (status: {cluster.get('status')})"
                )

            # Review with LLM
            reviewer = _get_llm_reviewer()
            if reviewer is None:
                raise HTTPException(status_code=503, detail="LLM reviewer not initialized")

            review_result = reviewer.review_candidate(cluster)
            decision = review_result.get("decision", "requires_human_review")

            if decision == "auto_approved":
                # Auto-approve the cluster
                _cluster_engine.approve_cluster(cluster_id)

                # Generate and save skill
                try:
                    from learning.skill_generator import SkillGenerator
                    generator = SkillGenerator()
                    skill = generator.generate_skill(cluster)
                    generator.save_skill(skill)

                    return {
                        "success": True,
                        "decision": "auto_approved",
                        "cluster_id": cluster_id,
                        "skill_id": skill.get("id"),
                        "review": review_result,
                    }
                except Exception as e:
                    return {
                        "success": False,
                        "decision": "approved_but_generation_failed",
                        "cluster_id": cluster_id,
                        "error": str(e),
                        "review": review_result,
                    }
            else:
                # Not approved, save review result for human
                _save_review_result(cluster_id, review_result)

                return {
                    "success": True,
                    "decision": decision,
                    "cluster_id": cluster_id,
                    "reason": review_result.get("recommendation", {}).get("reason", ""),
                    "review": review_result,
                }

        @app.get("/api/v1/skills/review-queue")
        async def get_review_queue():
            """
            Get candidates that require human review.

            Returns candidates that have been reviewed by LLM but
            flagged for human review (low confidence or high risk).
            """
            candidates = _cluster_engine.get_candidates()

            # Load review results
            review_results = _load_review_results()

            # Filter to those requiring human review
            human_review_queue = []
            for c in candidates:
                cluster_id = c.get("cluster_id")
                review = review_results.get(cluster_id, {})

                # Include if:
                # 1. Has been reviewed and flagged for human review
                # 2. Has not been reviewed yet
                if review.get("decision") == "requires_human_review" or not review:
                    human_review_queue.append({
                        "cluster": c,
                        "review": review if review else None,
                    })

            return {
                "total": len(human_review_queue),
                "queue": human_review_queue,
            }

        @app.get("/api/v1/skills/llm-stats")
        async def get_llm_skill_stats():
            """Get statistics about LLM-enhanced skill learning."""
            if not _llm_available:
                return {"available": False}

            reviewer = _get_llm_reviewer()
            review_stats = reviewer.get_review_stats() if reviewer else {}

            return {
                "available": True,
                "review_stats": review_stats,
            }

    return app


# Create app instance
app = create_app()
