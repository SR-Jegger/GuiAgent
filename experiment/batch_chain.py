"""批量指令执行器

输入格式: [{"201": [71.3568, 29.3665]}, {"206": [75.0871, 24.6166]}]
动态生成指令并遍历执行多个平台的流程

支持三阶段执行:
- pre_commands: 前置指令（执行一次，无参数替换）
- commands: 批量指令（遍历参数列表，参数替换）
- post_commands: 后置指令（执行一次，无参数替换）

Usage:
    python experiment/batch_chain.py --params experiment/batch_params.json
    python experiment/batch_chain.py -p experiment/batch_params.json -t experiment/batch_template.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional

# 复用 command_chain.py 的执行器
from command_chain import ChainExecutor, ChainConfig


# 默认指令模板（内置）- 支持前置/批量/后置三阶段
DEFAULT_TEMPLATE = {
    "name": "虚假目标流程模板",
    "pre_commands": [
        {
            "label": "发送区域目标",
            "instruction": "发送区域目标，覆盖区域A",
            "wait_after": 5
        }
    ],
    "commands": [
        {
            "label": "虚假目标",
            "instruction": "向{platform_id}平台发送虚假目标，经度{longitude},纬度{latitude}",
            "wait_after": 3
        },
        {
            "label": "瞄准目标",
            "instruction": "点击{platform_id}平台，进行瞄准目标确认",
            "wait_after": 3
        },
        {
            "label": "跟踪目标",
            "instruction": "点击{platform_id}平台，进行跟踪目标确认",
            "wait_after": 5
        }
    ],
    "post_commands": [],
    "pre_on_failure": "stop",      # 前置失败则停止
    "batch_on_failure": "continue",  # 批量失败继续下一个平台
}


def load_params(filepath: str) -> List[Dict]:
    """加载参数列表

    格式: [{"201": [71.3568, 29.3665]}, {"206": [75.0871, 24.6166]}]
    每个元素是键值对：平台编号 -> [经度, 纬度]
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"参数文件不存在: {filepath}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("参数文件必须是列表格式")

    return data


def load_template(filepath: Optional[str] = None) -> Dict:
    """加载指令模板"""
    if filepath:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"模板文件不存在: {filepath}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    return DEFAULT_TEMPLATE


class BatchExecutor:
    """批量执行器：支持前置、批量、后置三阶段执行"""

    def __init__(self, template: Dict, max_steps: int = 50, max_retries: int = 3):
        self.template = template
        self.max_steps = max_steps
        self.max_retries = max_retries

    def generate_commands(self, platform_id: str, longitude: float, latitude: float) -> List[Dict]:
        """根据参数生成指令列表（参数替换）"""
        commands = []
        for cmd in self.template.get("commands", []):
            instruction = cmd["instruction"]
            instruction = instruction.replace("{platform_id}", str(platform_id))
            instruction = instruction.replace("{longitude}", str(longitude))
            instruction = instruction.replace("{latitude}", str(latitude))

            commands.append({
                "label": cmd.get("label", ""),
                "instruction": instruction,
                "wait_after": cmd.get("wait_after", 3)
            })
        return commands

    async def run_pre_commands(
        self,
        executor: ChainExecutor,
        timeout: float,
    ) -> List[Dict]:
        """执行前置指令（执行一次，无参数替换）"""
        pre_commands = self.template.get("pre_commands", [])
        if not pre_commands:
            return []

        print(f"\n{'='*60}")
        print("【前置指令】执行一次")
        print(f"{'='*60}")

        config = ChainConfig({
            "name": "前置指令",
            "commands": pre_commands,
            "on_failure": self.template.get("pre_on_failure", "stop"),
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
        })

        # keep_alive=True，保持服务继续运行
        return await executor.run(config, timeout, keep_alive=True)

    async def run_post_commands(
        self,
        executor: ChainExecutor,
        timeout: float,
    ) -> List[Dict]:
        """执行后置指令（执行一次，无参数替换）"""
        post_commands = self.template.get("post_commands", [])
        if not post_commands:
            return []

        print(f"\n{'='*60}")
        print("【后置指令】执行一次")
        print(f"{'='*60}")

        config = ChainConfig({
            "name": "后置指令",
            "commands": post_commands,
            "on_failure": self.template.get("post_on_failure", "continue"),
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
        })

        # keep_alive=False，执行完后关闭服务
        return await executor.run(config, timeout, keep_alive=False)

    async def run_platform(
        self,
        executor: ChainExecutor,
        platform_id: str,
        longitude: float,
        latitude: float,
        timeout: float,
        on_failure: str,
        keep_alive: bool = False,
    ) -> Dict:
        """执行单个平台的完整流程"""
        commands = self.generate_commands(platform_id, longitude, latitude)

        print(f"\n=== 平台 {platform_id} ===")
        print(f"坐标: 经度={longitude}, 纬度={latitude}")
        print(f"指令数: {len(commands)}")

        config = ChainConfig({
            "name": f"平台{platform_id}流程",
            "commands": commands,
            "on_failure": on_failure,
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
        })

        results = await executor.run(config, timeout, keep_alive=keep_alive)

        success = sum(1 for r in results if r["status"] == "completed")
        return {
            "platform_id": platform_id,
            "longitude": longitude,
            "latitude": latitude,
            "results": results,
            "success_count": success,
            "total_count": len(results),
            "status": "completed" if success == len(results) else "partial"
        }

    async def run_batch(
        self,
        params_list: List[Dict],
        timeout: float,
    ) -> Dict:
        """执行完整流程：前置 → 批量 → 后置"""

        all_results = {
            "pre": [],
            "batch": [],
            "post": [],
            "stopped": False,
            "stop_reason": None,
        }

        # 共用一个 ChainExecutor（避免重复初始化服务）
        executor = ChainExecutor()
        total_platforms = len(params_list)
        batch_on_failure = self.template.get("batch_on_failure", "continue")

        # ========== Phase 1: 前置指令（执行一次）==========
        pre_results = await self.run_pre_commands(executor, timeout)
        all_results["pre"] = pre_results

        # 检查前置是否全部成功
        pre_failed = any(r["status"] != "completed" for r in pre_results)
        if pre_failed:
            print("\n【前置指令失败】停止执行")
            all_results["stopped"] = True
            all_results["stop_reason"] = "pre_failed"
            await executor._service.shutdown() if executor._service else None
            return all_results

        # ========== Phase 2: 批量执行（遍历参数列表）==========
        print(f"\n{'='*60}")
        print(f"【批量执行】共 {total_platforms} 个平台")
        print(f"{'='*60}")

        for i, params in enumerate(params_list, 1):
            # 解析参数：{"201": [71.3568, 29.3665]}
            for platform_id, coords in params.items():
                longitude, latitude = coords[0], coords[1]

                print(f"\n执行平台 {platform_id} ({i}/{total_platforms})")

                # 只有最后一个平台执行完后才可能关闭服务（取决于是否有后置指令）
                has_post_commands = bool(self.template.get("post_commands", []))
                is_last_platform = (i == total_platforms)
                keep_alive = not is_last_platform or has_post_commands

                platform_result = await self.run_platform(
                    executor=executor,
                    platform_id=platform_id,
                    longitude=longitude,
                    latitude=latitude,
                    timeout=timeout,
                    on_failure=batch_on_failure,
                    keep_alive=keep_alive,
                )
                all_results["batch"].append(platform_result)

                # 失败处理
                if platform_result["status"] != "completed":
                    if batch_on_failure == "stop":
                        print(f"\n【批量失败】停止执行，剩余 {total_platforms - i} 个平台")
                        all_results["stopped"] = True
                        all_results["stop_reason"] = "batch_failed"
                        # 如果没有后置指令，立即关闭服务
                        if not has_post_commands and executor._service:
                            await executor._service.shutdown()
                        break
                    else:
                        print(f"\n继续执行下一个平台...")

        # ========== Phase 3: 后置指令（执行一次）==========
        if not all_results["stopped"]:
            post_results = await self.run_post_commands(executor, timeout)
            all_results["post"] = post_results

        return all_results


def print_summary(results: Dict):
    """打印执行汇总（三阶段）"""
    print("\n" + "=" * 60)
    print("【执行汇总】")
    print("=" * 60)

    # Phase 1: 前置指令
    pre_results = results.get("pre", [])
    if pre_results:
        print("\n[前置指令]")
        for r in pre_results:
            icon = "OK" if r["status"] == "completed" else "FAIL"
            print(f"  [{icon}] #{r['index']} {r['label']} ({r.get('elapsed', '?')}s)")
        pre_success = sum(1 for r in pre_results if r["status"] == "completed")
        print(f"  成功: {pre_success}/{len(pre_results)}")

    # Phase 2: 批量执行
    batch_results = results.get("batch", [])
    if batch_results:
        print("\n[批量执行]")
        total_commands = 0
        total_success = 0

        for platform in batch_results:
            icon = "OK" if platform["status"] == "completed" else "PARTIAL"
            cmd_success = platform["success_count"]
            cmd_total = platform["total_count"]
            total_commands += cmd_total
            total_success += cmd_success

            print(f"  [{icon}] 平台 {platform['platform_id']}: {cmd_success}/{cmd_total} 指令成功")

        print(f"  总计: {total_success}/{total_commands} 指令成功")
        print(f"  平台: {len(batch_results)} 个")

    # Phase 3: 后置指令
    post_results = results.get("post", [])
    if post_results:
        print("\n[后置指令]")
        for r in post_results:
            icon = "OK" if r["status"] == "completed" else "FAIL"
            print(f"  [{icon}] #{r['index']} {r['label']} ({r.get('elapsed', '?')}s)")
        post_success = sum(1 for r in post_results if r["status"] == "completed")
        print(f"  成功: {post_success}/{len(post_results)}")

    # 整体状态
    print("\n" + "-" * 60)
    if results.get("stopped"):
        print(f"  状态: 停止执行 ({results.get('stop_reason')})")
    else:
        all_success = (
            all(r["status"] == "completed" for r in pre_results) and
            all(p["status"] == "completed" for p in batch_results) and
            all(r["status"] == "completed" for r in post_results)
        )
        print(f"  状态: {'全部成功' if all_success else '部分失败'}")
    print("=" * 60)


async def main_async(args) -> int:
    try:
        params_list = load_params(args.params)
        template = load_template(args.template)
    except Exception as e:
        print(f"[错误] 加载失败: {e}")
        return 1

    print(f"\n批量任务: {template.get('name', 'Untitled')}")
    print(f"平台数: {len(params_list)}")
    print(f"每平台指令数: {len(template.get('commands', []))}")
    print(f"前置指令数: {len(template.get('pre_commands', []))}")
    print(f"后置指令数: {len(template.get('post_commands', []))}")

    executor = BatchExecutor(
        template=template,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
    )
    results = await executor.run_batch(
        params_list=params_list,
        timeout=args.timeout,
    )

    print_summary(results)

    # 返回状态码
    if results.get("stopped"):
        return 1

    all_success = (
        all(r["status"] == "completed" for r in results.get("pre", [])) and
        all(p["status"] == "completed" for p in results.get("batch", [])) and
        all(r["status"] == "completed" for r in results.get("post", []))
    )
    return 0 if all_success else 1


def main():
    parser = argparse.ArgumentParser(description="批量指令执行器（支持前置/批量/后置三阶段）")
    parser.add_argument("--params", "-p", required=True, help="参数列表JSON文件")
    parser.add_argument("--template", "-t", help="指令模板JSON文件（可选，使用内置默认模板）")
    parser.add_argument("--timeout", type=float, default=600, help="单条指令超时时间（秒）")
    parser.add_argument("--max-steps", type=int, default=50, help="最大执行步数")
    parser.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    args = parser.parse_args()

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()