"""Repository for versioned, append-only Case Summaries in DuckDB."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from app.schemas.summary import (
    CaseSummaryResponse,
    ModelInfo,
    SummarySection,
    ValidationResult,
)


class CaseSummaryRepository:
    """Manages versioned, append-only case summary records."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def get_next_version(self, case_id: str) -> int:
        """Return the next sequential version number for this case (1-based)."""
        res = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM case_summaries WHERE case_id = ?",
            [case_id],
        ).fetchone()
        return int(res[0]) if res else 1

    def save_summary(self, summary: CaseSummaryResponse) -> None:
        """Insert a new summary version into the append-only table."""
        sections_json = json.dumps([s.model_dump() for s in summary.sections])
        validation_json = json.dumps(summary.validation.model_dump())
        model_info_json = json.dumps(summary.model_info.model_dump())

        self.conn.execute(
            """
            INSERT INTO case_summaries (
                summary_id, case_id, dataset_id, version, status,
                sections_json, validation_json, model_info_json,
                content_hash, created_at, approved_at, approved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                summary.summary_id,
                summary.case_id,
                summary.dataset_id,
                summary.version,
                summary.status,
                sections_json,
                validation_json,
                model_info_json,
                summary.content_hash,
                summary.created_at,
                summary.approved_at,
                summary.approved_by,
            ],
        )

    def get_summary_by_id(self, summary_id: str) -> CaseSummaryResponse | None:
        """Fetch a specific summary by its ID."""
        row = self.conn.execute(
            """
            SELECT summary_id, case_id, dataset_id, version, status,
                   sections_json, validation_json, model_info_json,
                   content_hash, created_at, approved_at, approved_by
            FROM case_summaries
            WHERE summary_id = ?
            """,
            [summary_id],
        ).fetchone()
        if not row:
            return None
        return self._row_to_model(row)

    def get_latest_summary(self, case_id: str) -> CaseSummaryResponse | None:
        """Fetch the newest version summary for a case."""
        row = self.conn.execute(
            """
            SELECT summary_id, case_id, dataset_id, version, status,
                   sections_json, validation_json, model_info_json,
                   content_hash, created_at, approved_at, approved_by
            FROM case_summaries
            WHERE case_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            [case_id],
        ).fetchone()
        if not row:
            return None
        return self._row_to_model(row)

    def get_latest_approved_summary(self, case_id: str) -> CaseSummaryResponse | None:
        """Fetch the latest officially approved summary for inclusion in legal export."""
        row = self.conn.execute(
            """
            SELECT summary_id, case_id, dataset_id, version, status,
                   sections_json, validation_json, model_info_json,
                   content_hash, created_at, approved_at, approved_by
            FROM case_summaries
            WHERE case_id = ? AND status = 'approved'
            ORDER BY version DESC
            LIMIT 1
            """,
            [case_id],
        ).fetchone()
        if not row:
            return None
        return self._row_to_model(row)

    def list_case_summaries(self, case_id: str) -> list[CaseSummaryResponse]:
        """List all version records for a case."""
        rows = self.conn.execute(
            """
            SELECT summary_id, case_id, dataset_id, version, status,
                   sections_json, validation_json, model_info_json,
                   content_hash, created_at, approved_at, approved_by
            FROM case_summaries
            WHERE case_id = ?
            ORDER BY version ASC
            """,
            [case_id],
        ).fetchall()
        return [self._row_to_model(r) for r in rows]

    def approve_summary(
        self,
        summary_id: str,
        officer_name: str,
    ) -> CaseSummaryResponse | None:
        """Mark a draft summary as approved. Immutable once approved."""
        existing = self.get_summary_by_id(summary_id)
        if not existing:
            return None
        if existing.status == "approved":
            # Already approved; cannot modify
            return existing

        now = datetime.now(UTC)
        self.conn.execute(
            """
            UPDATE case_summaries
            SET status = 'approved', approved_at = ?, approved_by = ?
            WHERE summary_id = ? AND status = 'draft'
            """,
            [now, officer_name, summary_id],
        )
        return self.get_summary_by_id(summary_id)

    def _row_to_model(self, row: tuple[Any, ...]) -> CaseSummaryResponse:
        """Convert DuckDB row tuple into CaseSummaryResponse."""
        (
            summary_id,
            case_id,
            dataset_id,
            version,
            status,
            sections_json,
            validation_json,
            model_info_json,
            content_hash,
            created_at,
            approved_at,
            approved_by,
        ) = row

        raw_sections = json.loads(sections_json) if isinstance(sections_json, str) else sections_json
        raw_val = json.loads(validation_json) if isinstance(validation_json, str) else validation_json
        raw_model = json.loads(model_info_json) if isinstance(model_info_json, str) else model_info_json

        sections = [SummarySection(**s) for s in raw_sections]
        val = ValidationResult(**raw_val)
        model_info = ModelInfo(**raw_model)

        return CaseSummaryResponse(
            summary_id=summary_id,
            case_id=case_id,
            dataset_id=dataset_id,
            version=version,
            status=status,
            sections=sections,
            validation=val,
            model_info=model_info,
            content_hash=content_hash,
            created_at=created_at,
            approved_at=approved_at,
            approved_by=approved_by,
        )
