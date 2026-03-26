"""
Parsers - Extract structured data from LLM responses.
"""

import ast
import json
import re


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------

def extract_tool_calls(text):
    """
    Extract all JSON objects from <tool_call>...</tool_call> blocks.

    Returns a list of parsed dicts. Blocks that fail to parse are skipped
    with a warning.
    """
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
    blocks = pattern.findall(text)

    actions = []
    for blk in blocks:
        blk = blk.strip()
        try:
            actions.append(ast.literal_eval(blk))
        except (ValueError, SyntaxError) as e:
            print(f"[WARN] Failed to parse tool_call block: {e} | snippet: {blk[:80]}...")
    return actions


def extract_template_request(text: str):
    """
    Extract JSON from <template_match>...</template_match>
    """

    pattern = r"<template_match>(.*?)</template_match>"
    match = re.search(pattern, text, re.DOTALL)

    if not match:
        return None

    try:
        request_json = match.group(1).strip()
        return json.loads(request_json)
    except Exception as e:
        print(f"[TEMPLATE_MATCH] JSON parse error: {e}")
        return None
    
def extract_action(text: str):
    """
    Extract the action from the LLM response.
    """
    pattern = r'Action:.*'
    match = re.search(pattern, text)
    if match:
        result = match.group(0)
        print(result)  # 输出: Action:xxxxx <tool>xxx</tool>
    return result.strip() if match else None
# ---------------------------------