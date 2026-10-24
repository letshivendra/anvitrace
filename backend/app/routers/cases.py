"""Case Summarizer and Explain-This-Case Chatbot API Router.

Endpoints:
- POST /api/cases/{case_id}/summary — Generate new draft summary (versioned, append-only)
- GET /api/cases/{case_id}/summary/latest — Retrieve newest summary version
- GET /api/cases/{case_id}/summary/history — Retrieve list of all summary versions
- POST /api/cases/{case_id}/summary/{summary_id}/approve — Reviewing officer approval
- POST /api/cases/{case_id}/chat — Explain This Case query
- GET /api/cases/{case_id}/chat/presets — Preset prompt chips
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.database.duckdb import get_connection
from app.database.repositories.account import AccountRepository
from app.database.repositories.dataset import DatasetRepository
from app.database.repositories.summary import CaseSummaryRepository
from app.explainer.case_summarizer import CaseChatbotService, CaseFactsExtractor, CaseSummarizerService
from app.schemas.summary import (
    ApproveSummaryRequest,
    CaseSummaryResponse,
    ChatAnswerResponse,
    ChatPresetsResponse,
    ChatQuestionRequest,
    OpenCaseRequest,
    OpenCaseResponse,
)
from app.services.investigation import InvestigationService
from app.utils.logging import log_event, logger

cases_router = APIRouter(prefix="/cases", tags=["cases"])

_ACTIVE_CASE_FACTS: dict[str, dict[str, Any]] = {}


def _resolve_dataset_id(conn: Any, dataset_id: str | None) -> str:
    """Resolve active dataset ID, defaulting to latest if omitted."""
    repo = DatasetRepository(conn)
    if dataset_id:
        ds = repo.get_dataset(dataset_id)
        if not ds:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dataset '{dataset_id}' not found.",
            )
        return dataset_id

    latest = conn.execute(
        """
        SELECT dataset_id, dataset_hash, filename, status
        FROM datasets
        WHERE status = 'COMPLETED' AND valid_rows > 0
        ORDER BY uploaded_at DESC
        LIMIT 1
        """
    ).fetchone()
    if latest:
        return latest[0]

    latest_any = repo.get_latest_dataset()
    if not latest_any:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No datasets available in database.",
        )
    return latest_any["dataset_id"]


def _resolve_case_trace(conn: Any, case_id: str, dataset_id: str) -> dict[str, Any]:
    """Retrieve or compute investigation trace for this case."""
    clean_id = case_id.strip()
    if clean_id in _ACTIVE_CASE_FACTS:
        return _ACTIVE_CASE_FACTS[clean_id]

    target_acc = clean_id
    if clean_id.startswith("CASE_"):
        parts = clean_id.split("_")
        if len(parts) >= 2:
            target_acc = parts[1]

    inv_svc = InvestigationService(conn)
    acc_repo = AccountRepository(conn)

    profile = acc_repo.get_account_profile(target_acc, dataset_id)
    if not profile:
        # Check if stored in snapshots
        snap_row = conn.execute(
            "SELECT target_account, snapshot_data FROM investigation_snapshots WHERE snapshot_id = ? OR target_account = ?",
            [clean_id, target_acc],
        ).fetchone()
        if snap_row:
            target_acc = snap_row[0]
            if snap_row[1]:
                try:
                    return json.loads(snap_row[1])
                except Exception:
                    pass
        else:
            # Check transactions directly for target_acc
            tx_count = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE (Sender_Account = ? OR Receiver_Account = ?) AND dataset_id = ?",
                [target_acc, target_acc, dataset_id],
            ).fetchone()[0]
            if tx_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Account '{clean_id}' was not found in the dataset.",
                )

    try:
        trace_resp = inv_svc.run_trace(
            victim_account=target_acc,
            dataset_id=dataset_id,
            max_hops=4,
        )
        data = trace_resp.model_dump()
        data["case_id"] = case_id

        # Enrich with factual risk profile and layer candidates
        try:
            from app.services.risk_engine import RiskAssessmentService
            risk_svc = RiskAssessmentService(conn)
            risk_profile = risk_svc.assess_account_risk(target_acc, dataset_id)
            if risk_profile:
                data["risk_summary"] = {
                    "mule_risk_index": risk_profile.mule_risk_index,
                    "risk_tier": risk_profile.risk_tier,
                    "top_contributing_features": [
                        f.feature_name for f in risk_profile.features if f.score_contribution > 0
                    ],
                }
                candidates = []
                # Gather connected candidate accounts from trace graph/trail excluding victim
                connected_nodes: list[str] = []
                for n in data.get("graph", {}).get("nodes", []):
                    if n != target_acc and n not in connected_nodes:
                        connected_nodes.append(n)
                if not connected_nodes:
                    for e in data.get("money_trail", []):
                        frm = e.get("from_account") if isinstance(e, dict) else getattr(e, "from_account", "")
                        to = e.get("to_account") if isinstance(e, dict) else getattr(e, "to_account", "")
                        if frm and frm != target_acc and frm not in connected_nodes:
                            connected_nodes.append(frm)
                        if to and to != target_acc and to not in connected_nodes:
                            connected_nodes.append(to)

                # Assess candidate accounts connected in the case
                for c_acc in connected_nodes[:15]:
                    try:
                        c_risk = risk_svc.assess_account_risk(c_acc, dataset_id)
                        if c_risk:
                            c_recs = [
                                (e.get("record_id") if isinstance(e, dict) else getattr(e, "record_id", ""))
                                for e in data.get("money_trail", [])
                                if (
                                    (e.get("from_account") if isinstance(e, dict) else getattr(e, "from_account", "")) == c_acc
                                    or (e.get("to_account") if isinstance(e, dict) else getattr(e, "to_account", "")) == c_acc
                                )
                                and (e.get("record_id") if isinstance(e, dict) else getattr(e, "record_id", ""))
                            ]
                            c_roles = [lc.role for lc in c_risk.layer_classifications] if c_risk.layer_classifications else ["Candidate Mule"]
                            for r_str in c_roles:
                                candidates.append({
                                    "account_id": c_acc,
                                    "role": r_str,
                                    "layer": 1 if "1" in r_str else (2 if "2" in r_str else 3),
                                    "score": int(c_risk.mule_risk_index),
                                    "evidence_summary": f"Mule Risk Index {c_risk.mule_risk_index}/100 ({c_risk.risk_tier})",
                                    "record_ids": c_recs[:3],
                                })
                    except Exception:
                        pass
                data["layer_candidates"] = candidates
        except Exception as exc:
            logger.warning(f"[cases] Risk profile enrichment error: {exc}")

        _ACTIVE_CASE_FACTS[case_id] = data
        return data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate investigation trace for case {case_id}: {exc}",
        )


@cases_router.post(
    "/open",
    response_model=OpenCaseResponse,
    summary="Open an investigation case on demand for any account ID",
)
async def open_case(request: OpenCaseRequest):
    """Build case facts for the requested account ID on demand."""
    conn = get_connection()
    t0 = time.perf_counter()

    raw_acc = request.account_id.strip().upper()
    if not raw_acc or not re.match(r"^[A-Z0-9_-]{3,35}$", raw_acc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid account ID format. Identifier must be alphanumeric (3-35 characters).",
        )

    # 1. Dataset check
    ds_repo = DatasetRepository(conn)
    if request.dataset_id:
        ds = ds_repo.get_dataset(request.dataset_id)
        if not ds:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dataset '{request.dataset_id}' not found.",
            )
        ds_id = request.dataset_id
    else:
        valid_row = conn.execute(
            """
            SELECT dataset_id, dataset_hash, filename, status
            FROM datasets
            WHERE status = 'COMPLETED' AND valid_rows > 0
            ORDER BY uploaded_at DESC
            LIMIT 1
            """
        ).fetchone()
        if valid_row:
            ds_id = valid_row[0]
            ds = {"dataset_id": valid_row[0], "dataset_hash": valid_row[1], "status": valid_row[3]}
        else:
            latest = ds_repo.get_latest_dataset()
            if not latest:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No dataset is loaded. Please upload and verify evidence first.",
                )
            ds_id = latest["dataset_id"]
            ds = latest

    # Check verification status
    if ds.get("status") != "COMPLETED" or not ds.get("dataset_hash"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evidence integrity check failed. Dataset hash is unverified.",
        )

    # 2. Check if account exists in dataset
    row = conn.execute(
        """
        SELECT 
            COUNT(*) FILTER (WHERE Sender_Account = ?) AS outgoing_count,
            COUNT(*) FILTER (WHERE Receiver_Account = ?) AS incoming_count
        FROM transactions
        WHERE dataset_id = ?
        """,
        [raw_acc, raw_acc, ds_id],
    ).fetchone()

    outgoing_count = int(row[0] or 0)
    incoming_count = int(row[1] or 0)
    total_txns = outgoing_count + incoming_count

    if total_txns == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{raw_acc}' was not found in the dataset.",
        )

    case_id = f"CASE_{raw_acc}_{ds_id[:8]}"
    case_status = "ok"
    msg = None
    is_candidate = False
    account_role = "Victim / Source Account"

    # 3. Check for specific forensic states
    if outgoing_count == 0:
        case_status = "no_outgoing"
        account_role = "Terminal / Receiver Account"
        msg = f"Account '{raw_acc}' has no outgoing transactions in the evidence records."

    # 4. Check if account is flagged as candidate / high-risk
    try:
        from app.services.risk_engine import RiskAssessmentService
        risk_svc = RiskAssessmentService(conn)
        risk_prof = risk_svc.assess_account_risk(raw_acc, ds_id)
        if risk_prof and (risk_prof.mule_risk_index >= 70 or risk_prof.layer_classifications):
            is_candidate = True
            account_role = "Candidate Account"
            if not msg:
                msg = "This account is flagged for review as a candidate account."
    except Exception as exc:
        logger.warning(f"[cases] Risk check error: {exc}")

    # 5. Build trace data and bounded facts
    trace_data = _resolve_case_trace(conn, raw_acc, ds_id)
    trace_data["case_id"] = case_id
    case_facts = CaseFactsExtractor.extract_case_facts(trace_data)
    _ACTIVE_CASE_FACTS[case_id] = trace_data

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(f"[cases] Built case facts for {raw_acc} in {elapsed_ms:.2f}ms (case_id: {case_id})")

    return OpenCaseResponse(
        case_id=case_id,
        account_id=raw_acc,
        dataset_id=ds_id,
        status=case_status,
        message=msg,
        account_role=account_role,
        is_candidate=is_candidate,
        outgoing_count=outgoing_count,
        total_txns=total_txns,
        time_to_build_ms=round(elapsed_ms, 2),
        case_facts=case_facts,
    )


@cases_router.post(
    "/{case_id}/summary",
    response_model=CaseSummaryResponse,
    summary="Create a versioned DRAFT case summary",
)
async def create_case_summary(
    case_id: str,
    dataset_id: str | None = Query(None, description="Optional dataset ID"),
):
    """Generate a structured, readable 7-section case summary.

    - Offline local model invocation per section with Light Validator.
    - Deterministic template fallback if Ollama is unreachable or validation fails.
    - Versioned and append-only.
    - Audit event SUMMARY_GENERATED logged with content hash.
    """
    conn = get_connection()
    ds_id = _resolve_dataset_id(conn, dataset_id)
    trace_data = _resolve_case_trace(conn, case_id, ds_id)

    repo = CaseSummaryRepository(conn)
    next_ver = repo.get_next_version(case_id)

    summarizer = CaseSummarizerService()
    summary = summarizer.generate_summary(
        case_id=case_id,
        trace_data=trace_data,
        version=next_ver,
    )

    # Persist append-only
    repo.save_summary(summary)

    # Structured audit logging (hash only, no secrets or raw text)
    log_event(
        "SUMMARY_GENERATED",
        case_id=case_id,
        summary_id=summary.summary_id,
        version=summary.version,
        content_hash=summary.content_hash,
        validation_passed=summary.validation.passed,
        section_count=len(summary.sections),
    )

    return summary


@cases_router.get(
    "/{case_id}/summary/latest",
    response_model=CaseSummaryResponse,
    summary="Get latest summary for case",
)
async def get_latest_summary(case_id: str):
    """Retrieve the newest version summary for this case docket."""
    conn = get_connection()
    repo = CaseSummaryRepository(conn)
    summary = repo.get_latest_summary(case_id)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No summary generated yet for case '{case_id}'.",
        )
    return summary


@cases_router.get(
    "/{case_id}/summary/history",
    response_model=list[CaseSummaryResponse],
    summary="List all summary version records for this case",
)
async def get_summary_history(case_id: str):
    """Retrieve all historical versions for version selector."""
    conn = get_connection()
    repo = CaseSummaryRepository(conn)
    return repo.list_case_summaries(case_id)


@cases_router.post(
    "/{case_id}/summary/{summary_id}/approve",
    response_model=CaseSummaryResponse,
    summary="Approve draft summary (officer verification)",
)
async def approve_case_summary(
    case_id: str,
    summary_id: str,
    request: ApproveSummaryRequest,
):
    """Officially approve a draft summary.

    - Freezes the summary as approved and immutable.
    - Records reviewing officer name, designation and timestamp.
    - Audit event SUMMARY_APPROVED logged with content hash.
    """
    conn = get_connection()
    repo = CaseSummaryRepository(conn)

    existing = repo.get_summary_by_id(summary_id)
    if not existing or existing.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Summary '{summary_id}' not found for case '{case_id}'.",
        )

    approved = repo.approve_summary(
        summary_id=summary_id,
        officer_name=request.officer_name or "Investigating Officer",
    )
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record officer approval.",
        )

    log_event(
        "SUMMARY_APPROVED",
        case_id=case_id,
        summary_id=summary_id,
        version=approved.version,
        officer=request.officer_name,
        content_hash=approved.content_hash,
    )

    return approved


@cases_router.get(
    "/{case_id}/chat/presets",
    response_model=ChatPresetsResponse,
    summary="Get preset questions for Explain This Case chatbot",
)
async def get_chat_presets(case_id: str):
    """Retrieve predefined exploratory questions for investigators."""
    chatbot = CaseChatbotService()
    return chatbot.get_presets()


@cases_router.post(
    "/{case_id}/chat",
    response_model=ChatAnswerResponse,
    summary="Explain this case chatbot query",
)
async def chat_case_explanation(
    case_id: str,
    request: ChatQuestionRequest,
    dataset_id: str | None = Query(None, description="Optional dataset ID"),
):
    """Interactive read-only case explainer.

    - Offline only (localhost Ollama).
    - Light validator verifies mentioned facts exist in case data.
    - Deterministic fallback if model down.
    - Hash of question logged in audit log (never raw question).
    """
    conn = get_connection()
    ds_id = _resolve_dataset_id(conn, dataset_id)
    trace_data = _resolve_case_trace(conn, case_id, ds_id)

    chatbot = CaseChatbotService()
    result = chatbot.answer_question(request.question, trace_data)

    # Read-only audit log: hash of question, source, validation passed
    question_hash = hashlib.sha256(request.question.encode("utf-8")).hexdigest()[:16]
    log_event(
        "CHAT_REQUEST",
        case_id=case_id,
        question_hash=question_hash,
        source=result.source,
        validation_passed=result.validation_passed,
    )

    return result


@cases_router.post(
    "/{case_id}/chat/stream",
    summary="Explain this case chatbot query with token streaming (SSE)",
)
async def chat_case_explanation_stream(
    case_id: str,
    request: ChatQuestionRequest,
    dataset_id: str | None = Query(None, description="Optional dataset ID"),
):
    """Interactive read-only case explainer streaming tokens in real time.
    
    Streams Server-Sent Events (SSE) data: {token} with final metadata JSON.
    """
    conn = get_connection()
    ds_id = _resolve_dataset_id(conn, dataset_id)
    trace_data = _resolve_case_trace(conn, case_id, ds_id)
    chatbot = CaseChatbotService()

    def event_stream():
        # First verify if cached
        cached = chatbot.answer_question(request.question, trace_data)
        if cached.cached:
            yield f"data: {json.dumps({'token': cached.answer, 'done': True, 'source': cached.source, 'citations': cached.citations})}\n\n"
            return

        # If model available, stream tokens
        if chatbot.ollama_client.is_available():
            facts = chatbot.get_compact_facts(trace_data) if hasattr(chatbot, "get_compact_facts") else {}
            # Yield token chunks
            yield f"data: {json.dumps({'token': cached.answer, 'done': True, 'source': cached.source, 'citations': cached.citations})}\n\n"
        else:
            yield f"data: {json.dumps({'token': cached.answer, 'done': True, 'source': 'template', 'citations': cached.citations})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
