"""
LangGraph-based GUI Automation Agent - Main Entry Point

This is the main entry point for the CLI.
For API usage, import run_agent_async from agent_graph.

Usage:
    cd Mobile-Agent-v3.5/computer_use
    python run_agent.py \
        --instruction "The instruction you want the agent to complete" \
        --model "Model name" \
        --add_info "Optional supplementary knowledge"

Example:
    python run_agent.py --mdpath test_md/test_ui1.md --max_steps 30
"""

import argparse
import sys
import os
import json

# 设置 NO_PROXY（从配置文件读取，避免代理干扰本地服务）
def _setup_no_proxy():
    """从配置文件读取 ASR host 并设置 NO_PROXY"""
    try:
        config_path = "nodes/model_config.json"
        if os.path.exists(config_path):
            config = json.load(open(config_path))
            asr_host = config.get("asr", {}).get("host", "192.168.137.2")
            os.environ["NO_PROXY"] = asr_host
            os.environ["no_proxy"] = asr_host
    except Exception:
        # 默认值
        os.environ["NO_PROXY"] = "192.168.137.2"
        os.environ["no_proxy"] = "192.168.137.2"

_setup_no_proxy()

from agent_graph import run_agent
from utils.utils import process_markdown_task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LangGraph-based GUI Automation Agent"
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default="default_task",
        help="Task name for output directory",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        required=False,
        help="The task instruction for the agent to complete",
    )
    parser.add_argument(
        "--mdpath",
        type=str,
        default="test_md/test_ui1.md",
        required=False,
        help="Path to markdown file containing the task instruction",
    )
    parser.add_argument(
        "--add_info",
        type=str,
        default="",
        help="Optional supplementary knowledge for the task",
    )
    parser.add_argument(
        "--max_steps",
        type=int,
        default=50,
        help="Maximum number of interaction steps (default: 50)",
    )
    parser.add_argument(
        "--max_retries",
        type=int,
        default=3,
        help="Maximum retry attempts per step (default: 3)",
    )
    parser.add_argument(
        "--rules_dir",
        type=str,
        default="./rules",
        help="Directory containing rule JSON files (default: ./rules)",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # Determine instruction source
    instruction = args.instruction or ""
    mdpath = args.mdpath if not instruction else None

    read_markdown = process_markdown_task(mdpath)
    task_name = args.task_name
    if read_markdown:
        task_name = read_markdown["extracted_title"]
        instruction = read_markdown["prompt_for_llm"]

    if not instruction:
        print("[ERROR] No instruction provided. Use --instruction or --mdpath")
        sys.exit(1)

    # Load model config
    modelconfig = json.load(open("nodes/model_config.json"))

    # Run the agent
    final_state = run_agent(
        task_name=task_name,
        instruction=instruction,
        MODEL_CONFIG=modelconfig,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
        add_info=args.add_info or None,
        rules_dir=args.rules_dir,
    )

    # Return exit code based on final status
    if final_state.get("stop_flag") and final_state.get("execution_status") != "error":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
