"""
LLM Client - Unified interface for LLM providers.

Uses configuration from nodes/model_config.json.

Supports OpenAI-compatible APIs including:
- OpenAI official API
- Local models (vLLM, etc.)
- Other compatible providers (DeepSeek, Moonshot, etc.)

Usage:
    from learning.llm_client import LLMClient

    # Use default model from config
    client = LLMClient.from_config()

    # Or specify a model name from model_config.json
    client = LLMClient.from_config(model_name="local_qwen8b")

    response = client.chat(messages=[{"role": "user", "content": "Hello"}])
"""

import os
import json
from typing import Optional, List, Dict, Any
from openai import OpenAI


# Path to model configuration
MODEL_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "nodes",
    "model_config.json"
)

class LLMClient:
    """
    Unified LLM client for chat completions.

    Features:
    - OpenAI-compatible API interface
    - Support for local and remote models
    - JSON response format support
    - Connection validation
    - Configuration from nodes/model_config.json
    """

    def __init__(
        self,
        model_config: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize the LLM client.

        Args:
            model_config: Pre-loaded model config dict (highest priority)
            model_name: Model name from model_config.json (e.g., "local_qwen8b")
            base_url: API base URL (overrides config if provided)
            api_key: API key (overrides config if provided)
            model: Model name (overrides config if provided)
        """
        # Load from config file if not provided      
        with open(MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        models = config.get("models", {})
        model_config = models.get(model_name, {})
        print(f"[LLMClient] Loaded config for model: {model_name}")
        
        # Override with explicit parameters
        self.model = model or model_config.get("model", "gpt-4o-mini")
        self.base_url = base_url or model_config.get("base_url", "https://api.openai.com/v1")
        self.api_key = api_key or model_config.get("api_key", "EMPTY")

        # Validate API key
        if not self.api_key:
            raise ValueError(
                "API key not provided. Set api_key parameter or configure it in nodes/model_config.json"
            )

        # Initialize OpenAI client with timeout
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=30.0,  # 30 second timeout for API calls
        )

        print(f"[LLMClient] Initialized with model={self.model}, base_url={self.base_url}")

    @classmethod
    def from_config(cls, model_name: Optional[str] = None) -> "LLMClient":
        """
        Create LLMClient from model_config.json.

        Args:
            model_name: Model name from config (e.g., "local_qwen8b", "gui-plus-20260226")
                       If None, uses the default model.

        Returns:
            Configured LLMClient instance
        """
        return cls(model_name=model_name)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1, lower = more deterministic)
            max_tokens: Maximum tokens to generate
            response_format: "json" for JSON output, None for text
            **kwargs: Additional arguments to pass to the API

        Returns:
            Response content as string
        """
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens:
            request_kwargs["max_tokens"] = max_tokens

        if response_format == "json":
            request_kwargs["response_format"] = {"type": "json_object"}

        # Add any additional kwargs
        request_kwargs.update(kwargs)

        response = self.client.chat.completions.create(**request_kwargs)

        return response.choices[0].message.content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send a chat completion request expecting JSON response.

        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional arguments

        Returns:
            Parsed JSON response as dict
        """
        content = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format="json",
            **kwargs
        )

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"[LLMClient] Failed to parse JSON response: {e}")
            print(f"Raw response: {content[:500]}...")
            raise ValueError(f"Invalid JSON response from LLM: {e}")

    def validate_connection(self) -> bool:
        """
        Validate the LLM connection with a simple test request.

        Returns:
            True if connection is successful
        """
        try:
            test_messages = [{"role": "user", "content": "Reply with just: OK"}]
            response = self.chat(test_messages, max_tokens=10)
            print(f"[LLMClient] Connection validated: {response.strip()}")
            return True
        except Exception as e:
            print(f"[LLMClient] Connection validation failed: {e}")
            return False

    def list_models(self) -> List[str]:
        """
        List available models from the API.

        Returns:
            List of model names
        """
        try:
            models = self.client.models.list()
            return [model.id for model in models.data]
        except Exception as e:
            print(f"[LLMClient] Failed to list models: {e}")
            return []


def create_llm_client(
    model_name: Optional[str] = "local_qwen8b",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMClient:
    """
    Factory function to create an LLM client.

    Args:
        model_name: Model name from model_config.json (e.g., "local_qwen8b")
                   If provided, loads config from file.
        base_url: API base URL (overrides config if provided)
        api_key: API key (overrides config if provided)
        model: Model name (overrides config if provided)

    Returns:
        Configured LLMClient instance
    """
    if model_name is not None:
        # Load from config file
        return LLMClient.from_config(model_name=model_name)
    else:
        # Create with explicit parameters or defaults
        return LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model
        )
        
