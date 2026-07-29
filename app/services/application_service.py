"""
Application Service Layer for GUI Agent Task Management.

This module provides high-level business logic for task management,
decoupled from FastAPI routing concerns.
"""

import asyncio
import json
import re
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
from app.popup.main_entry_card import MainEntryCard, CardState
from app.semantic.semantic_matcher import SemanticMatcher, RuleBasedMatcher, HybridMatcher, MatchResult
from app.semantic.semantic_matcher import normalize_chinese_numerals, normalize_strike_mode

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

    # @path md 展开后的逐行语义匹配结果（与 semantic_matched_id 互斥）
    # 每项: {"matched_id": str|None, "is_matched": bool, "parameters": dict,
    #        "instruction": str, "original_text": str}
    semantic_matches: Optional[List[Dict]] = None

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

        # 杀伤链实时缓存（轮询专属 BrowserTools，与 per-task 隔离）
        self._cache_browser = None
        self._kill_chain_cache = None

        # LLM 自主决策杀伤链推进
        self._auto_execute = "off"  # "off" | "confirm" | "auto"
        self._decision_task = None
        self._pending_confirmation = None  # {"target_id": str, "timestamp": float}
        self._advance_cooldowns = {}  # {target_id: last_advance_ts}
        self._cooldown_seconds = 30  # 同一条链 30s 内不重复推进

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

        # 启动杀伤链缓存轮询（非致命：失败不影响主流程）
        try:
            from utils.kill_chain_cache import get_kill_chain_cache
            from utils.browser_tools import BrowserTools
            self._cache_browser = BrowserTools(cdp_endpoint=self._browser_manager.cdp_endpoint)
            await self._cache_browser.start()
            self._kill_chain_cache = get_kill_chain_cache()
            await self._kill_chain_cache.start_polling(self._cache_browser, 1.0)
            print("[AgentApplicationService] Kill chain cache polling started")
        except Exception as exc:
            self._cache_browser = None
            self._kill_chain_cache = None
            print(f"[AgentApplicationService] Kill chain cache startup failed ({exc}), dispatcher fallback to inline")

        # 启动 LLM 自主决策循环（非致命）
        try:
            self._decision_task = asyncio.create_task(self._decision_loop())
            print(f"[AgentApplicationService] Decision loop started (auto_execute={self._auto_execute})")
        except Exception as exc:
            self._decision_task = None
            print(f"[AgentApplicationService] Decision loop start failed ({exc})")

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

    def _on_card_submit(self, instruction: str, from_file: bool = False) -> None:
        """卡片提交任务回调（从 Qt 线程调用）

        Args:
            instruction: 任务指令文本（@path 引用已展开）
            from_file: True 表示指令来自 @path 文件展开，应跳过语义匹配，
                       直接交给 task_decomposer 按行解析多行文本
        """
        if self._loop is None:
            print("[AgentApplicationService] Event loop not ready")
            return
        # 跨线程调度异步任务
        asyncio.run_coroutine_threadsafe(
            self._submit_from_card(instruction, from_file=from_file),
            self._loop
        )

    async def _match_single(self, instruction: str) -> Optional["MatchResult"]:
        """对单条指令跑语义匹配，返回 MatchResult 或 None（匹配器未配置）。"""
        if not self._semantic_matcher:
            return None
        if isinstance(self._semantic_matcher, RuleBasedMatcher):
            return self._semantic_matcher.match(instruction)
        return await self._semantic_matcher.match(instruction)

    def _try_cache_dispatch(self, instruction: str) -> Optional["MatchResult"]:
        """缓存驱动兜底：优先用数字标识匹配 target_id；退化到 target_id 子串匹配。

        方案一：ASR 同音不同字时，target_id 的汉字部分（如"剑发"）可能被识别成
        "建发"等错字，但数字部分（如"8686"）通常准确。先按数字标识找：

        1. find_chain_by_number 抽数字标识 -> 唯一命中即返回
        2. 多条命中时 find_chain_by_number 内部用 target_id 子串消歧
        3. 数字未命中，走原 target_id 子串匹配兜底（要求汉字部分也对得上）

        仅匹配 target_id（不匹 platform_id/grid_id，避免"确认206"误触发）。
        dash-insensitive：用户说"丰田6686"能命中缓存里的"丰田-6686"。

        UI 纠正展示：命中后 corrected_intent 用 correct_target_id_homophone 重写，
        让卡片展示"打击剑发8686"而不是"打击建发8686"。
        """
        if self._kill_chain_cache is None:
            return None
        chains = self._kill_chain_cache.chains
        if not chains:
            return None
        from utils.kill_chain_cache import find_chain_by_number, correct_target_id_homophone, KillChainCache

        chain = find_chain_by_number(instruction)
        if chain is not None:
            corrected = correct_target_id_homophone(instruction)
            print(
                f"[AgentApplicationService] 数字标识命中 target_id={chain.target_id!r}，"
                f"展示文本纠正: {instruction!r} -> {corrected!r}"
            )
            return MatchResult(
                matched_id="confirm_kill_chain_by_target",
                confidence=0.95,
                corrected_intent=corrected,
                instruction=instruction,
                original_text=instruction,
                is_matched=True,
                parameters={"target_id": chain.target_id},
            )

        # 退化：target_id 子串匹配（原逻辑，要求汉字部分也对得上）
        # 数字未命中时几乎不会命中（target_id 必含数字），保留作防御兜底
        instruction_norm = KillChainCache._normalize(instruction)
        for chain in chains:
            if not chain.target_id:
                continue
            target_norm = KillChainCache._normalize(chain.target_id)
            if target_norm and target_norm in instruction_norm:
                return MatchResult(
                    matched_id="confirm_kill_chain_by_target",
                    confidence=0.95,
                    corrected_intent=instruction,
                    instruction=instruction,
                    original_text=instruction,
                    is_matched=True,
                    parameters={"target_id": chain.target_id},
                )
        return None

    # ===== LLM 自主决策杀伤链推进 =====

    async def _decision_loop(self) -> None:
        """顺序循环：评估 -> 执行（等完成）-> 冷却 3s -> 下一轮。cancel 即退出。"""
        import asyncio
        while True:
            try:
                if (self._auto_execute != "off"
                        and self._pending_confirmation is None
                        and not self._has_running_task()
                        and self._kill_chain_cache is not None
                        and self._kill_chain_cache.chains):
                    decision = await self._llm_evaluate()
                    action = decision.get("action")
                    if action == "advance":
                        target_id = decision.get("target_id", "")
                        if target_id and not self._in_cooldown(target_id):
                            if self._auto_execute == "auto":
                                await self._execute_auto_advance(target_id)
                            else:  # confirm
                                await self._request_confirmation(target_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[DecisionLoop] error: {e}")
            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise

    async def _llm_evaluate(self) -> dict:
        """调 LLM 看 cache + 优先级，返回 {action, target_id} 或 {action: none}。"""
        import json
        from openai import AsyncOpenAI
        from utils.kill_chain_cache import load_priority_config, get_priority

        cache = self._kill_chain_cache
        if not cache or not cache.chains:
            return {"action": "none"}

        # 格式化 cache + 优先级为 prompt
        cfg = load_priority_config()
        lines = []
        for c in cache.chains:
            pri = get_priority(c.target_id, cfg)
            plats = ",".join(f"{p.platform_id}:{p.stage or '?'}" for p in c.platforms)
            lines.append(f"[优先级{pri}] {c.target_id} [{c.grid_id}] - 平台:{plats}")
        state_text = "\n".join(lines)

        prompt = (
            "你是杀伤链调度助手。根据当前页面杀伤链状态和优先级，决定是否推进某条链。\n\n"
            f"当前杀伤链：\n{state_text}\n\n"
            "阶段顺序：fix -> track -> target -> engage -> assess（assess 是最终阶段）\n\n"
            "规则：\n"
            "1. 选优先级最高且未到 assess 的链推进\n"
            "2. 同优先级选阶段最靠后的\n"
            "3. 全部到 assess 或无链时返回 none\n\n"
            '返回 JSON：{"action": "advance", "target_id": "目标标识"} 或 {"action": "none"}'
        )

        model_cfg = self._model_config.get("models", {}).get("gemma4_e4b", {})
        base_url = model_cfg.get("base_url", "")
        api_key = model_cfg.get("api_key", "")
        model = model_cfg.get("model", "")
        if not base_url or not model:
            print("[DecisionLoop] LLM 配置缺失，跳过评估")
            return {"action": "none"}

        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=30.0)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=30.0,
            )
            content = response.choices[0].message.content or ""
            # 提取 JSON
            m = re.search(r'\{[^}]+\}', content)
            if m:
                return json.loads(m.group(0))
            return {"action": "none"}
        except Exception as e:
            print(f"[DecisionLoop] LLM 调用失败: {e}")
            return {"action": "none"}

    async def _execute_auto_advance(self, target_id: str) -> str:
        """构造 MatchResult，走正常 dispatcher 链路执行推进。"""
        import time
        self._current_semantic_result = MatchResult(
            matched_id="confirm_kill_chain_by_target",
            confidence=0.99,
            corrected_intent=f"[自动] 推进 {target_id}",
            instruction=f"[自动] 推进 {target_id}",
            original_text=f"[自动] 推进 {target_id}",
            is_matched=True,
            parameters={"target_id": target_id},
        )
        self._advance_cooldowns[target_id] = time.time()
        print(f"[DecisionLoop] 自动推进 {target_id}（已设冷却 {self._cooldown_seconds}s）")
        task_id = await self.submit_task(
            instruction=f"[自动] 推进 {target_id}",
            use_intent_mapping=True,
        )
        return task_id

    async def _request_confirmation(self, target_id: str) -> None:
        """模式 A：弹出确认 UI，等用户响应。"""
        import time
        from utils.kill_chain_cache import get_kill_chain_cache
        self._pending_confirmation = {"target_id": target_id, "timestamp": time.time()}

        cache = get_kill_chain_cache()
        chain = cache.resolve_first(target_id) if cache else None
        platform = chain.pick_platform_by_priority() if chain else None
        msg = f"建议推进 {target_id}"
        if platform:
            msg += f"（平台 {platform.platform_id} 处于 {platform.stage} 阶段）"
        msg += "，确认吗？"

        print(f"[DecisionLoop] 请求确认: {msg}")
        if self._main_entry_card is not None:
            self._main_entry_card.show_confirmation(
                msg,
                on_confirm=self._on_confirm_advance,
                on_cancel=self._on_cancel_advance,
                on_alternate=self._on_alternate_advance,
            )

    def _on_confirm_advance(self) -> None:
        """用户点确认按钮（从 Qt 线程回调）。"""
        if self._pending_confirmation:
            target_id = self._pending_confirmation["target_id"]
            self._pending_confirmation = None
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._execute_auto_advance(target_id), self._loop)

    def _on_cancel_advance(self) -> None:
        """用户点取消按钮。"""
        self._pending_confirmation = None
        print("[DecisionLoop] 用户取消推进")

    def _on_alternate_advance(self) -> None:
        """用户点换一个：清除 pending，下一轮决策循环选下一条。"""
        self._pending_confirmation = None
        print("[DecisionLoop] 用户要求换一个，下一轮重新选择")

    def _has_running_task(self) -> bool:
        """检查是否有 task 正在运行（PENDING/RUNNING）。"""
        from app.services.application_service import TaskStatus
        for task in self._tasks.values():
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return True
        return False

    def _in_cooldown(self, target_id: str) -> bool:
        """检查 target_id 是否在冷却期内。"""
        import time
        last = self._advance_cooldowns.get(target_id)
        if last is None:
            return False
        if time.time() - last < self._cooldown_seconds:
            return True
        # 过期清理
        del self._advance_cooldowns[target_id]
        return False

    def _check_pending_confirmation_timeout(self) -> None:
        """清理超时的 pending confirmation（30s 未响应）。"""
        import time
        if self._pending_confirmation:
            if time.time() - self._pending_confirmation["timestamp"] > 30:
                print("[DecisionLoop] pending confirmation 超时 30s，自动清除")
                self._pending_confirmation = None

    async def _submit_from_card(self, instruction: str, from_file: bool = False) -> str:
        """从卡片提交任务（支持嵌入 data:image URI 的图片输入）

        Args:
            from_file: True 表示指令来自 @path 文件展开。此时按行拆分，
                       每行单独跑语义匹配器（与直接输入等价能力），收集成
                       semantic_matches 传给 task_decomposer 逐行展开。
        """
        # 汉字数字归一化：ASR 常把"8686"转写成"八六八六"，统一转回阿拉伯数字，
        # 让下游 \d 正则、target_id 子串匹配、kill_chain 缓存命中都能正常工作。
        # 覆盖语音/手打/@path/预设四条卡片入口；CLI 路径在 submit_task 再做一次。
        # 打击方式归一化：ASR 念法（金地自杀/KVD/远火）统一转回页面字面（金地打击/KVD打击/远火打击）。
        instruction = normalize_chinese_numerals(instruction)
        instruction = normalize_strike_mode(instruction)

        # ===== auto_execute 模式切换指令拦截 =====
        if instruction in ("开启自动模式", "开启自动决策", "开启自动"):
            self._auto_execute = "auto"
            print(f"[AgentApplicationService] auto_execute -> auto")
            if self._main_entry_card is not None:
                self._main_entry_card.update_progress(
                    build_progress_snapshot(
                        task_id=self._current_task_id or "",
                        instruction=instruction,
                        status=PROGRESS_STATUS_COMPLETED,
                        status_message="已开启自动决策模式",
                    )
                )
            return ""
        if instruction in ("开启确认模式", "开启确认决策", "开启确认"):
            self._auto_execute = "confirm"
            print(f"[AgentApplicationService] auto_execute -> confirm")
            if self._main_entry_card is not None:
                self._main_entry_card.update_progress(
                    build_progress_snapshot(
                        task_id=self._current_task_id or "",
                        instruction=instruction,
                        status=PROGRESS_STATUS_COMPLETED,
                        status_message="已开启确认决策模式",
                    )
                )
            return ""
        if instruction in ("关闭自动决策", "关闭自动模式", "关闭自动"):
            self._auto_execute = "off"
            print(f"[AgentApplicationService] auto_execute -> off")
            if self._main_entry_card is not None:
                self._main_entry_card.update_progress(
                    build_progress_snapshot(
                        task_id=self._current_task_id or "",
                        instruction=instruction,
                        status=PROGRESS_STATUS_COMPLETED,
                        status_message="已关闭自动决策",
                    )
                )
            return ""

        # ===== pending confirmation 拦截 =====
        if self._pending_confirmation:
            target_id = self._pending_confirmation["target_id"]
            if instruction in ("确认", "是", "对", "确认推进", "确定"):
                self._pending_confirmation = None
                print(f"[AgentApplicationService] 用户确认推进 {target_id}")
                return await self._execute_auto_advance(target_id)
            if instruction in ("取消", "否", "不", "取消推进", "不要"):
                self._pending_confirmation = None
                print("[AgentApplicationService] 用户取消推进")
                if self._main_entry_card is not None:
                    self._main_entry_card.update_progress(
                        build_progress_snapshot(
                            task_id=self._current_task_id or "",
                            instruction=instruction,
                            status=PROGRESS_STATUS_COMPLETED,
                            status_message="已取消推进",
                        )
                    )
                return ""
            if instruction in ("换一个", "换", "下一个", "换条"):
                self._pending_confirmation = None
                print("[AgentApplicationService] 用户要求换一个，下一轮决策重新选择")
                if self._main_entry_card is not None:
                    self._main_entry_card.update_progress(
                        build_progress_snapshot(
                            task_id=self._current_task_id or "",
                            instruction=instruction,
                            status=PROGRESS_STATUS_COMPLETED,
                            status_message="已换一个，重新评估中",
                        )
                    )
                return ""
            # 其他输入：清除 pending，按正常指令处理
            print(f"[AgentApplicationService] 输入 {instruction!r} 非确认/取消/换一个，清除 pending 并按正常指令处理")
            self._pending_confirmation = None

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

        semantic_matches: Optional[List[Dict]] = None

        if from_file:
            # @path 展开：逐行匹配，保持与直接输入等价的能力。
            # 过滤 markdown 标题行（# 开头）和分隔线（--- / ===），避免把
            # 文档结构行当成指令送进匹配器或 sub_steps。
            def _is_metadata_line(s: str) -> bool:
                if s.startswith("#"):
                    return True
                if set(s) <= {"-", "=", " "} and len(s) >= 3:
                    return True
                return False

            lines = [ln.strip() for ln in instruction.split("\n") if ln.strip()]
            lines = [ln for ln in lines if not _is_metadata_line(ln)]
            print(f"[语义匹配] @path 文件展开，逐行匹配 {len(lines)} 行（已过滤标题/分隔线）")
            semantic_matches = []
            for idx, line in enumerate(lines, 1):
                match_result = await self._match_single(line)
                if match_result is not None and match_result.is_matched:
                    print(f"  [行 {idx}] '{line}' → matched_id={match_result.matched_id}, "
                          f"params={match_result.parameters}")
                    semantic_matches.append({
                        "matched_id": match_result.matched_id,
                        "is_matched": True,
                        "parameters": match_result.parameters,
                        "instruction": match_result.instruction,
                        "original_text": line,
                    })
                else:
                    print(f"  [行 {idx}] '{line}' → 无匹配，作为文本步")
                    semantic_matches.append({
                        "matched_id": None,
                        "is_matched": False,
                        "parameters": {},
                        "instruction": line,
                        "original_text": line,
                    })
            # @path 模式不设置单值 semantic_matched_id
            self._current_semantic_result = None
        else:
            # 直接输入：整段匹配（原逻辑）
            match_result = await self._match_single(instruction)
            if match_result is not None and match_result.is_matched:
                print(
                    f"[语义匹配] 原文本: '{instruction}' → "
                    f"匹配ID: {match_result.matched_id}, "
                    f"置信度: {match_result.confidence:.2f}, "
                    f"参数: {match_result.parameters}, "
                    f"指令: '{match_result.instruction}'"
                )
                # 保留用户原文（normalize 后）作为 task.instruction，不替换成 matcher 的模板描述。
                # 原因：下游 _resolve_strike_mode 需要从原文识别打击方式关键词（KVD/金地打击/远火），
                # 模板描述（如"确认杀伤链目标 (target_id=剑发8686)"）丢失这些关键词，会走 platform_id 默认。
                self._current_semantic_result = match_result
            else:
                print(f"[语义匹配] 无匹配，使用原文本: '{instruction}'")
                self._current_semantic_result = None
                # 缓存驱动兜底：指令含真实 target_id 时路由到杀伤链 dispatcher
                cache_hit = self._try_cache_dispatch(instruction)
                if cache_hit is not None:
                    print(f"[语义匹配] 缓存命中 target_id={cache_hit.parameters.get('target_id')!r} -> confirm_kill_chain_by_target")
                    self._current_semantic_result = cache_hit

        task_id = await self.submit_task(
            instruction=instruction,
            input_images=input_images if input_images else None,
            semantic_matches=semantic_matches,
        )
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
        semantic_matches: Optional[List[Dict]] = None,
    ) -> str:
        """Submit a new task for async execution."""
        # CLI 入口归一化（卡片入口已在 _submit_from_card 归一化，此处幂等无副作用；
        # 自动推进路径的 target_id 来自缓存本身是 ASCII，归一化为 no-op）。
        instruction = normalize_chinese_numerals(instruction)
        instruction = normalize_strike_mode(instruction)

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
            semantic_matches=semantic_matches,
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
        semantic_matches: Optional[List[Dict]] = None,
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
            semantic_matches=semantic_matches,
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
                semantic_matches=task.semantic_matches,  # @path 逐行匹配结果
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
        # 停止 LLM 决策循环
        if self._decision_task is not None:
            try:
                self._decision_task.cancel()
                await asyncio.gather(self._decision_task, return_exceptions=True)
                self._decision_task = None
                print("[AgentApplicationService] Decision loop stopped")
            except Exception as exc:
                print(f"[AgentApplicationService] Decision loop stop failed: {exc}")

        # 停止杀伤链缓存轮询
        if self._kill_chain_cache is not None:
            try:
                await self._kill_chain_cache.stop_polling()
                print("[AgentApplicationService] Kill chain cache polling stopped")
            except Exception as exc:
                print(f"[AgentApplicationService] Kill chain cache stop failed: {exc}")
        if self._cache_browser is not None:
            try:
                await self._cache_browser.close()
                print("[AgentApplicationService] Cache browser closed")
            except Exception as exc:
                print(f"[AgentApplicationService] Cache browser close failed: {exc}")

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
