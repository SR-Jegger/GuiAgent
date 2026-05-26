"""批量指令执行器

输入格式: [{"201": [71.3568, 29.3665]}, {"206": [75.0871, 24.6166]}]
动态生成指令并遍历执行多个平台的流程

复用 command_chain.py 的 ChainExecutor 执行指令

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


# 默认指令模板（内置）
DEFAULT_TEMPLATE = {
    "name": "虚假目标流程模板",
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
    ]
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
    """批量执行器：负责参数展开，执行交给 ChainExecutor"""

    def __init__(self, template: Dict, max_steps: int = 50, max_retries: int = 3):
        self.template = template
        self.max_steps = max_steps
        self.max_retries = max_retries

    def generate_commands(self, platform_id: str, longitude: float, latitude: float) -> List[Dict]:
        """根据参数生成指令列表"""
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
        """执行单个平台的完整流程（复用 ChainExecutor）"""
        commands = self.generate_commands(platform_id, longitude, latitude)

        print(f"\n=== 平台 {platform_id} ===")
        print(f"坐标: 经度={longitude}, 纬度={latitude}")
        print(f"指令数: {len(commands)}")

        # 构造 ChainConfig，交给 ChainExecutor 执行
        config = ChainConfig({
            "name": f"平台{platform_id}流程",
            "commands": commands,
            "on_failure": on_failure,
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
        })

        # 传入 keep_alive 参数控制是否关闭服务
        results = await executor.run(config, timeout, keep_alive=keep_alive)

        # 统计
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
        on_failure: str = "continue",
    ) -> List[Dict]:
        """遍历执行多个平台"""
        all_results = []
        total_platforms = len(params_list)

        # 共用一个 ChainExecutor（避免重复初始化服务）
        executor = ChainExecutor()

        for i, params in enumerate(params_list, 1):
            # 解析参数：{"201": [71.3568, 29.3665]}
            for platform_id, coords in params.items():
                longitude, latitude = coords[0], coords[1]

                print(f"\n{'='*60}")
                print(f"执行平台 {platform_id} ({i}/{total_platforms})")
                print(f"{'='*60}")

                # 最后一个平台执行完后关闭服务
                is_last_platform = (i == total_platforms)

                platform_result = await self.run_platform(
                    executor=executor,
                    platform_id=platform_id,
                    longitude=longitude,
                    latitude=latitude,
                    timeout=timeout,
                    on_failure=on_failure,
                    keep_alive=not is_last_platform,  # 只有最后一个平台执行完后关闭服务
                )
                all_results.append(platform_result)

                # 失败处理
                if platform_result["status"] != "completed":
                    if on_failure == "stop":
                        print(f"\n停止执行，剩余 {total_platforms - i} 个平台")
                        # 手动关闭服务
                        if executor._service:
                            await executor._service.shutdown()
                        break
                    else:
                        print(f"\n继续执行下一个平台...")

        return all_results


def print_summary(results: List[Dict]):
    """打印执行汇总"""
    print("\n" + "=" * 60)
    print("批量执行汇总")
    print("=" * 60)

    total_success = 0
    total_commands = 0

    for r in results:
        icon = "OK" if r["status"] == "completed" else "PARTIAL"
        total_success += r["success_count"]
        total_commands += r["total_count"]
        print(f"  [{icon}] 平台 {r['platform_id']}: {r['success_count']}/{r['total_count']} 指令成功")

    print(f"\n  总计: {total_success}/{total_commands} 指令成功")
    print(f"  平台: {len(results)} 个")
    print("=" * 60)


async def main_async(args) -> int:
    try:
        params_list = load_params(args.params)
        template = load_template(args.template)
    except Exception as e:
        print(f"[错误] 加载失败: {e}")
        return 1

    print(f"批量任务: {template.get('name', 'Untitled')}")
    print(f"平台数: {len(params_list)}")
    print(f"每平台指令数: {len(template.get('commands', []))}")

    executor = BatchExecutor(
        template=template,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
    )
    results = await executor.run_batch(
        params_list=params_list,
        timeout=args.timeout,
        on_failure=args.on_failure,
    )

    print_summary(results)

    # 返回状态码
    all_success = all(r["status"] == "completed" for r in results)
    return 0 if all_success else 1


def main():
    parser = argparse.ArgumentParser(description="批量指令执行器")
    parser.add_argument("--params", "-p", required=True, help="参数列表JSON文件")
    parser.add_argument("--template", "-t", help="指令模板JSON文件（可选，使用内置默认模板）")
    parser.add_argument("--timeout", type=float, default=600, help="单条指令超时时间（秒）")
    parser.add_argument("--max-steps", type=int, default=50, help="最大执行步数")
    parser.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    parser.add_argument("--on-failure", choices=["stop", "continue"], default="continue",
                        help="失败策略：stop停止，continue继续下一个平台")
    args = parser.parse_args()

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()