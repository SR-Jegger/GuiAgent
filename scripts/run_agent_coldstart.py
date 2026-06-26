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

from agent_graph import run_agent_async
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
    parser.add_argument(
        "--no-intent-mapping",
        action="store_true",
        help="Disable intent mapping mode (default: enabled). When enabled, "
             "task_decomposer uses intent_mappings.json to resolve sub_steps via "
             "keyword match. Use this flag to fall back to the LLM/rule decomposer.",
    )
    parser.add_argument(
        "--intent-mapping-config-path",
        type=str,
        default=None,
        help="Path to intent_mappings.json (default: data/intent_mappings.json).",
    )
    parser.add_argument(
        "--semantic-matched-id",
        type=str,
        default=None,
        help="Directly specify a matched mapping ID (e.g. platform_task_assign) "
             "to skip keyword matching. Always triggers intent mapping mode "
             "regardless of --no-intent-mapping.",
    )
    # Browser pre-step options
    parser.add_argument(
        "--target-url",
        type=str,
        default=None,
        help="Initial URL to open via Playwright before desktop pipeline",
    )
    parser.add_argument(
        "--browser-headless",
        action="store_true",
        help="Run Playwright browser in headless mode",
    )
    parser.add_argument(
        "--browser-storage-state",
        type=str,
        default=None,
        help="Path to Playwright storage state JSON (for session persistence)",
    )
    parser.add_argument(
        "--browser-user-data-dir",
        type=str,
        default=None,
        help="Persistent browser profile directory",
    )
    parser.add_argument(
        "--cdp-endpoint",
        type=str,
        default=None,
        help="Connect to an already-running browser via CDP (e.g. http://localhost:9222)",
    )
    parser.add_argument(
        "--input-images",
        type=str,
        nargs="*",
        default=None,
        help="Paths to user-provided images for multimodal input",
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

    # Encode user-provided images to base64 data URIs
    from utils.utils import parse_input_images_from_text

    # First, extract file: and data:image prefixes from instruction text
    instruction, input_images = parse_input_images_from_text(instruction)

    # Then merge with --input-images CLI args (if any)
    if args.input_images:
        import base64
        if input_images is None:
            input_images = []
        for img_path in args.input_images:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                input_images.append(f"data:image/png;base64,{b64}")
                print(f"[INFO] Encoded input image: {img_path}")
            else:
                print(f"[WARN] Input image not found: {img_path}")

    # Load model config
    modelconfig = json.load(open("nodes/model_config.json"))

    # Intent mapping is ON by default. --no-intent-mapping disables it.
    # --semantic-matched-id always forces it on (overrides --no-intent-mapping).
    use_intent_mapping = (not args.no_intent_mapping) or bool(args.semantic_matched_id)
    semantic_matched_id = args.semantic_matched_id
    semantic_parameters: dict = {}

    # If semantic_matched_id is given, optionally pre-extract parameters from
    # the instruction using that mapping's extract_pattern definitions.
    if semantic_matched_id:
        try:
            from nodes.task_decomposer_node import IntentMappingConfig
            cfg_path = args.intent_mapping_config_path or "data/intent_mappings.json"
            cfg = IntentMappingConfig(cfg_path)
            mapping = cfg.get_mapping_by_id(semantic_matched_id)
            if mapping:
                extracted = cfg.extract_parameters(instruction, mapping)
                if extracted:
                    semantic_parameters = extracted
                    print(f"[INFO] Pre-extracted parameters for '{semantic_matched_id}': {semantic_parameters}")
            else:
                print(f"[WARN] semantic_matched_id='{semantic_matched_id}' not found in {cfg_path}")
        except Exception as exc:
            print(f"[WARN] Failed to pre-extract parameters: {exc}")

    # Run the agent
    import asyncio
    final_state = asyncio.run(run_agent_async(
        task_name=task_name,
        instruction=instruction,
        MODEL_CONFIG=modelconfig,
        max_steps=args.max_steps,
        max_retries=args.max_retries,
        add_info=args.add_info or None,
        rules_dir=args.rules_dir,
        input_images=input_images,
        target_url=args.target_url,
        browser_headless=args.browser_headless,
        browser_storage_state=args.browser_storage_state,
        browser_user_data_dir=args.browser_user_data_dir,
        cdp_endpoint=args.cdp_endpoint,
        use_intent_mapping=use_intent_mapping,
        intent_mapping_config_path=args.intent_mapping_config_path,
        semantic_matched_id=semantic_matched_id,
        semantic_parameters=semantic_parameters or None,
    ))

    # Return exit code based on final status
    if final_state.get("stop_flag") and final_state.get("execution_status") != "error":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
