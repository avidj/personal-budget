# SPDX-License-Identifier: Apache-2.0
"""HTTP API for orchestrators (n8n). Returns counts and identifiers only, never rows."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel

from pipeline.config import Settings, load_sources
from pipeline.connectors import build_registry
from pipeline.formats import FormatError
from pipeline.masking import configure_logging
from pipeline.service import PipelineError, PipelineService
from pipeline.writer.actualpy_writer import ActualpyWriter

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_service() -> PipelineService:
    settings = get_settings()
    sources = load_sources(settings.sources_file)
    return PipelineService(
        settings, sources, build_registry(settings.inbox_dir), ActualpyWriter(settings)
    )


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().pipeline_api_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "PIPELINE_API_TOKEN is not configured"
        )
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


Auth = Depends(require_token)


class ImportOptions(BaseModel):
    dry_run: bool = False


class ApplyRulesOptions(BaseModel):
    uncategorized_only: bool = True


class ProposalsIn(BaseModel):
    proposals: list[dict[str, Any]]
    model: str | None = None


class ApproveIn(BaseModel):
    ids: list[str] = []
    min_confidence: float | None = None
    apply: bool = True


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(title="personal-budget pipeline", version="0.1.0", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    def _abandon_stale_runs() -> None:
        from pipeline import db
        from pipeline.ledger import Ledger

        with db.connect(get_settings().database_url) as conn:
            n = Ledger(conn).abandon_stale_runs()
            conn.commit()
        if n:
            log.info("marked %d stale run(s) as abandoned", n)

    @app.exception_handler(PipelineError)
    async def _pipeline_error(_, exc: PipelineError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(FormatError)
    async def _format_error(_, exc: FormatError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    @app.exception_handler(KeyError)
    async def _key_error(_, exc: KeyError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(LookupError)
    async def _lookup_error(_, exc: LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return get_service().health()

    @app.post("/sources/{source_id}/fetch", dependencies=[Auth])
    def fetch(source_id: str) -> dict[str, Any]:
        return get_service().fetch(source_id, trigger="n8n")

    @app.post("/batches/{batch_id}/dedup", dependencies=[Auth])
    def dedup(batch_id: str) -> dict[str, Any]:
        return get_service().dedup(batch_id)

    @app.post("/batches/{batch_id}/import", dependencies=[Auth])
    def import_batch(batch_id: str, options: ImportOptions | None = None) -> dict[str, Any]:
        return get_service().import_batch(batch_id, dry_run=bool(options and options.dry_run))

    @app.post("/sources/{source_id}/opening-balance", dependencies=[Auth])
    def opening_balance(source_id: str, options: ImportOptions | None = None) -> dict[str, Any]:
        return get_service().opening_balance(source_id, dry_run=bool(options and options.dry_run))

    @app.post("/sources/{source_id}/run", dependencies=[Auth])
    def run(source_id: str, dry_run: Annotated[bool, Query()] = False) -> dict[str, Any]:
        return get_service().run(source_id, dry_run=dry_run, trigger="api")

    @app.post("/rules/apply", dependencies=[Auth])
    def rules_apply(options: ApplyRulesOptions | None = None) -> dict[str, Any]:
        return get_service().apply_rules(
            uncategorized_only=options.uncategorized_only if options else True
        )

    @app.get("/categorization/candidates", dependencies=[Auth])
    def categorization_candidates() -> dict[str, Any]:
        return get_service().candidates()

    @app.post("/categorization/proposals", dependencies=[Auth])
    def categorization_store(body: ProposalsIn) -> dict[str, Any]:
        return get_service().store_proposals({"proposals": body.proposals}, model=body.model)

    @app.post("/categorization/propose", dependencies=[Auth])
    def categorization_propose() -> dict[str, Any]:
        return get_service().propose()

    @app.get("/categorization/proposals", dependencies=[Auth])
    def categorization_list(
        status: Annotated[str | None, Query()] = "pending",
    ) -> list[dict[str, Any]]:
        return get_service().proposals(status)

    @app.post("/categorization/approve", dependencies=[Auth])
    def categorization_approve(body: ApproveIn) -> dict[str, Any]:
        return get_service().approve(body.ids, min_confidence=body.min_confidence, apply=body.apply)

    @app.post("/categorization/reject", dependencies=[Auth])
    def categorization_reject(body: ApproveIn) -> dict[str, Any]:
        return get_service().reject(body.ids)

    @app.get("/summary", dependencies=[Auth])
    def summary() -> dict[str, Any]:
        return get_service().summary()

    return app


app = create_app()
