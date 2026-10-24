"""Pydantic schemas for Case Summarizer and Case Explainer Chatbot."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class OpenCaseRequest(BaseModel):
    """Request payload to open an investigation case for any account."""

    account_id: str = Field(..., min_length=1, max_length=50, description="Account identifier to investigate")
    dataset_id: str | None = Field(default=None, description="Optional dataset ID")


class OpenCaseResponse(BaseModel):
    """Response payload when opening a case for an account."""

    case_id: str
    account_id: str
    dataset_id: str
    status: str = Field(default="ok", description="Status of opened case: ok, flagged_for_review, no_outgoing")
    message: str | None = None
    account_role: str | None = None
    is_candidate: bool = False
    outgoing_count: int = 0
    total_txns: int = 0
    time_to_build_ms: float = 0.0
    case_facts: dict[str, Any] | None = None


class SummarySection(BaseModel):
    """A structured section within a case summary."""

    title: str = Field(..., description="Title of the section")
    text: str = Field(..., description="Narrative body with bracketed citations like [TXN123]")
    citations: list[str] = Field(default_factory=list, description="List of record_ids cited in this section")
    source: str = Field(
        default="Verified facts (computed)",
        description="Whether this section was verified computed facts or phrased by local model",
    )


class ValidationResult(BaseModel):
    """Outcome of light validator verifying facts against the case data."""

    passed: bool = Field(..., description="True if all sections passed validation")
    issues: list[str] = Field(default_factory=list, description="List of validation failure descriptions, if any")


class ModelInfo(BaseModel):
    """Metadata of the model used for audit trail."""

    name: str = Field(default="ollama/llama3.2:3b")
    version: str = Field(default="unknown")
    hash: str | None = Field(default=None, description="Model SHA-256 hash if available")


class CaseSummaryResponse(BaseModel):
    """Versioned Case Summary output contract."""

    model_config = {"protected_namespaces": ()}

    summary_id: str
    case_id: str
    dataset_id: str
    version: int = Field(default=1, description="Sequential version number (1, 2, 3...)")
    status: Literal["draft", "approved"] = Field(default="draft")
    sections: list[SummarySection] = Field(default_factory=list)
    validation: ValidationResult
    model_info: ModelInfo
    content_hash: str = Field("", description="SHA-256 hash of sections content")
    created_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None


class ApproveSummaryRequest(BaseModel):
    """Request payload to officially approve a draft summary."""

    officer_name: str = Field(default="Investigating Officer", description="Name of the reviewing officer")
    officer_designation: str | None = Field(default="Cyber Crime Cell", description="Designation / Police Station")
    notes: str | None = Field(default=None, description="Optional officer approval notes")


class ChatQuestionRequest(BaseModel):
    """Request payload for Explain This Case chatbot."""

    question: str = Field(..., min_length=1, max_length=1000, description="Natural language question about the case")


class ChatAnswerResponse(BaseModel):
    """Response payload for Explain This Case chatbot."""

    answer: str = Field(..., description="Chatbot answer with bracketed citations")
    citations: list[str] = Field(default_factory=list, description="Record IDs cited in answer")
    source: str = Field(default="template", description="Source badge: Verified facts (computed), Phrased by local model, or Cached")
    validation_passed: bool = Field(default=True)
    cached: bool = Field(default=False, description="True if response was served from DEMO_MODE cache")


class ChatPresetsResponse(BaseModel):
    """Preset standard questions for officer exploration."""

    presets: list[str] = Field(default_factory=list)
