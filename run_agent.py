"""
LangGraph-based GUI Automation Agent - Main Entry Point

This is the new main entry point for the refactored agent.
It uses LangGraph to manage state and control flow.

Usage:
    cd Mobile-Agent-v3.5/computer_use
    python run_agent.py \\
        --api_key "Your API key" \\
        --base_url "Your base URL of vllm service" \\
        --instruction "The instruction you want the agent to complete" \\
        --model "Model name" \\
        --add_info "Optional supplementary knowledge"

Example:
    python run_agent.py --mdpath test_md/test_ui1.md --max_steps 30
"""

import argparse
import sys

import os
os.environ["NO_PROXY"] = "192.168.137.2"
os.environ["no_proxy"] = "192.168.137.2"
# Import from the agent graph module
from agent_graph import run_agent, parse_args, main


if __name__ == "__main__":
    os.environ["NO_PROXY"] = "192.168.137.2"
    os.environ["no_proxy"] = "192.168.137.2"
    main()
