"""
LLM Client Wrapper
Unified OpenAI format API calls
Supports Ollama num_ctx parameter to prevent prompt truncation
"""

import json
import os
import re
import time
import random
import threading
from typing import Optional, Dict, Any, List
import openai
from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.llm_client')


class LLMClient:
    """
    LLM Client with thread-local storage to prevent Windows Socket error (10038)
    and smart JSON repair logic.
    """

    _thread_local = threading.local()

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 300.0,
        max_retries: Optional[int] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        self.timeout = timeout
        self.max_retries = max_retries if max_retries is not None else Config.LLM_MAX_RETRIES

        if not self.api_key:
            raise ValueError("LLM_API_KEY not configured")

        # Ollama context window size
        self._num_ctx = int(os.environ.get('OLLAMA_NUM_CTX', '8192'))

    @property
    def client(self) -> OpenAI:
        """Get or create thread-local OpenAI client to avoid WinError 10038"""
        if not hasattr(self._thread_local, "client"):
            logger.debug(f"Creating new OpenAI client for thread {threading.get_ident()}")
            self._thread_local.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._thread_local.client

    def _is_ollama(self) -> bool:
        """Check if we're talking to an Ollama server."""
        return '11434' in (self.base_url or '')

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096 * 3,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send chat request with manual retry and thread-safe client access
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        # For Ollama: pass num_ctx via extra_body
        if self._is_ollama() and self._num_ctx:
            kwargs["extra_body"] = {
                "options": {"num_ctx": self._num_ctx}
            }

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                # Use property that provides thread-local client
                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                # Keep <think> content if present, we'll parse it in frontend
                return content.strip()

            except (APIConnectionError, APITimeoutError) as e:
                last_error = e
                # If connection error, reset thread-local client for next attempt
                if hasattr(self._thread_local, "client"):
                    del self._thread_local.client

                if attempt < self.max_retries:
                    wait_time = (2 ** attempt) + random.random()
                    logger.warning(f"LLM connection error (attempt {attempt + 1}/{self.max_retries + 1}): {e}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"LLM connection failed after {self.max_retries + 1} attempts: {e}")
                    raise

            except RateLimitError as e:
                last_error = e
                if attempt < self.max_retries:
                    wait_time = 10 * (attempt + 1)
                    logger.warning(f"LLM rate limit (attempt {attempt + 1}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise

            except Exception as e:
                logger.error(f"LLM unexpected error: {str(e)}")
                raise

        raise last_error if last_error else Exception("LLM call failed")

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096 * 3
    ) -> Dict[str, Any]:
        """
        Send chat request and return JSON with smart repair logic
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # Clean markdown code block markers
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            # Attempt to repair truncated JSON
            repaired = self._repair_json(cleaned_response)
            try:
                return json.loads(repaired)
            except Exception:
                # Truncate long responses in error messages to avoid bloating logs/SSE
                truncated_resp = cleaned_response[:500] + "..." if len(cleaned_response) > 500 else cleaned_response
                raise ValueError(f"Invalid and unrepairable JSON format from LLM (truncated): {truncated_resp}")

    def _repair_json(self, json_str: str) -> str:
        """
        Robust logic to repair truncated JSON by balancing braces and brackets.
        Handles partial keys/values at the end of the string.
        """
        json_str = json_str.strip()

        # 1. Handle common truncation artifacts
        # Remove trailing commas
        json_str = re.sub(r',[\s\n]*$', '', json_str)

        # Remove partial keys/values: look for a trailing quote that isn't preceded by a colon or start of object
        # If the string ends with something like "key": "val... or "key": ...
        # We try to find the last complete structural element.

        # 2. Balance brackets and braces
        stack = []
        in_string = False
        escaped = False
        fixed_str = ""

        for i, char in enumerate(json_str):
            if char == '"' and not escaped:
                in_string = not in_string

            if not in_string:
                if char == '{':
                    stack.append('}')
                elif char == '[':
                    stack.append(']')
                elif char == '}' or char == ']':
                    if stack and stack[-1] == char:
                        stack.pop()

            if char == '\\' and not escaped:
                escaped = True
            else:
                escaped = False

            fixed_str += char

        # 3. Handle the case where we stopped inside a string
        if in_string:
            # If we were in a string, we might have half a key or value.
            # Easiest fix is to close the quote and let the stack close the objects.
            fixed_str += '"'

        # 4. Final cleaning: if we ended up with something like "key": " or "key": , remove the dangling key
        # This is a bit complex, but simple repair often works:
        fixed_str = re.sub(r',?\s*\"[^"]+\"\s*:\s*\"?$', '', fixed_str)
        fixed_str = re.sub(r',?\s*\"[^"]+\"\s*:\s*$', '', fixed_str)

        # Close all remaining scopes in reverse order
        while stack:
            fixed_str += stack.pop()

        return fixed_str
