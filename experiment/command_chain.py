"""
Sequential Command Chain Executor

Execute multiple GUI agent instructions one by one.
Each command starts only after the previous one completes.

Usage:
    python experiment/command_chain.py --file experiment/chain_demo.json

Flow (same as card input):
    instruction → semantic_matcher → matched_id → submit_task → agent
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


class ChainConfig:
    """Command chain configuration."""

    def __init__(self, data: dict):
        self.name: str = data.get("name", "Untitled")
        self.description: str = data.get("description", "")
        self.on_failure: str = data.get("on_failure", "stop")
        self.max_steps: int = data.get("max_steps", 50)
        self.max_retries: int = data.get("max_retries", 3)
        self.commands: List[Dict] = data.get("commands", [])


def load_chain_config(filepath: str) -> ChainConfig:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Not found: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data.get("commands"):
        raise ValueError("No commands in config")

    for i, cmd in enumerate(data["commands"]):
        if "instruction" not in cmd:
            raise ValueError(f"Command {i} missing 'instruction'")

    return ChainConfig(data)


class ChainExecutor:
    """Execute command chain, same flow as card input."""

    def __init__(self):
        self._service = None
        self._semantic_matcher = None

    async def _ensure_service(self):
        """Initialize service and semantic matcher (lazy)."""
        if self._service is None:
            from app.services import AgentApplicationService
            from app.semantic.semantic_matcher import HybridMatcher

            # 加载模型配置
            model_config = json.load(open("nodes/model_config.json"))
            model_cfg = model_config.get("models", {}).get("gemma4_e4b", {})

            # 初始化 semantic matcher（和卡片一样）
            self._semantic_matcher = HybridMatcher(model_config=model_cfg)

            # 初始化服务（不显示卡片）
            self._service = AgentApplicationService(
                max_concurrent=1,
                show_entry_card=False,
                use_semantic_match=False,  # 我们自己处理 semantic match
            )
            await self._service.initialize()

    async def run_command(
        self,
        instruction: str,
        max_steps: int,
        max_retries: int,
        timeout: float,
    ) -> Dict:
        """Run single command, same flow as card input."""
        await self._ensure_service()

        semantic_matched_id = None
        semantic_parameters = None

        # 语义匹配（和卡片输入一样）
        if self._semantic_matcher:
            match_result = await self._semantic_matcher.match(instruction)
            if match_result.is_matched:
                semantic_matched_id = match_result.matched_id
                semantic_parameters = match_result.parameters
                instruction = match_result.instruction
                print(f"  [语义匹配] ID={semantic_matched_id}, 参数={semantic_parameters}")

        # 提交任务（传递 matched_id 给 agent）
        task_id = await self._service.submit_task(
            instruction=instruction,
            max_steps=max_steps,
            max_retries=max_retries,
            semantic_matched_id=semantic_matched_id,
            semantic_parameters=semantic_parameters,
        )

        # 等待完成
        start = time.time()
        while True:
            task = await self._service.get_task(task_id)
            if not task:
                return {"error": "Task not found"}

            if task.status.value in ("completed", "failed", "cancelled"):
                elapsed = round(time.time() - start, 1)
                return {
                    "status": task.status.value,
                    "elapsed": elapsed,
                    "result": task.result,
                    "error": task.error,
                }

            if time.time() - start > timeout:
                await self._service.cancel_task(task_id)
                return {"status": "timeout", "elapsed": round(timeout, 1)}

            await asyncio.sleep(1)

    async def run(self, config: ChainConfig, timeout: float, keep_alive: bool = False) -> List[Dict]:
        """Execute all commands.

        Args:
            config: Chain configuration
            timeout: Timeout per command
            keep_alive: If True, don't shutdown service after execution (for batch use)
        """
        results = []

        try:
            for i, cmd in enumerate(config.commands, 1):
                instruction = cmd["instruction"]
                label = cmd.get("label", f"Step {i}")

                print(f"\n[{i}/{len(config.commands)}] {label}")
                print(f"  > {instruction}")

                result = await self.run_command(
                    instruction=instruction,
                    max_steps=config.max_steps,
                    max_retries=config.max_retries,
                    timeout=timeout,
                )
                result["label"] = label
                result["index"] = i
                results.append(result)

                print(f"  [结果] {result['status']} ({result.get('elapsed', '?')}s)")
                if result.get("error"):
                    print(f"  [错误] {result['error']}")

                # 执行后等待
                wait_after = cmd.get("wait_after", 0)
                if wait_after and i < len(config.commands):
                    print(f"  等待 {wait_after} 秒...")
                    await asyncio.sleep(wait_after)

                # 失败处理
                if result["status"] in ("failed", "timeout"):
                    if config.on_failure == "stop":
                        print(f"\n停止执行，剩余 {len(config.commands) - i} 条指令")
                        break
        finally:
            # 只有 keep_alive=False 时才关闭服务
            if not keep_alive and self._service:
                await self._service.shutdown()

        return results


def print_summary(results: List[Dict]):
    print("\n" + "=" * 50)
    print("执行汇总")
    print("=" * 50)

    for r in results:
        icon = "OK" if r["status"] == "completed" else "FAIL"
        print(f"  [{icon}] #{r['index']} {r['label']}")

    passed = sum(1 for r in results if r["status"] == "completed")
    print(f"\n  成功: {passed}  失败: {len(results) - passed}")
    print("=" * 50)


async def main_async(args) -> int:
    try:
        config = load_chain_config(args.file)
    except Exception as e:
        print(f"[错误] 加载配置失败: {e}")
        return 1

    print(f"任务链: {config.name}")
    print(f"指令数: {len(config.commands)} | 失败策略: {config.on_failure}")

    executor = ChainExecutor()
    results = await executor.run(config, args.timeout)

    print_summary(results)

    return 0 if all(r["status"] == "completed" for r in results) else 1


def main():
    parser = argparse.ArgumentParser(description="Command Chain Executor")
    parser.add_argument("--file", "-f", required=True, help="JSON config file")
    parser.add_argument("--timeout", "-t", type=float, default=600, help="Timeout per command")
    args = parser.parse_args()

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()