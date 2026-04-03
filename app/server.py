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
from typing import Optional, Dict, Any
from enum import Enum

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent_graph import build_agent_graph_simple, run_agent_async, AgentState
from utils.utils import get_output_dir


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

    # ========================================================================
    # Skill Learning API Endpoints
    # ========================================================================

    # Import learning modules
    try:
        from learning import ClusterEngine, OperationLogger
        _learning_available = True
    except ImportError:
        _learning_available = False
        print("[SERVER] Warning: learning module not available")

    if _learning_available:
        # Global cluster engine instance
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

            return {
                "clusters": cluster_stats,
                "operations": log_stats,
            }

        @app.get("/api/v1/skills")
        async def list_skills():
            """List all approved skills"""
            try:
                import json
                skills_file = "rules/learned_skills.json"
                if os.path.exists(skills_file):
                    with open(skills_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return {
                        "total": len(data.get("rules", [])),
                        "skills": data.get("rules", []),
                    }
                return {"total": 0, "skills": []}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to load skills: {e}")

    return app


# Create app instance
app = create_app()
