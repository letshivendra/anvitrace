"""Model manifest loader and startup integrity verifier.

Flow:
    1. Read models_manifest.json from project root.
    2. Resolve MODEL_PATH from env/settings.
    3. Stream-hash the model file in 8 MB chunks with hashlib.sha256.
    4. Compare with hmac.compare_digest (constant-time) against manifest entry.
    5. On mismatch: log MODEL_HASH_MISMATCH, refuse to load, return NullBackend.
    6. On match: return LlamaCppBackend.
    7. If MODEL_PATH is unset or file absent: return NullBackend silently.

No network calls are made here or from any model backend.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from app.explainer.model_backend import LlamaCppBackend, ModelBackend, NullBackend
from app.utils.logging import log_event

if TYPE_CHECKING:
    pass

logger = logging.getLogger("anvitrace")

# Chunk size for streaming model hash (8 MB as spec mandates)
_HASH_CHUNK_BYTES = 8 * 1024 * 1024

# Singleton backend — resolved once at startup
_backend: ModelBackend | None = None


def _project_root() -> Path:
    """Return backend project root (parent of this package's parent directory)."""
    return Path(__file__).resolve().parent.parent.parent


def _load_manifest() -> dict:
    """Load and parse models_manifest.json from the project root."""
    manifest_path = _project_root() / "models_manifest.json"
    if not manifest_path.exists():
        logger.warning("[explainer] models_manifest.json not found — running with NullBackend.")
        return {"models": {}}
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error(f"[explainer] Failed to parse models_manifest.json: {exc}")
        return {"models": {}}


def _stream_sha256(path: Path) -> str:
    """Compute SHA-256 of a file using 8 MB streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_backend() -> ModelBackend:
    """Resolve and return the model backend singleton.

    Called once at application startup from main.py lifespan.
    Subsequent calls return the cached singleton.
    """
    global _backend
    if _backend is not None:
        return _backend

    model_path_env = os.environ.get("MODEL_PATH", "").strip()
    if not model_path_env:
        logger.info("[explainer] MODEL_PATH not set — using NullBackend (template mode).")
        _backend = NullBackend()
        return _backend

    model_path = Path(model_path_env)

    # Refuse pickle files immediately
    if model_path.suffix.lower() in {".pkl", ".pickle"}:
        logger.error(f"[explainer] Refused to load pickle file '{model_path}'. Use GGUF only.")
        log_event("MODEL_LOAD_REFUSED", reason="pickle_file", path="<redacted>")
        _backend = NullBackend()
        return _backend

    if not model_path.exists():
        logger.warning(f"[explainer] Model file not found at configured path — using NullBackend.")
        _backend = NullBackend()
        return _backend

    filename = model_path.name
    manifest = _load_manifest()
    models_dict: dict = manifest.get("models", {})

    if filename not in models_dict:
        logger.warning(
            f"[explainer] Model '{filename}' not in manifest — cannot verify. Using NullBackend."
        )
        log_event(
            "MODEL_HASH_MISMATCH",
            reason="not_in_manifest",
            filename=filename,
        )
        _backend = NullBackend()
        return _backend

    entry = models_dict[filename]
    expected_hash: str = entry.get("sha256", "").lower().strip()

    if not expected_hash:
        logger.warning(f"[explainer] Manifest entry for '{filename}' has no sha256 — using NullBackend.")
        log_event("MODEL_HASH_MISMATCH", reason="no_hash_in_manifest", filename=filename)
        _backend = NullBackend()
        return _backend

    logger.info(f"[explainer] Verifying model integrity: {filename} ...")
    try:
        actual_hash = _stream_sha256(model_path)
    except Exception as exc:
        logger.error(f"[explainer] Could not hash model file: {exc}")
        log_event("MODEL_HASH_MISMATCH", reason="hash_error", filename=filename)
        _backend = NullBackend()
        return _backend

    # Constant-time comparison
    if not hmac.compare_digest(actual_hash, expected_hash):
        logger.error(
            f"[explainer] MODEL_HASH_MISMATCH for '{filename}': "
            f"computed={actual_hash[:16]}... expected={expected_hash[:16]}..."
        )
        log_event(
            "MODEL_HASH_MISMATCH",
            filename=filename,
            computed_prefix=actual_hash[:16],
            expected_prefix=expected_hash[:16],
        )
        _backend = NullBackend()
        return _backend

    logger.info(f"[explainer] Model integrity verified: {filename}")
    log_event(
        "MODEL_LOADED",
        filename=filename,
        sha256_prefix=actual_hash[:16],
        version=entry.get("version", "unknown"),
    )

    context_size = int(os.environ.get("CONTEXT_SIZE", "2048"))
    temperature = float(os.environ.get("TEMPERATURE", "0.1"))

    _backend = LlamaCppBackend(
        model_path=str(model_path),
        context_size=context_size,
        temperature=temperature,
        verified_sha256=actual_hash,
        manifest_version=entry.get("version", "unknown"),
        manifest_name=filename,
    )
    return _backend


def get_backend() -> ModelBackend:
    """Return the already-resolved backend singleton.  Call resolve_backend() at startup first."""
    global _backend
    if _backend is None:
        return resolve_backend()
    return _backend


def reset_backend_for_testing(backend: ModelBackend | None = None) -> None:
    """Override the singleton — for tests only."""
    global _backend
    _backend = backend
