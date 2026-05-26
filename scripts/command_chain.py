"""
Sequential Command Chain Executor

Execute multiple GUI agent instructions one by one.
Each command starts only after the previous one completes.

Usage:
    # API mode (requires running server):
    python scripts/command_chain.py --mode api --file scripts/chain_demo.json

    # Direct mode (no server needed):
    python scripts/command_chain.py --mode direct --file scripts/chain_demo.json

    # With custom server URL and timeout per command:
    python scripts/command_chain.py --mode api --url http://localhost:8000 --timeout 300 --file my_chain.json
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List


# ============================================================================
# Data models
# ============================================================================

class ChainConfig:
    """Parsed command chain configuration."""

    def __init__(self, data: dict):
        self.name: str = data.get("name", "Untitled Chain")
        self.description: str = data.get("description", "")
        self.commands: List[Dict] = data.get("commands", [])
        self.on_failure: str = data.get("on_failure", "stop")  # "stop" or "skip"


def load_chain_config(filepath: str) -> ChainConfig:
    """Load and validate chain config from JSON file."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Chain config not found: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    commands = data.get("commands", [])
    if not commands:
        raise ValueError("No commands found in chain config")

    for i, cmd in enumerate(commands):
        if "instruction" not in cmd:
            raise ValueError(f"Command {i} missing 'instruction' field")

    return ChainConfig(data)


# ============================================================================
# API Mode
# ============================================================================

class ApiChainExecutor:
    """Execute command chain via FastAPI server."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    async def run_command(self, instruction: str, timeout: float, **kwargs) -> Dict:
        """Submit a task via API and poll until completion."""
        import aiohttp

        payload = {
            "instruction": instruction,
            "task_name": kwargs.get("task_name", "chain_task"),
            "max_steps": kwargs.get("max_steps", 50),
            "max_retries": kwargs.get("max_retries", 3),
        }

        async with aiohttp.ClientSession() as session:
            # Submit
            async with session.post(f"{self.base_url}/api/v1/tasks", json=payload) as resp:
                result = await resp.json()
                task_id = result.get("task_id")
                if not task_id:
                    return {"error": f"Failed to submit task: {result}"}

            print(f"  [API] Task submitted: {task_id}")

            # Poll until completion
            start = time.time()
            while True:
                await asyncio.sleep(1)

                async with session.get(f"{self.base_url}/api/v1/tasks/{task_id}") as resp:
                    status_data = await resp.json()

                status = status_data.get("status")
                elapsed = time.time() - start

                if status in ("completed", "failed", "cancelled"):
                    return {
                        "task_id": task_id,
                        "status": status,
                        "elapsed": round(elapsed, 1),
                        "result": status_data.get("result"),
                        "error": status_data.get("error"),
                    }

                if elapsed > timeout:
                    # Cancel the timed-out task
                    await session.post(f"{self.base_url}/api/v1/tasks/{task_id}/cancel")
                    return {
                        "task_id": task_id,
                        "status": "timeout",
                        "elapsed": round(elapsed, 1),
                    }

    async def run(self, config: ChainConfig, timeout: float) -> List[Dict]:
        """Execute all commands in sequence."""
        results = []

        for i, cmd in enumerate(config.commands, 1):
            instruction = cmd["instruction"]
            label = cmd.get("label", f"Command {i}")

            print(f"\n[{i}/{len(config.commands)}] {label}")
            print(f"  > {instruction}")

            # 过滤掉非 API 参数的字段
            cmd_kwargs = {k: v for k, v in cmd.items()
                          if k in ("task_name", "max_steps", "max_retries", "add_info", "rules_dir")}
            result = await self.run_command(instruction, timeout, **cmd_kwargs)
            result["label"] = label
            result["index"] = i
            results.append(result)

            print(f"  [Result] status={result['status']} elapsed={result.get('elapsed', '?')}s")

            if result.get("error"):
                print(f"  [Error] {result['error']}")

            # 执行后等待
            wait_after = cmd.get("wait_after", 0)
            if wait_after and i < len(config.commands):
                print(f"  Waiting {wait_after}s before next command...")
                await asyncio.sleep(wait_after)

            if result["status"] in ("failed", "timeout") and config.on_failure == "stop":
                print(f"\nStopping chain (on_failure=stop). {len(config.commands) - i} commands remaining.")
                break

        return results


# ============================================================================
# Direct Mode
# ============================================================================

class DirectChainExecutor:
    """Execute command chain directly without API server."""

    def __init__(self):
        self._service = None

    async def _ensure_service(self):
        """Lazy-initialize the application service."""
        if self._service is None:
            from app.services import AgentApplicationService

            self._service = AgentApplicationService(max_concurrent=1, show_entry_card=False)
            await self._service.initialize()

    async def run_command(self, instruction: str, timeout: float, **kwargs) -> Dict:
        """Run a single command via the service layer."""
        await self._ensure_service()

        result = await asyncio.wait_for(
            self._service.run_once(
                instruction=instruction,
                task_name=kwargs.get("task_name", "chain_task"),
                max_steps=kwargs.get("max_steps", 50),
                max_retries=kwargs.get("max_retries", 3),
                timeout=timeout,
            ),
            timeout=timeout + 10,  # extra buffer for setup/teardown
        )

        status = result.get("status", "unknown")
        return {
            "status": status,
            "result": result.get("result"),
            "error": result.get("error"),
        }

    async def run(self, config: ChainConfig, timeout: float) -> List[Dict]:
        """Execute all commands in sequence."""
        results = []

        try:
            for i, cmd in enumerate(config.commands, 1):
                instruction = cmd["instruction"]
                label = cmd.get("label", f"Command {i}")

                print(f"\n[{i}/{len(config.commands)}] {label}")
                print(f"  > {instruction}")

                # 过滤掉非服务层参数的字段
                cmd_kwargs = {k: v for k, v in cmd.items()
                              if k in ("task_name", "max_steps", "max_retries", "add_info", "rules_dir")}
                result = await self.run_command(instruction, timeout, **cmd_kwargs)
                result["label"] = label
                result["index"] = i
                results.append(result)

                print(f"  [Result] status={result['status']}")

                if result.get("error"):
                    print(f"  [Error] {result['error']}")

                # 执行后等待
                wait_after = cmd.get("wait_after", 0)
                if wait_after and i < len(config.commands):
                    print(f"  Waiting {wait_after}s before next command...")
                    await asyncio.sleep(wait_after)

                if result["status"] in ("failed", "timeout") and config.on_failure == "stop":
                    print(f"\nStopping chain (on_failure=stop). {len(config.commands) - i} commands remaining.")
                    break
        finally:
            if self._service:
                await self._service.shutdown()

        return results


# ============================================================================
# Report
# ============================================================================

def print_summary(results: List[Dict]) -> None:
    """Print execution summary."""
    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)

    passed = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] in ("failed", "timeout"))

    for r in results:
        icon = {"completed": "OK", "failed": "FAIL", "timeout": "TIMEOUT", "cancelled": "CANCEL"}.get(r["status"], "?")
        print(f"  [{icon}] #{r['index']} {r['label']}  ({r.get('elapsed', '?')}s)")

    print(f"\n  Passed: {passed}  Failed: {failed}  Total: {len(results)}")
    print("=" * 60)


# ============================================================================
# Main
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sequential Command Chain Executor")
    parser.add_argument(
        "--file", "-f",
        required=True,
        help="Path to JSON chain config file",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["api", "direct"],
        default="api",
        help="Execution mode: 'api' calls FastAPI server, 'direct' bypasses it (default: api)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Server URL for API mode (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float,
        default=600.0,
        help="Timeout per command in seconds (default: 600)",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    # Load config
    try:
        config = load_chain_config(args.file)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] Config load failed: {exc}")
        return 1

    print(f"Chain: {config.name}")
    if config.description:
        print(f"Description: {config.description}")
    print(f"Commands: {len(config.commands)}  |  Mode: {args.mode}  |  Timeout: {args.timeout}s  |  On failure: {config.on_failure}")

    # Execute
    if args.mode == "api":
        executor = ApiChainExecutor(base_url=args.url)
    else:
        executor = DirectChainExecutor()

    results = await executor.run(config, args.timeout)

    # Summary
    print_summary(results)

    failed = sum(1 for r in results if r["status"] in ("failed", "timeout"))
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()