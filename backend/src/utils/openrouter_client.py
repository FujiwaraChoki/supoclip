"""
OpenRouter API client for multi-LLM access.
"""
import os
import logging
import aiohttp
import json
from typing import Dict, Any, Optional, List
from .retry_utils import async_retry

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """Client for OpenRouter unified LLM API."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize OpenRouter client.

        Args:
            api_key: OpenRouter API key (defaults to env var)
        """
        self.api_key = api_key or os.getenv('OPENROUTER_API_KEY')
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

        self.base_url = "https://openrouter.ai/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": os.getenv('OPENROUTER_REFERER', 'http://localhost:3000'),
            "X-Title": "SupoClip AI Council",
            "Content-Type": "application/json"
        }

    @async_retry(max_attempts=3, delay=2, backoff=2)
    async def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4000,
        response_format: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Send chat completion request to OpenRouter.

        Args:
            model: Model ID (e.g., "anthropic/claude-sonnet-4-20250514")
            messages: List of message dicts with role and content
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            response_format: Optional format specification (e.g., {"type": "json_object"})

        Returns:
            Response dict with content and metadata
        """
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        if response_format:
            payload["response_format"] = response_format

        logger.info(f"🤖 Calling {model} via OpenRouter")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"OpenRouter API error ({response.status}): {error_text}")
                    raise Exception(f"OpenRouter API error: {error_text}")

                result = await response.json()

                # Extract content
                content = result['choices'][0]['message']['content']

                return {
                    'content': content,
                    'model': result.get('model', model),
                    'usage': result.get('usage', {}),
                    'finish_reason': result['choices'][0].get('finish_reason')
                }

    async def batch_chat_completions(
        self,
        requests: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Send multiple chat completion requests in parallel.

        Args:
            requests: List of request dicts, each with 'model', 'messages', etc.

        Returns:
            List of response dicts
        """
        import asyncio

        tasks = [
            self.chat_completion(**req)
            for req in requests
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any failures
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Request {i} failed: {result}")
                processed_results.append({
                    'error': str(result),
                    'request': requests[i]
                })
            else:
                processed_results.append(result)

        return processed_results


# Singleton instance
_client = None


def get_openrouter_client() -> OpenRouterClient:
    """Get or create OpenRouter client singleton."""
    global _client
    if _client is None:
        _client = OpenRouterClient()
    return _client


async def call_openrouter(
    model: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4000,
    json_mode: bool = False
) -> str:
    """
    Convenience function to call OpenRouter with a simple prompt.

    Args:
        model: Model ID
        prompt: User prompt
        system_prompt: Optional system prompt
        temperature: Sampling temperature
        max_tokens: Maximum tokens
        json_mode: Request JSON output format

    Returns:
        Response content as string
    """
    client = get_openrouter_client()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response_format = {"type": "json_object"} if json_mode else None

    result = await client.chat_completion(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format
    )

    return result['content']
