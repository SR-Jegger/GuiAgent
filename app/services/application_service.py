"""
Application Service Layer for GUI Agent Task Management.

This module provides high-level business logic for task management,
decoupled from FastAPI routing concerns.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent_graph import build_agent_graph_simple, run_agent_async
from utils.browser_manager import BrowserManager
from app.popup.task_progress import (
    PROGRESS_STATUS_CANCELLED,
    PROGRESS_STATUS_CANCELLING,
    PROGRESS_STATUS_COMPLETED,
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_PENDING,
    PROGRESS_STATUS_RUNNING,
    TaskProgressSnapshot,
    build_progress_snapshot,
)
from app.popup.task_progress_popup import TaskProgressPopup
from app.popup.main_entry_card import MainEntryCard, CardState
from app.semantic.semantic_matcher import SemanticMatcher, RuleBasedMatcher, HybridMatcher, MatchResult


class TaskStatus(str, Enum):
    """Task lifecycle status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Represents a single task with its lifecycle."""

    task_id: str
    instruction: str
    task_name: str = "default_task"
    max_steps: int = 50
    max_retries: int = 3
    add_info: Optional[str] = None
    rules_dir: str = "./rules"
    use_intent_mapping: bool = False  # 是否启用意图映射
    intent_mapping_config_path: Optional[str] = None  # 映射配置路径

    # 语义匹配结果（从 semantic_matcher 传递）
    semantic_matched_id: Optional[str] = None
    semantic_parameters: Optional[Dict] = None

    # 用户提供的图片（base64 data URI 或 HTTP URL）
    input_images: Optional[list[str]] = None

    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    result: Optional[Dict] = None
    error: Optional[str] = None

    _stop_event: Optional[asyncio.Event] = field(default=None, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    progress: Optional[TaskProgressSnapshot] = field(default=None, repr=False)
    _progress_popup: Optional[TaskProgressPopup] = field(default=None, repr=False)

    def cancel(self) -> None:
        """Request task cancellation."""
        if self._stop_event:
            self._stop_event.set()

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "progress": self.progress.__dict__ if self.progress else None,
        }

    @classmethod
    def create(cls, instruction: str, **kwargs) -> "Task":
        """Factory method to create a new task with UUID."""
        return cls(
            task_id=str(uuid.uuid4()),
            instruction=instruction,
            _stop_event=asyncio.Event(),
            **kwargs,
        )


class AgentApplicationService:
    """Application service for GUI agent task management."""

    def __init__(self, max_concurrent: int = 1, show_entry_card: bool = True, use_semantic_match: bool = True):
        self.max_concurrent = max_concurrent
        self.show_entry_card = show_entry_card  # 是否显示主入口卡片
        self.use_semantic_match = use_semantic_match  # 是否启用语义匹配
        self._tasks: Dict[str, Task] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: List[asyncio.Task] = []
        self._lock = asyncio.Lock()

        self._model_config: Optional[Dict] = None
        self._compiled_agent = None

        # 主入口卡片
        self._main_entry_card: Optional[MainEntryCard] = None
        self._current_task_id: Optional[str] = None  # 当前任务ID（单任务模式）

        # 事件循环引用（用于跨线程调用）
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 语义匹配器（混合匹配：规则 + LLM）
        self._semantic_matcher: Optional[SemanticMatcher | RuleBasedMatcher | HybridMatcher] = None
        # 匹配模式: "hybrid" (默认), "llm", "rule"
        self._matcher_mode: str = "rule" #"hybrid"

        # 当前语义匹配结果（用于传递给 agent）
        self._current_semantic_result: Optional[MatchResult] = None

        # 持久化浏览器管理器
        self._browser_manager: Optional[BrowserManager] = None

    async def initialize(self, config_path: str = "nodes/model_config.json") -> None:
        """Initialize service: load config, compile agent graph, start workers."""
        # 保存事件循环引用
        self._loop = asyncio.get_running_loop()

        await self._load_model_config(config_path)
        self._compile_agent()
        await self._start_workers()

        # 初始化语义匹配器
        if self.use_semantic_match:
            if self._matcher_mode == "hybrid":
                # 使用混合匹配（规则 + LLM 结合）【推荐】
                model_name = "gemma4_e4b"
                model_cfg = self._model_config.get("models", {}).get(model_name, {})
                self._semantic_matcher = HybridMatcher(model_config=model_cfg)
                print(f"[AgentApplicationService] Semantic matcher initialized (Hybrid: Rule + LLM)")
            elif self._matcher_mode == "llm":
                # 仅使用 LLM 匹配
                model_name = "gemma4_e4b"
                model_cfg = self._model_config.get("models", {}).get(model_name, {})
                self._semantic_matcher = SemanticMatcher(model_config=model_cfg)
                print(f"[AgentApplicationService] Semantic matcher initialized (LLM: {model_name})")
            else:
                # 仅使用规则匹配（快速、无LLM调用）
                self._semantic_matcher = RuleBasedMatcher()
                print("[AgentApplicationService] Semantic matcher initialized (Rule-based)")

        # 启动持久化浏览器（带 remote debugging port）
        try:
            self._browser_manager = BrowserManager(port=9222)
            self._browser_manager.start(headless=False)
            print(f"[AgentApplicationService] Browser started with CDP at {self._browser_manager.cdp_endpoint}")
        except Exception as exc:
            print(f"[AgentApplicationService] Browser manager start failed ({exc}), tasks can still launch their own browser")

        # 启动主入口卡片
        if self.show_entry_card:
            self._start_main_entry_card()

    async def _load_model_config(self, config_path: str) -> None:
        """Load model configuration from file."""
        loop = asyncio.get_event_loop()
        self._model_config = await loop.run_in_executor(None, self._load_config_sync, config_path)
        print(f"[AgentApplicationService] Loaded model config from {config_path}")

    def _load_config_sync(self, config_path: str) -> Dict:
        """Synchronous config loading."""
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _compile_agent(self) -> None:
        """Compile agent graph once for hot-start."""
        builder = build_agent_graph_simple()
        self._compiled_agent = builder.compile()
        print("[AgentApplicationService] Agent graph compiled")

    async def _start_workers(self) -> None:
        """Start worker tasks to process queue."""
        for i in range(self.max_concurrent):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        print(f"[AgentApplicationService] Started {self.max_concurrent} worker(s)")

    def _start_main_entry_card(self) -> None:
        """启动主入口卡片"""
        try:
            # 从配置获取 ASR 服务地址
            asr_config = self._model_config.get("asr_server", {})
            asr_server_url = asr_config.get("server_url", "ws://192.168.137.2:8585/asr/stream")

            self._main_entry_card = MainEntryCard(
                on_submit=self._on_card_submit,
                on_cancel=self._on_card_cancel,
                asr_server_url=asr_server_url,
            )
            self._main_entry_card.start()
            print("[AgentApplicationService] Main entry card started")
        except Exception as exc:
            print(f"[AgentApplicationService] Failed to start main entry card: {exc}")

    def _on_card_submit(self, instruction: str) -> None:
        """卡片提交任务回调（从 Qt 线程调用）"""
        if self._loop is None:
            print("[AgentApplicationService] Event loop not ready")
            return
        # 跨线程调度异步任务
        asyncio.run_coroutine_threadsafe(
            self._submit_from_card(instruction),
            self._loop
        )

    async def _submit_from_card(self, instruction: str) -> str:
        """从卡片提交任务（支持嵌入 data:image URI 的图片输入）"""
        if self._current_task_id:
            # 单任务模式：检查是否有正在执行的任务
            task = await self.get_task(self._current_task_id)
            if task and task.status == TaskStatus.RUNNING:
                print("[AgentApplicationService] 已有任务在执行，忽略新提交")
                return ""

        # 从文本中提取嵌入的 data:image URI
        from utils.utils import parse_input_images_from_text
        instruction, input_images = parse_input_images_from_text(instruction)
        if input_images:
            print(f"[AgentApplicationService] 从卡片输入中提取到 {len(input_images)} 张图片")

        # 语义匹配处理
        if self._semantic_matcher:
            # HybridMatcher 和 SemanticMatcher 是异步的，RuleBasedMatcher 是同步的
            if isinstance(self._semantic_matcher, RuleBasedMatcher):
                match_result = self._semantic_matcher.match(instruction)
            else:
                # HybridMatcher 或 SemanticMatcher（异步）
                match_result = await self._semantic_matcher.match(instruction)
            if match_result.is_matched:
                print(
                    f"[语义匹配] 原文本: '{instruction}' → "
                    f"匹配ID: {match_result.matched_id}, "
                    f"置信度: {match_result.confidence:.2f}, "
                    f"参数: {match_result.parameters}, "
                    f"指令: '{match_result.instruction}'"
                )
                instruction = match_result.instruction
                # 保存语义匹配结果，用于传递给 agent
                self._current_semantic_result = match_result
            else:
                print(f"[语义匹配] 无匹配，使用原文本: '{instruction}'")
                self._current_semantic_result = None

        task_id = await self.submit_task(instruction=instruction, input_images=input_images if input_images else None)
        self._current_task_id = task_id
        return task_id

    def _on_card_cancel(self) -> None:
        """卡片取消任务回调（从 Qt 线程调用）"""
        if self._loop is None or not self._current_task_id:
            return
        # 跨线程调度取消任务
        asyncio.run_coroutine_threadsafe(
            self.cancel_task(self._current_task_id),
            self._loop
        )

    async def submit_task(
        self,
        instruction: str,
        task_name: str = "default_task",
        max_steps: int = 50,
        max_retries: int = 3,
        add_info: Optional[str] = None,
        rules_dir: str = "./rules",
        use_intent_mapping: bool = False,
        intent_mapping_config_path: Optional[str] = None,
        semantic_matched_id: Optional[str] = None,
        semantic_parameters: Optional[Dict] = None,
        input_images: Optional[list[str]] = None,
    ) -> str:
        """Submit a new task for async execution."""
        task = Task.create(
            instruction=instruction,
            task_name=task_name,
            max_steps=max_steps,
            max_retries=max_retries,
            add_info=add_info,
            rules_dir=rules_dir,
            use_intent_mapping=use_intent_mapping,
            intent_mapping_config_path=intent_mapping_config_path,
            semantic_matched_id=semantic_matched_id,
            semantic_parameters=semantic_parameters,
            input_images=input_images,
        )
        task.progress = build_progress_snapshot(
            task_id=task.task_id,
            instruction=task.instruction,
            status=PROGRESS_STATUS_PENDING,
        )

        async with self._lock:
            self._tasks[task.task_id] = task

        await self._queue.put(task)
        print(f"[AgentApplicationService] Submitted task: {task.task_id}")
        return task.task_id

    async def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Get current status of a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task object by ID."""
        async with self._lock:
            return self._tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or pending task."""
        async with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return False

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False

        task.cancel()
        self._update_task_progress(
            task,
            status=PROGRESS_STATUS_CANCELLING,
            status_message="正在取消任务...",
        )
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        self._update_task_progress(
            task,
            status=PROGRESS_STATUS_CANCELLED,
            status_message="任务已取消",
        )

        print(f"[AgentApplicationService] Cancelled task: {task.task_id}")
        return True

    async def list_tasks(self, status_filter: Optional[TaskStatus] = None) -> List[Dict]:
        """List all tasks, optionally filtered by status."""
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
        use_intent_mapping: bool = False,
        intent_mapping_config_path: Optional[str] = None,
        semantic_matched_id: Optional[str] = None,
        semantic_parameters: Optional[Dict] = None,
        input_images: Optional[list[str]] = None,
    ) -> Dict:
        """Run a single task synchronously."""
        task_id = await self.submit_task(
            instruction=instruction,
            task_name=task_name,
            max_steps=max_steps,
            max_retries=max_retries,
            add_info=add_info,
            rules_dir=rules_dir,
            use_intent_mapping=use_intent_mapping,
            intent_mapping_config_path=intent_mapping_config_path,
            semantic_matched_id=semantic_matched_id,
            semantic_parameters=semantic_parameters,
            input_images=input_images,
        )

        start_time = time.time()
        while True:
            task = await self.get_task(task_id)
            if not task:
                return {"error": "Task not found"}

            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return task.to_dict()

            if timeout and (time.time() - start_time) > timeout:
                await self.cancel_task(task_id)
                return {"error": "Timeout", "timeout": timeout}

            await asyncio.sleep(0.5)

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine that processes tasks from queue."""
        print(f"[Worker-{worker_id}] Started")

        while True:
            try:
                task: Task = await self._queue.get()

                if task.status == TaskStatus.CANCELLED:
                    self._queue.task_done()
                    continue

                await self._execute_task(worker_id, task)
                self._queue.task_done()

            except asyncio.CancelledError:
                print(f"[Worker-{worker_id}] Stopped")
                break
            except Exception as exc:
                print(f"[Worker-{worker_id}] Error: {exc}")

    async def _execute_task(self, worker_id: int, task: Task) -> None:
        """Execute a single task."""
        print(f"[Worker-{worker_id}] Executing task: {task.task_id}")

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._update_task_progress(
            task,
            status=PROGRESS_STATUS_RUNNING,
            status_message="任务开始执行",
        )
        # 不再创建独立弹窗，使用主入口卡片

        try:
            # 获取语义匹配结果：优先从 task 读取，其次从全局状态（兼容卡片输入）
            semantic_matched_id = task.semantic_matched_id
            semantic_parameters = task.semantic_parameters
            if not semantic_matched_id and self._current_semantic_result:
                semantic_matched_id = self._current_semantic_result.matched_id
                semantic_parameters = self._current_semantic_result.parameters

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
                use_intent_mapping=task.use_intent_mapping,
                intent_mapping_config_path=task.intent_mapping_config_path,
                semantic_matched_id=semantic_matched_id,  # 语义匹配 ID
                semantic_parameters=semantic_parameters,  # 语义匹配参数
                input_images=task.input_images,  # 用户提供的图片
                cdp_endpoint=self._browser_manager.cdp_endpoint if self._browser_manager else None,
                progress_callback=lambda state, status=None, message="": self._update_task_progress(
                    task,
                    agent_state=state,
                    status=status,
                    status_message=message,
                ),
            )

            if task._stop_event.is_set():
                task.status = TaskStatus.CANCELLED
                self._update_task_progress(
                    task,
                    agent_state=final_state,
                    status=PROGRESS_STATUS_CANCELLED,
                    status_message="任务已取消",
                )
            elif final_state.get("execution_status") == "error":
                task.status = TaskStatus.FAILED
                task.error = final_state.get("error_message", "Unknown error")
                self._update_task_progress(
                    task,
                    agent_state=final_state,
                    status=PROGRESS_STATUS_FAILED,
                    status_message=f"失败: {task.error}",
                )
            else:
                task.status = TaskStatus.COMPLETED
                task.result = {
                    "final_state": {
                        "step_id": final_state.get("step_id", 0),
                        "stop_flag": final_state.get("stop_flag", False),
                        "output_dir": final_state.get("output_dir", "N/A"),
                    }
                }
                self._update_task_progress(
                    task,
                    agent_state=final_state,
                    status=PROGRESS_STATUS_COMPLETED,
                    status_message="任务执行完成",
                )

        except Exception as exc:
            print(f"[Worker-{worker_id}] Task failed: {exc}")
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            self._update_task_progress(
                task,
                status=PROGRESS_STATUS_FAILED,
                status_message=f"失败: {task.error}",
            )

        finally:
            task.completed_at = time.time()
            print(f"[Worker-{worker_id}] Task {task.task_id} finished: {task.status.value}")

    # 临时禁用弹窗的开关
    POPUP_ENABLED = True  # 改为 True 可重新启用

    def _ensure_progress_popup(self, task: Task) -> None:
        """Create the popup lazily when task execution starts."""
        if not self.POPUP_ENABLED:
            return  # 弹窗已禁用，跳过

        if task._progress_popup is not None or task.progress is None:
            return

        try:
            popup = TaskProgressPopup(snapshot=task.progress)
            popup.start()
            task._progress_popup = popup
        except Exception as exc:
            print(f"[AgentApplicationService] Failed to create progress popup: {exc}")

    def _update_task_progress(
        self,
        task: Task,
        agent_state: Optional[dict] = None,
        status: Optional[str] = None,
        status_message: str = "",
    ) -> None:
        """Build the latest UI snapshot and push it to the popup or main entry card."""
        progress_status = status or self._map_task_status(task.status)
        task.progress = build_progress_snapshot(
            task_id=task.task_id,
            instruction=task.instruction,
            status=progress_status,
            state=agent_state,
            status_message=status_message,
        )

        # 更新主入口卡片
        if self._main_entry_card is not None:
            self._main_entry_card.update_progress(task.progress)
            # 更新卡片状态
            if progress_status == PROGRESS_STATUS_COMPLETED:
                self._main_entry_card.set_state(CardState.COMPLETED)
            elif progress_status == PROGRESS_STATUS_FAILED:
                self._main_entry_card.set_state(CardState.FAILED)
            elif progress_status == PROGRESS_STATUS_CANCELLED:
                self._main_entry_card.set_state(CardState.CANCELLED)

        # 也更新独立弹窗（如果存在）
        if task._progress_popup is not None:
            task._progress_popup.update(task.progress)

    @staticmethod
    def _map_task_status(task_status: TaskStatus) -> str:
        mapping = {
            TaskStatus.PENDING: PROGRESS_STATUS_PENDING,
            TaskStatus.RUNNING: PROGRESS_STATUS_RUNNING,
            TaskStatus.COMPLETED: PROGRESS_STATUS_COMPLETED,
            TaskStatus.FAILED: PROGRESS_STATUS_FAILED,
            TaskStatus.CANCELLED: PROGRESS_STATUS_CANCELLED,
        }
        return mapping[task_status]

    async def shutdown(self) -> None:
        """Shutdown service: cancel pending tasks and stop workers."""
        # 关闭持久化浏览器
        if self._browser_manager is not None:
            self._browser_manager.stop()
            print("[AgentApplicationService] Browser stopped")

        # 关闭主入口卡片
        if self._main_entry_card is not None:
            self._main_entry_card.close()
            print("[AgentApplicationService] Main entry card closed")

        async with self._lock:
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
                    task.cancel()

        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        print("[AgentApplicationService] All workers stopped")


async def run_task_once(instruction: str, **kwargs) -> Dict:
    """Convenience wrapper for simple usage."""
    service = AgentApplicationService()
    await service.initialize()
    try:
        return await service.run_once(instruction, **kwargs)
    finally:
        await service.shutdown()
