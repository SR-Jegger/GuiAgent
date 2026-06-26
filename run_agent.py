"""
LangGraph-based GUI Automation Agent - CLI Entry (HTTP client mode)

This CLI is a thin client over the running FastAPI server (start_server.py).
It submits tasks via HTTP and polls for completion. The server owns the
persistent browser (BrowserManager / CDP), agent graph, OCR, and task queue.

For a standalone cold-start CLI that does not need the server, use:
    python scripts/run_agent_coldstart.py

Usage:
    python start_server.py            # terminal 1: start the server
    python run_agent.py --mdpath test_md/test_ui1.md   # terminal 2: submit task

Examples:
    python run_agent.py --mdpath test_md/test_ui1.md --max_steps 30
    python run_agent.py --instruction "右键点击206号平台卡片" \
        --semantic-matched-id platform_task_assign
"""

import argparse
import sys
import os
import json
import time
import signal


# Default server endpoint
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"

# Polling config
POLL_INTERVAL_SECONDS = 1.0
CANCEL_GRACE_SECONDS = 5.0  # wait for server to reach cancelled terminal state

# TaskStatus values (must match app/services/application_service.py)
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUI Agent CLI (HTTP client to start_server.py)"
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
        default=None,
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
             "task_decomposer uses intent_mappings.json to resolve sub_steps.",
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
             "to skip keyword matching. Always triggers intent mapping mode.",
    )
    parser.add_argument(
        "--input-images",
        type=str,
        nargs="*",
        default=None,
        help="Paths to user-provided images for multimodal input",
    )
    parser.add_argument(
        "--server-url",
        type=str,
        default=DEFAULT_SERVER_URL,
        help=f"Server base URL (default: {DEFAULT_SERVER_URL})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=POLL_INTERVAL_SECONDS,
        help=f"Polling interval in seconds (default: {POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit task and exit immediately without waiting for completion",
    )

    return parser.parse_args()


def _read_md(path: str) -> tuple[str, str]:
    """Read markdown file, return (title, prompt_for_llm).

    Same logic as utils.utils.process_markdown_task: first line is title
    (with leading # stripped, illegal filename chars replaced), rest is
    the instruction text.
    """
    import re
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if not lines:
        raise ValueError("File is empty")
    title = re.sub(r"^#+\s*", "", lines[0].strip()).strip()
    safe_filename = re.sub(r'[\\/:*?"<>|]', "_", title)
    rest_lines = lines[1:]
    if rest_lines and rest_lines[0].strip() == "":
        rest_lines = rest_lines[1:]
    return safe_filename, "".join(rest_lines)


def _check_server_health(server_url: str) -> bool:
    """Quick health check; returns True if server responds."""
    try:
        import requests
        r = requests.get(f"{server_url}/api/v1/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _submit_task(server_url: str, payload: dict) -> dict:
    """POST /api/v1/tasks, return response JSON."""
    import requests
    r = requests.post(f"{server_url}/api/v1/tasks", json=payload, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Submit failed: HTTP {r.status_code} - {r.text}")
    return r.json()


def _poll_task(server_url: str, task_id: str, interval: float) -> dict:
    """GET /api/v1/tasks/{task_id}, return current status dict."""
    import requests
    r = requests.get(f"{server_url}/api/v1/tasks/{task_id}", timeout=5)
    if r.status_code != 200:
        raise RuntimeError(f"Poll failed: HTTP {r.status_code} - {r.text}")
    return r.json()


def _cancel_task(server_url: str, task_id: str) -> None:
    """POST /api/v1/tasks/{task_id}/cancel."""
    try:
        import requests
        requests.post(f"{server_url}/api/v1/tasks/{task_id}/cancel", timeout=5)
    except Exception as exc:
        print(f"[WARN] Cancel request failed: {exc}")


def main():
    args = parse_args()

    # Determine instruction source
    instruction = args.instruction or ""
    mdpath = args.mdpath if not instruction else None

    if mdpath:
        try:
            task_name, md_instruction = _read_md(mdpath)
            instruction = md_instruction
        except Exception as exc:
            print(f"[ERROR] Failed to read markdown file: {exc}")
            sys.exit(1)
    else:
        task_name = args.task_name

    if not instruction:
        print("[ERROR] No instruction provided. Use --instruction or --mdpath")
        sys.exit(1)

    # Encode user-provided images to base64 data URIs
    input_images = None
    if args.input_images:
        import base64
        input_images = []
        for img_path in args.input_images:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                input_images.append(f"data:image/png;base64,{b64}")
                print(f"[INFO] Encoded input image: {img_path}")
            else:
                print(f"[WARN] Input image not found: {img_path}")

    # Intent mapping is ON by default. --no-intent-mapping disables it.
    # --semantic-matched-id always forces it on.
    use_intent_mapping = (not args.no_intent_mapping) or bool(args.semantic_matched_id)

    # Build request payload
    payload = {
        "task_name": task_name,
        "instruction": instruction,
        "max_steps": args.max_steps,
        "max_retries": args.max_retries,
        "add_info": args.add_info or None,
        "rules_dir": args.rules_dir,
        "use_intent_mapping": use_intent_mapping,
        "intent_mapping_config_path": args.intent_mapping_config_path,
        "semantic_matched_id": args.semantic_matched_id,
        "semantic_parameters": None,
        "input_images": input_images,
    }

    print("=" * 60)
    print("GUI Agent CLI (HTTP client mode)")
    print("=" * 60)
    print(f"Server URL: {args.server_url}")
    print(f"Instruction: {instruction[:100]}{'...' if len(instruction) > 100 else ''}")
    print(f"Task name: {task_name}")
    print(f"Max Steps: {args.max_steps}")
    print(f"Intent Mapping: {use_intent_mapping}")
    if args.semantic_matched_id:
        print(f"Semantic Matched ID: {args.semantic_matched_id}")
    print("=" * 60)

    # Health check
    if not _check_server_health(args.server_url):
        print(f"\n[ERROR] Server not reachable at {args.server_url}")
        print("Start it first:  python start_server.py")
        sys.exit(2)
    print("[INFO] Server reachable.")

    # Submit task
    try:
        response = _submit_task(args.server_url, payload)
    except Exception as exc:
        print(f"\n[ERROR] Failed to submit task: {exc}")
        sys.exit(2)

    task_id = response.get("task_id")
    if not task_id:
        print(f"\n[ERROR] Server did not return a task_id. Response: {response}")
        sys.exit(2)

    print(f"[INFO] Task submitted: {task_id}")
    print(f"[INFO] Status: {response.get('status', 'unknown')}")
    print(f"[INFO] Dashboard: {args.server_url}/dashboard")
    print()

    # --no-wait: exit immediately after submit
    if args.no_wait:
        print("[INFO] --no-wait set, exiting without waiting for completion.")
        sys.exit(0)

    # Setup Ctrl+C handler: cancel task on interrupt
    cancelled_by_user = {"value": False}

    def _on_sigint(signum, frame):
        if not cancelled_by_user["value"]:
            cancelled_by_user["value"] = True
            print("\n[INFO] Ctrl+C received, cancelling task on server...")
            _cancel_task(args.server_url, task_id)
        else:
            print("\n[INFO] Second Ctrl+C, exiting immediately.")
            sys.exit(130)

    signal.signal(signal.SIGINT, _on_sigint)

    # Poll for completion
    last_status = None
    last_progress_step = None
    start_time = time.time()

    while True:
        try:
            task = _poll_task(args.server_url, task_id, args.poll_interval)
        except Exception as exc:
            print(f"[WARN] Poll failed: {exc}, retrying...")
            time.sleep(args.poll_interval)
            continue

        status = task.get("status", "unknown")
        progress = task.get("progress") or {}

        # Print progress updates (when current step changes)
        cur_step = progress.get("current_step")
        cur_idx = progress.get("current_index")
        total = progress.get("total_steps")
        if cur_step != last_progress_step:
            if cur_idx and total:
                print(f"[{status}] step {cur_idx}/{total}: {cur_step}")
            elif cur_step:
                print(f"[{status}] {cur_step}")
            last_progress_step = cur_step

        if status != last_status:
            if status == "running":
                print(f"[INFO] Task running...")
            elif status == "completed":
                elapsed = time.time() - start_time
                print(f"\n[OK] Task completed in {elapsed:.1f}s")
                result = task.get("result")
                if result:
                    print(f"[INFO] Result: {json.dumps(result, ensure_ascii=False)[:500]}")
                sys.exit(0)
            elif status == "failed":
                elapsed = time.time() - start_time
                print(f"\n[FAIL] Task failed after {elapsed:.1f}s")
                if task.get("error"):
                    print(f"[ERROR] {task['error']}")
                sys.exit(1)
            elif status == "cancelled":
                elapsed = time.time() - start_time
                if cancelled_by_user["value"]:
                    print(f"\n[INFO] Task cancelled by user after {elapsed:.1f}s")
                else:
                    print(f"\n[INFO] Task cancelled after {elapsed:.1f}s")
                sys.exit(130)
            last_status = status

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
