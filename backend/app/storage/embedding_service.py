"""
EmbeddingService — local and online embedding support
"""

import time
import logging
from typing import List, Optional, Union

import requests

from ..config import Config

logger = logging.getLogger('mirofish.embedding')


class EmbeddingService:
    """Generate embeddings using local Ollama or online API."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 30,
    ):
        self.model = model or Config.EMBEDDING_MODEL
        self.base_url = (base_url or Config.EMBEDDING_BASE_URL).rstrip('/')
        self.api_key = api_key or Config.EMBEDDING_API_KEY
        self.max_retries = max_retries
        self.timeout = timeout

        # Automatic provider detection
        if "nomic.ai" in self.base_url.lower():
            self.provider = "nomic"
            self._embed_url = self.base_url # Use user-provided specific endpoint
        elif self.api_key:
            self.provider = "online"
            self._embed_url = self.base_url
        elif "11434" in self.base_url or "ollama" in self.base_url.lower():
            self.provider = "ollama"
            self._embed_url = f"{self.base_url}/api/embed"
        else:
            self.provider = "online"
            self._embed_url = self.base_url

        # Simple in-memory cache (text -> embedding vector)
        self._cache: dict[str, List[float]] = {}
        self._cache_max_size = 2000
        self._dim = None # Will be determined after first call

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        if not text or not text.strip():
            raise EmbeddingError("Cannot embed empty text")

        text = text.strip()
        if text in self._cache:
            return self._cache[text]

        vectors = self._request_embeddings([text])
        vector = vectors[0]
        self._cache_put(text, vector)
        if self._dim is None:
            self._dim = len(vector)
        return vector

    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []

        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        for i, text in enumerate(texts):
            text = text.strip() if text else ""
            if text in self._cache:
                results[i] = self._cache[text]
            elif text:
                uncached_indices.append(i)
                uncached_texts.append(text)
            else:
                dim = self._dim or 768
                results[i] = [0.0] * dim

        if uncached_texts:
            all_vectors: List[List[float]] = []
            for start in range(0, len(uncached_texts), batch_size):
                batch = uncached_texts[start:start + batch_size]
                vectors = self._request_embeddings(batch)
                all_vectors.extend(vectors)

            for idx, vec, text in zip(uncached_indices, all_vectors, uncached_texts):
                results[idx] = vec
                self._cache_put(text, vec)
                if self._dim is None:
                    self._dim = len(vec)

        return results  # type: ignore

    def _request_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Make HTTP request to embedding endpoint with retry."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Initial Payload
        payload = {
            "model": self.model,
        }

        # Handle different input keys based on provider
        if self.provider == "nomic":
            payload["texts"] = texts
        else:
            payload["input"] = texts

        # Special handling for Nomic Atlas specific endpoint
        # The /v1/embedding/text endpoint sometimes prefers specific task types
        if self.provider == "nomic":
            payload["task_type"] = "search_document"
            if len(texts) == 1 and ("?" in texts[0] or len(texts[0]) < 100):
                payload["task_type"] = "search_query"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self._embed_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code == 422:
                    error_info = response.text
                    logger.warning(f"Nomic API 422 Error (Attempt {attempt+1}): {error_info}")

                    # Strategy 1: If it's a missing field error for 'texts', we already tried to fix it in payload setup.
                    # But if we are here, something else might be wrong with task_type.
                    if "task_type" in payload:
                        logger.info("Retrying without task_type...")
                        del payload["task_type"]
                        continue

                    # Strategy 2: If multiple texts, try reducing batch size (already handled by caller, but here we just fail)
                    raise EmbeddingError(f"API rejected payload: {error_info}")

                response.raise_for_status()
                data = response.json()

                # Handle various response formats
                embeddings = []
                if "embeddings" in data:
                    embeddings = data["embeddings"]
                elif "data" in data and isinstance(data["data"], list):
                    # OpenAI format
                    embeddings = [item["embedding"] for item in data["data"]]
                elif isinstance(data, list):
                    # Direct list format
                    embeddings = data
                else:
                    raise EmbeddingError(f"Unsupported response format from {self._embed_url}")

                if not embeddings or len(embeddings) != len(texts):
                    # Special case: single result as flat list
                    if len(texts) == 1 and embeddings and isinstance(embeddings[0], float):
                        return [embeddings]
                    raise EmbeddingError(f"Expected {len(texts)} vectors, got {len(embeddings)}")

                return embeddings

            except Exception as e:
                last_error = e
                if isinstance(e, requests.exceptions.HTTPError):
                    status = e.response.status_code
                    if status == 401: raise EmbeddingError("Invalid API Key")
                    if status == 404: raise EmbeddingError(f"Endpoint not found: {self._embed_url}")
                    if status < 500 and status != 429 and status != 422:
                        raise EmbeddingError(f"Client error: {e}")

                logger.warning(f"Embedding attempt {attempt+1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise EmbeddingError(f"All retries failed: {last_error}")

    def _cache_put(self, text: str, vector: List[float]) -> None:
        if len(self._cache) >= self._cache_max_size:
            keys_to_remove = list(self._cache.keys())[:self._cache_max_size // 10]
            for key in keys_to_remove:
                del self._cache[key]
        self._cache[text] = vector

    def health_check(self) -> bool:
        try:
            vec = self.embed("health check")
            return len(vec) > 0
        except Exception:
            return False


class EmbeddingError(Exception):
    pass
