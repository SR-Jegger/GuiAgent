"""
Judge node for GUI automation agent.

Responsible for evaluating if task needs template matching.
"""

import re
from typing import TYPE_CHECKING

from openai import OpenAI

from nodes.types import AgentState
from prompt_llm import JUDGE_PROMPT_2 as JUDGE_PROMPT


def judge_node(state: AgentState) -> AgentState:
    """
    Evaluate if the task requires template matching.

    This node:
    1. Extracts observation and thought from LLM response
    2. Calls judge LLM to determine if template matching is needed
    3. Sets judge_result for routing

    Args:
        state: Current agent state

    Returns:
        Updated state with judge_result
    """
    step_id = state.get("step_id", 0)
    llm_response = state.get("llm_response", "")
    task = state.get("instruction", "")
    
    MODEL_CONFIG = state.get("MODEL_CONFIG", None)
    llm_config = MODEL_CONFIG["models"]["local_qwen8b"]
    model = llm_config["model"]
    base_url = llm_config["base_url"]
    api_key = llm_config["api_key"]

    print(f"\n[JUDGE] Step {step_id}: Evaluating if task needs template match...")

    pattern = r"Observation:(.*?)Thought:(.*?)(?:Action:|)"
    match = re.search(pattern, llm_response, re.DOTALL)
    observation = match.group(1).strip() if match else ""
    thought = match.group(2).strip() if match else ""

    print(f"[JUDGE] Observation: {observation}")
    print(f"[JUDGE] Thought: {thought}")

    prompt = JUDGE_PROMPT.format(observation=observation, agent_thought=thought)
    message_for_judge = [{
        "role": "user",
        "content": [{"type": "text", "text": prompt}]
    }]

    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=message_for_judge,
        )
        print(f"\n[JUDGE] Received response from Judge LLM:")

        if "<template_match>" in response.choices[0].message.content.lower():
            decision = "template_match"
        else:
            decision = "execute"

        state["judge_result"] = decision
        state["template_request"] = response.choices[0].message.content
        state["execution_status"] = "success"
        print(f"[JUDGE] Decision: {decision}")

        return {
            "judge_result": decision,
            "template_request": response.choices[0].message.content,
            "execution_status": "success",
        }

    except Exception as e:
        print(f"[JUDGE] Warning: Could not get Judge content: {e}")
        return {
            "execution_status": "error",
            "error_message": f"Judge node error: {str(e)}",
        }
