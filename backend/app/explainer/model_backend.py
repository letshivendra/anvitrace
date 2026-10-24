"""Model backend abstraction for Case Explainer.

Pluggable interface:
    ModelBackend.generate(prompt, max_tokens, stop) -> str

Implementations:
    NullBackend  — always returns empty string; used for tests and fallback.
    LlamaCppBackend — loads a GGUF model via llama-cpp-python (optional dep).

Safety rules enforced here:
    - Never make any network calls.
    - Never load pickle files.
    - Manifest hash is verified before the model is used (see manifest.py).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("anvitrace")


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class ModelBackend(ABC):
    """Pluggable local-model backend."""

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 256, stop: list[str] | None = None) -> str:
        """Generate a completion given a prompt.

        Args:
            prompt: Full formatted prompt (system + context + user turn).
            max_tokens: Maximum tokens to generate.
            stop: Optional list of stop sequences.

        Returns:
            Generated text string (may be empty on failure).
        """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model name for audit records."""

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Version string from manifest (or 'unknown')."""

    @property
    @abstractmethod
    def model_sha256(self) -> str:
        """Verified SHA-256 of the loaded model file (or empty string)."""

    def model_info(self) -> dict[str, str]:
        """Return model metadata dict for inclusion in every response."""
        return {
            "name": self.model_name,
            "version": self.model_version,
            "sha256": self.model_sha256,
        }


# ---------------------------------------------------------------------------
# NullBackend — always available, no model required
# ---------------------------------------------------------------------------

class NullBackend(ModelBackend):
    """Fallback backend that returns empty strings.

    Used in:
    - Tests (deterministic, no model dependency).
    - Production fallback when model file is absent or hash mismatches.
    """

    def generate(self, prompt: str, max_tokens: int = 256, stop: list[str] | None = None) -> str:
        return ""

    @property
    def model_name(self) -> str:
        return "null"

    @property
    def model_version(self) -> str:
        return "0.0.0"

    @property
    def model_sha256(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# LlamaCppBackend — optional, requires llama-cpp-python
# ---------------------------------------------------------------------------

class LlamaCppBackend(ModelBackend):
    """Wraps llama-cpp-python for local GGUF model inference.

    Loaded lazily at first use.  Network access at runtime is never needed
    (and any attempt is a bug — this module imports only stdlib + llama_cpp).

    Config (all from settings / env):
        MODEL_PATH     : absolute path to the .gguf file
        CONTEXT_SIZE   : n_ctx (default 2048)
        MAX_TOKENS     : default max tokens (default 256)
        TEMPERATURE    : sampling temperature (default 0.1)
    """

    def __init__(
        self,
        model_path: str,
        *,
        context_size: int = 2048,
        temperature: float = 0.1,
        verified_sha256: str = "",
        manifest_version: str = "unknown",
        manifest_name: str = "",
    ) -> None:
        self._model_path = model_path
        self._context_size = context_size
        self._temperature = temperature
        self._verified_sha256 = verified_sha256
        self._manifest_version = manifest_version
        self._manifest_name = manifest_name or _basename(model_path)
        self._llm: Any = None  # loaded lazily

    def _load(self) -> None:
        """Load the GGUF model.  Raises ImportError if llama-cpp-python is absent."""
        if self._llm is not None:
            return
        try:
            from llama_cpp import Llama  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python is not installed.  "
                "Install it with: pip install llama-cpp-python  "
                "or use NullBackend for tests."
            ) from exc

        # Refuse to load pickle files
        if self._model_path.endswith(".pkl") or self._model_path.endswith(".pickle"):
            raise ValueError(
                f"Refused to load pickle file '{self._model_path}'. "
                "Only GGUF/safetensors models are supported."
            )

        logger.info(f"[explainer] Loading model from {self._model_path!r}")
        self._llm = Llama(
            model_path=self._model_path,
            n_ctx=self._context_size,
            verbose=False,
        )
        logger.info("[explainer] Model loaded successfully.")

    def generate(self, prompt: str, max_tokens: int = 256, stop: list[str] | None = None) -> str:
        """Run inference.  Returns empty string on any error."""
        try:
            self._load()
        except Exception as exc:
            logger.error(f"[explainer] Model load failed: {exc}")
            return ""
        try:
            result = self._llm(
                prompt,
                max_tokens=max_tokens,
                temperature=self._temperature,
                stop=stop or ["</answer>", "\n\nUser:", "\n\nHuman:"],
                echo=False,
            )
            return result["choices"][0]["text"].strip()
        except Exception as exc:
            logger.error(f"[explainer] Inference error: {exc}")
            return ""

    @property
    def model_name(self) -> str:
        return self._manifest_name

    @property
    def model_version(self) -> str:
        return self._manifest_version

    @property
    def model_sha256(self) -> str:
        return self._verified_sha256


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _basename(path: str) -> str:
    import os
    return os.path.basename(path)
