"""
Application Service Layer for GUI Agent Task Management

This module provides high-level business logic for task management,
decoupled from FastAPI routing concerns.

Services:
- AgentApplicationService: Task lifecycle management
"""

import asyncio
import os
import uuid
import time
import json
from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path

from agent_graph import run_agent_async, build_agent_graph_simple


class TaskStatus(str, Enum):
    """Task lifecycle status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Represents a single task with its lifecycle"""
    task_id: str
    instruction: str
    task_name: str = "default_task"
    max_steps: int = 50
    max_retries: int = 3
    add_info: Optional[str] = None
    rules_dir: str = "./rules"

    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    result: Optional[Dict] = None
    error: Optional[str] = None

    # Internal
    _stop_event: Optional[asyncio.Event] = field(default=None, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def cancel(self):
        """Request task cancellation"""
        if self._stop_event:
            self._stop_event.set()

    def to_dict(self) -> dict:
        """Convert to dictionary for API response"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def create(cls, instruction: str, **kwargs) -> "Task":
        """Factory method to create a new task with UUID"""
        return cls(
            task_id=str(uuid.uuid4()),
            instruction=instruction,
            _stop_event=asyncio.Event(),
            **kwargs
        )


class AgentApplicationService:
    """
    Application service for GUI agent task management.

    Provides high-level business logic for:
    - Task submission and lifecycle management
    - Concurrent execution control
    - Task cancellation
    - Task status tracking

    Usage:
        service = AgentApplicationService()
        await service.initialize()
        task_id = await service.submit_task("Click the login button")
        status = await service.get_task_status(task_id)
    """

    def __init__(self, max_concurrent: int = 1):
        self.max_concurrent = max_concurrent
        self._tasks: Dict[str, Task] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: List[asyncio.Task] = []
        self._running_count = 0
        self._lock = asyncio.Lock()

        # Cached resources (hot-start)
        self._model_config: Optional[Dict] = None
        self._compiled_agent = None

    async def initialize(self, config_path: str = "nodes/model_config.json"):
        """
        Initialize service: load config, compile agent graph.
        Call this once at application startup.
        """
        # Load model configuration
        await self._load_model_config(config_path)

        # Compile agent graph for hot-start
        self._compile_agent()

        # Start worker tasks
        await self._start_workers()

    async def _load_model_config(self, config_path: str):
        """Load model configuration from file"""
        loop = asyncio.get_event_loop()
        self._model_config = await loop.run_in_executor(
            None, self._load_config_sync, config_path
        )
        print(f"[AgentApplicationService] Loaded model config from {config_path}")

    def _load_config_sync(self, config_path: str) -> Dict:
        """Synchronous config loading"""
        with open(config_path, 'r') as f:
            return json.load(f)

    def _compile_agent(self):
        """Compile agent graph once for hot-start"""
        builder = build_agent_graph_simple()
        self._compiled_agent = builder.compile()
        print("[AgentApplicationService] Agent graph compiled")

    async def _start_workers(self):
        """Start worker tasks to process queue"""
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        print(f"[AgentApplicationService] Started {self.max_concurrent} worker(s)")

    async def submit_task(
        self,
        instruction: str,
        task_name: str = "default_task",
        max_steps: int = 50,
        max_retries: int = 3,
        add_info: Optional[str] = None,
        rules_dir: str = "./rules",
    ) -> str:
        """
        Submit a new task for async execution.

        Args:
            instruction: Task instruction text
            task_name: Optional task name
            max_steps: Maximum steps to execute
            max_retries: Maximum retries per step
            add_info: Additional context info
            rules_dir: Path to rules directory

        Returns:
            task_id: Unique task identifier
        """
        task = Task.create(
            instruction=instruction,
            task_name=task_name,
            max_steps=max_steps,
            max_retries=max_retries,
            add_info=add_info,
            rules_dir=rules_dir,
        )

        async with self._lock:
            self._tasks[task.task_id] = task

        await self._queue.put(task)
        print(f"[AgentApplicationService] Submitted task: {task.task_id}")
        return task.task_id

    async def get_task_status(self, task_id: str) -> Optional[Dict]:
        """
        Get current status of a task.

        Args:
            task_id: Task identifier

        Returns:
            Task status dict or None if not found
        """
        async with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return None

        return task.to_dict()

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task object by ID (internal use)"""
        async with self._lock:
            return self._tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running or pending task.

        Args:
            task_id: Task identifier

        Returns:
            True if cancelled, False if task not found or already terminal
        """
        async with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return False

        # Cannot cancel terminal tasks
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False

        task.cancel()
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()

        print(f"[AgentApplicationService] Cancelled task: {task.task_id}")
        return True

    async def list_tasks(self, status_filter: Optional[TaskStatus] = None) -> List[Dict]:
        """
        List all tasks, optionally filtered by status.

        Args:
            status_filter: Optional status to filter by

        Returns:
            List of task status dicts
        """
        async with self._lock:
            tasks = list(self._tasks.values())

        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]

        return [t.to_dict() for t in tasks]

    async def run_once(
        self,
        instruction: str,
        task_name: str = "default_task",
        max_steps: int = 50,
        max_retries: int = 3,
        add_info: Optional[str] = None,
        rules_dir: str = "./rules",
        timeout: Optional[float] = None,
    ) -> Dict:
        """
        Run a single task synchronously (blocking call).

        For CLI usage or simple scripts. For API usage, use submit_task + get_task_status.

        Args:
            instruction: Task instruction text
            task_name: Optional task name
            max_steps: Maximum steps to execute
            max_retries: Maximum retries per step
            add_info: Additional context info
            rules_dir: Path to rules directory
            timeout: Optional timeout in seconds

        Returns:
            Final task state dict
        """
        # Create a temporary task
        task_id = await self.submit_task(
            instruction=instruction,
            task_name=task_name,
            max_steps=max_steps,
            max_retries=max_retries,
            add_info=add_info,
            rules_dir=rules_dir,
        )

        # Wait for completion
        start_time = time.time()
        while True:
            task = await self.get_task(task_id)
            if not task:
                return {"error": "Task not found"}

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return task.to_dict()

            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                await self.cancel_task(task_id)
                return {"error": "Timeout", "timeout": timeout}

            # Poll interval
            await asyncio.sleep(0.5)

    async def _worker(self, worker_id: int):
        """Worker coroutine that processes tasks from queue"""
        print(f"[Worker-{worker_id}] Started")

        while True:
            try:
                task: Task = await self._queue.get()

                # Check if cancelled while pending
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
            # Run agent with pre-compiled graph
            final_state = await run_agent_async(
                task_name=task.task_name,
                instruction=task.instruction,
                MODEL_CONFIG=self._model_config,
                max_steps=task.max_steps,
                max_retries=task.max_retries,
                add_info=task.add_info,
                rules_dir=task.rules_dir,
                stop_event=task._stop_event,
                compiled_agent=self._compiled_agent,
            )

            # Determine final status
            if task._stop_event.is_set():
                task.status = TaskStatus.CANCELLED
            elif final_state.get("execution_status") == "error":
                task.status = TaskStatus.FAILED
                task.error = final_state.get("error_message", "Unknown error")
            else:
                task.status = TaskStatus.COMPLETED
                task.result = {
                    "final_state": {
                        "step_id": final_state.get("step_id", 0),
                        "stop_flag": final_state.get("stop_flag", False),
                        "output_dir": final_state.get("output_dir", "N/A"),
                    }
                }

        except Exception as e:
            print(f"[Worker-{worker_id}] Task failed: {e}")
            task.status = TaskStatus.FAILED
            task.error = str(e)

        finally:
            task.completed_at = time.time()
            print(f"[Worker-{worker_id}] Task {task.task_id} finished: {task.status.value}")

    async def shutdown(self):
        """Shutdown service: cancel pending tasks and stop workers"""
        # Cancel pending tasks
        async with self._lock:
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
                    task.cancel()

        # Stop workers
        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        print("[AgentApplicationService] All workers stopped")


# Convenience function for CLI usage
async def run_task_once(
    instruction: str,
    **kwargs
) -> Dict:
    """
    Run a single task and wait for result.

    Convenience wrapper for simple usage.

    Example:
        result = await run_task_once("Click the login button", max_steps=20)
        print(result["status"])  # "completed" | "failed" | "cancelled"
    """
    service = AgentApplicationService()
    await service.initialize()
    try:
        return await service.run_once(instruction, **kwargs)
    finally:
        await service.shutdown()
