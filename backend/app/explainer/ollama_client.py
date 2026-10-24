"""Localhost Ollama client for ANVITRACE Case Explainer and Summarizer.

Enforces:
- Localhost only (http://localhost:11434 or 127.0.0.1:11434), no outbound internet calls.
- Timeout default 30.0 seconds (configurable via OLLAMA_TIMEOUT env var).
- Non-streaming JSON request/response to POST /api/chat.
- Startup model verification against models_manifest.json with hmac.compare_digest.
- Audit event MODEL_HASH_MISMATCH on hash discrepancies; refuses to load model and falls back to template mode.
- Warm-up ping at startup for zero-latency demo responses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.utils.logging import log_event

logger = logging.getLogger("anvitrace")

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT", "30.0"))


def _project_root() -> Path:
    """Return backend project root."""
    return Path(__file__).resolve().parent.parent.parent


class OllamaClient:
    """Client for local Ollama server with hash verification, warmup, and fail-safe fallback."""

    def __init__(
        self,
        base_url: str | None = None,
        model_name: str | None = None,
        timeout: float | None = None,
    ):
        raw_url = (base_url or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        # Security: strictly verify localhost / loopback only
        parsed = urllib.parse.urlparse(raw_url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(
                f"Security violation: Ollama server must be strictly on localhost. Refusing host: {hostname}"
            )

        self.base_url = raw_url
        self.model_name = (
            model_name or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        ).strip()
        self.timeout = timeout if timeout is not None else float(os.environ.get("OLLAMA_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
        self.hash_verified: bool = True
        self.refuse_inference: bool = False
        self.warmup_completed: bool = False
        self._last_avail_check: float = 0.0
        self._cached_available: bool = False

    @property
    def model_hash_ok(self) -> bool:
        """Whether model hash verified successfully against manifest."""
        return self.hash_verified

    def get_expected_model_hash(self) -> str:
        """Return expected SHA-256 hash from models_manifest.json."""
        manifest_path = _project_root() / "models_manifest.json"
        if not manifest_path.exists():
            return "no_manifest"
        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            entry = data.get("models", {}).get(self.model_name)
            if not entry:
                for k, v in data.get("models", {}).items():
                    if k == self.model_name or k.startswith(self.model_name):
                        entry = v
                        break
            if entry and entry.get("sha256"):
                return entry["sha256"].lower().strip()
        except Exception:
            pass
        return "unknown_hash"

    def is_available(self) -> bool:
        """Ping local Ollama server to check availability with 5-second TTL."""
        if self.refuse_inference:
            return False
        import time
        now = time.monotonic()
        if now - self._last_avail_check < 5.0:
            return self._cached_available

        self._last_avail_check = now
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                headers={"User-Agent": "AnviTrace-Forensic/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                self._cached_available = (resp.status == 200)
                return self._cached_available
        except Exception:
            self._cached_available = False
            return False

    def get_model_info(self) -> dict[str, Any] | None:
        """Retrieve model metadata or digest from Ollama."""
        # 1. Try /api/show
        try:
            payload = json.dumps({"name": self.model_name}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/show",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "AnviTrace-Forensic/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    info = json.loads(resp.read().decode("utf-8"))
                    if info.get("digest"):
                        return info
        except Exception:
            pass

        # 2. Try /api/tags for model digest
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                headers={"User-Agent": "AnviTrace-Forensic/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    tags_data = json.loads(resp.read().decode("utf-8"))
                    for m in tags_data.get("models", []):
                        m_name = m.get("name", "")
                        if m_name == self.model_name or m_name.startswith(f"{self.model_name}:"):
                            return m
        except Exception:
            pass
        return None

    def verify_model_integrity(self) -> tuple[bool, str]:
        """Verify model against models_manifest.json using constant-time hmac.compare_digest.

        Returns: (is_ok: bool, reason: str)
        """
        manifest_path = _project_root() / "models_manifest.json"
        if not manifest_path.exists():
            self.hash_verified = True
            return True, "no_manifest"

        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest_data = json.load(fh)
        except Exception as exc:
            logger.error(f"[explainer] Could not parse models_manifest.json: {exc}")
            return True, "parse_error"

        models = manifest_data.get("models", {})
        # If manifest specifies entries, find matching entry for model_name or filename
        entry = models.get(self.model_name)
        if not entry:
            # Check by basename or tag
            for k, v in models.items():
                if k == self.model_name or k.startswith(self.model_name):
                    entry = v
                    break

        if not entry:
            self.hash_verified = True
            return True, "model_not_in_manifest"

        expected_hash = (entry.get("sha256") or "").lower().strip()
        if not expected_hash:
            self.hash_verified = True
            return True, "no_expected_hash"

        # Locate the actual model blob file on disk (Ollama blobs folder or MODEL_PATH)
        blob_path: Path | None = None
        local_model_path = os.environ.get("MODEL_PATH")
        if local_model_path and Path(local_model_path).exists():
            blob_path = Path(local_model_path)
        else:
            # Check Ollama blobs directory
            ollama_home = os.environ.get("OLLAMA_MODELS")
            candidate_dirs: list[Path] = []
            if ollama_home:
                candidate_dirs.append(Path(ollama_home) / "blobs")
            user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
            if user_profile:
                candidate_dirs.append(Path(user_profile) / ".ollama" / "models" / "blobs")
                candidate_dirs.append(Path(user_profile) / "AppData" / "Local" / "Ollama" / "models" / "blobs")
            candidate_dirs.append(Path(r"C:\Program Files\Ollama\models\blobs"))

            # Look for exact blob file named sha256-<expected_hash>
            for d in candidate_dirs:
                if d.exists() and d.is_dir():
                    candidate_file = d / f"sha256-{expected_hash}"
                    if candidate_file.exists():
                        blob_path = candidate_file
                        break
                    # If entry has a filename, also check it
                    fname = entry.get("filename")
                    if fname and (d / fname).exists():
                        blob_path = d / fname
                        break

            # If not located by exact expected hash, find blob referenced in Ollama manifest
            manifest_dirs: list[Path] = []
            if ollama_home:
                manifest_dirs.append(Path(ollama_home) / "manifests-v2")
                manifest_dirs.append(Path(ollama_home) / "manifests")
            if user_profile:
                manifest_dirs.append(Path(user_profile) / ".ollama" / "models" / "manifests-v2")
                manifest_dirs.append(Path(user_profile) / ".ollama" / "models" / "manifests")

            for m_dir in manifest_dirs:
                if m_dir.exists():
                    # Search for manifest matching model_name
                    for mf in m_dir.rglob("*"):
                        if mf.is_file() and not mf.name.endswith(".json"):
                            try:
                                with mf.open("r", encoding="utf-8") as f:
                                    m_json = json.load(f)
                                    for layer in m_json.get("layers", []):
                                        if layer.get("mediaType") == "application/vnd.ollama.image.model":
                                            layer_digest = (layer.get("digest") or "").replace("sha256:", "")
                                            for d in candidate_dirs:
                                                candidate_file = d / f"sha256-{layer_digest}"
                                                if candidate_file.exists():
                                                    blob_path = candidate_file
                                                    break
                            except Exception:
                                pass
                        if blob_path:
                            break
                if blob_path:
                    break

        if not blob_path or not blob_path.exists():
            logger.warning(
                f"[explainer] Could not locate model blob file on disk for '{self.model_name}' (expected sha256-{expected_hash[:16]}...)"
            )
            # Refuse model loading when disk blob cannot be found
            self.hash_verified = False
            self.refuse_inference = True
            log_event(
                "MODEL_HASH_MISMATCH",
                model_name=self.model_name,
                reason="BLOB_NOT_FOUND_ON_DISK",
            )
            return False, "BLOB_NOT_FOUND_ON_DISK"

        logger.info(f"[explainer] Streaming model blob from disk for hash verification: {blob_path}")
        h = hashlib.sha256()
        try:
            with blob_path.open("rb") as f:
                while chunk := f.read(8 * 1024 * 1024):
                    h.update(chunk)
            actual_hash = h.hexdigest().lower()
        except Exception as exc:
            logger.error(f"[explainer] Error streaming model blob {blob_path}: {exc}")
            self.hash_verified = False
            self.refuse_inference = True
            log_event("MODEL_HASH_MISMATCH", model_name=self.model_name, reason=str(exc))
            return False, f"READ_ERROR: {exc}"

        # Constant-time comparison using hmac.compare_digest
        if not hmac.compare_digest(actual_hash, expected_hash):
            logger.error(
                f"[explainer] MODEL_HASH_MISMATCH for '{self.model_name}': "
                f"computed={actual_hash[:16]}... expected={expected_hash[:16]}..."
            )
            log_event(
                "MODEL_HASH_MISMATCH",
                model_name=self.model_name,
                computed_prefix=actual_hash[:16],
                expected_prefix=expected_hash[:16],
            )
            self.hash_verified = False
            self.refuse_inference = True
            return False, "MODEL_HASH_MISMATCH"

        log_event("MODEL_VERIFIED", model_name=self.model_name)
        self.hash_verified = True
        self.refuse_inference = False
        return True, "VERIFIED"

    def warmup(self) -> bool:
        """Send tiny request at startup to eliminate cold-start inference latency."""
        if self.refuse_inference:
            return False
        try:
            logger.info(f"[explainer] Warming up model '{self.model_name}' on localhost Ollama...")
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "options": {"num_predict": 1},
            }
            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "AnviTrace-Forensic/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 200:
                    self.warmup_completed = True
                    logger.info(f"[explainer] Model '{self.model_name}' warm-up successful. Ready for requests.")
                    return True
        except Exception as exc:
            logger.info(f"[explainer] Warm-up skipped or model not ready ({exc}). Using deterministic fallback.")
        self.warmup_completed = False
        return False

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
    ) -> str | None:
        """Call Ollama POST /api/chat with stream=false."""
        if self.refuse_inference:
            logger.warning("[explainer] Inference refused due to model integrity failure. Returning template.")
            return None

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 512,
                "num_predict": 96,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AnviTrace-Forensic/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    body = json.loads(response.read().decode("utf-8"))
                    msg = body.get("message", {}).get("content", "")
                    return msg.strip()
                logger.warning(f"[Ollama] Non-200 status code: {response.status}")
                return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.info(f"[Ollama] Local server unreachable or timed out ({e}). Falling back to template.")
            return None
        except Exception as e:
            logger.warning(f"[Ollama] Inference call failed: {e}. Falling back to template.")
            return None

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
    ):
        """Call Ollama POST /api/chat with stream=true yielding text tokens."""
        if self.refuse_inference:
            return

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": 512,
                "num_predict": 96,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AnviTrace-Forensic/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status == 200:
                    for line in response:
                        line_str = line.decode("utf-8").strip()
                        if line_str:
                            try:
                                chunk = json.loads(line_str)
                                content = chunk.get("message", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            logger.warning(f"[Ollama] Stream error: {e}")
            return
