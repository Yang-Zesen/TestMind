"""Authorized HTTP surface for the asynchronous knowledge workflow."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from testmind.core.config import get_config
from testmind.core.security import (
    AuthContext,
    get_auth_context,
    get_authorized_project_ids,
    require_knowledge_base_access,
    require_project_access,
)
from testmind.infrastructure.knowledge.document_parser import validate_upload
from testmind.infrastructure.knowledge.source_storage import SourceStorage
from testmind.infrastructure.postgres.knowledge_repository import (
    IdempotencyConflict,
    InvalidVersionTransition,
    KnowledgeRepository,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class AcceptedUpload(BaseModel):
    job_id: str
    version_id: str
    document_id: str
    status: str = "queued"
    replayed: bool = False


class AcceptedOperation(BaseModel):
    operation_id: str
    status: str = "pending"


class DocumentMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict[str, Any] | None = None


def _repository(request: Request) -> KnowledgeRepository:
    repository = getattr(request.app.state, "knowledge_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Knowledge service is unavailable")
    return repository


def _storage(request: Request) -> SourceStorage:
    storage = getattr(request.app.state, "source_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Knowledge storage is unavailable")
    return storage


async def _authorized_base(
    request: Request, context: AuthContext, kb_id: str
) -> dict[str, Any]:
    require_knowledge_base_access(context, kb_id)
    base = await _repository(request).get_knowledge_base(kb_id)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    require_project_access(context, base["project_id"])
    return base


async def _authorized_document(
    request: Request, context: AuthContext, document_id: str
) -> dict[str, str]:
    scope = await _repository(request).get_document_scope(document_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Document not found")
    require_knowledge_base_access(context, scope["kb_id"])
    require_project_access(context, scope["project_id"])
    return scope


@router.get("/knowledge-bases")
async def list_knowledge_bases(
    request: Request, context: AuthContext = Depends(get_auth_context)
) -> list[dict[str, Any]]:
    repository = _repository(request)
    bases = await repository.list_knowledge_bases(context.authorized_knowledge_base_ids)
    allowed_projects = get_authorized_project_ids(context)
    return [base for base in bases if base["project_id"] in allowed_projects]


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedUpload,
)
async def upload_document(
    kb_id: str,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    metadata: str = Form("{}"),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
    context: AuthContext = Depends(get_auth_context),
) -> AcceptedUpload:
    base = await _authorized_base(request, context, kb_id)
    config = get_config()
    name = Path(file.filename or "").name
    content = await file.read(config.knowledge_max_upload_bytes + 1)
    if len(content) > config.knowledge_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds configured size limit")
    media_type = (file.content_type or "application/octet-stream").split(";", 1)[0].lower()
    try:
        suffix = "." + validate_upload(content, media_type, filename=name)
        metadata_value = json.loads(metadata)
        if not isinstance(metadata_value, dict):
            raise ValueError("Metadata must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_title = title.strip() or Path(name).stem
    canonical = json.dumps(
        {"sha256": hashlib.sha256(content).hexdigest(), "title": normalized_title,
         "metadata": metadata_value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    storage = _storage(request)
    stored = storage.save(BytesIO(content), suffix=suffix)
    try:
        result = await _repository(request).create_upload(
            project_id=base["project_id"],
            kb_id=kb_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            storage_key=stored.key,
            title=normalized_title,
            metadata=metadata_value,
            actor_id=context.user_id,
            correlation_id=request.headers.get("X-Correlation-ID", str(uuid4())),
        )
    except IdempotencyConflict as exc:
        storage.delete(stored.key)
        raise HTTPException(status_code=409, detail="Idempotency key conflicts with another request") from exc
    except Exception as exc:
        storage.delete(stored.key)
        raise HTTPException(status_code=503, detail="Knowledge job could not be registered") from exc
    if result["replayed"]:
        storage.delete(stored.key)
    return AcceptedUpload(**result)


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    request: Request,
    limit: int = 50,
    context: AuthContext = Depends(get_auth_context),
) -> list[dict[str, Any]]:
    await _authorized_base(request, context, kb_id)
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return await _repository(request).list_documents(kb_id=kb_id, limit=limit)


@router.get("/documents/{document_id}/versions")
async def list_document_versions(
    document_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> list[dict[str, Any]]:
    await _authorized_document(request, context, document_id)
    return await _repository(request).list_versions(document_id=document_id)


@router.patch("/documents/{document_id}")
async def update_document_metadata(
    document_id: str,
    payload: DocumentMetadataUpdate,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    if payload.title is None and payload.metadata is None:
        raise HTTPException(status_code=400, detail="At least one metadata field is required")
    scope = await _authorized_document(request, context, document_id)
    updated = await _repository(request).update_document_metadata(
        project_id=scope["project_id"],
        kb_id=scope["kb_id"],
        document_id=document_id,
        title=payload.title,
        metadata=payload.metadata,
        actor_id=context.user_id,
        correlation_id=request.headers.get("X-Correlation-ID", str(uuid4())),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document_id": document_id, "status": "updated"}


@router.post(
    "/documents/{document_id}/versions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedUpload,
)
async def replace_document_version(
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
    context: AuthContext = Depends(get_auth_context),
) -> AcceptedUpload:
    scope = await _authorized_document(request, context, document_id)
    config = get_config()
    name = Path(file.filename or "").name
    content = await file.read(config.knowledge_max_upload_bytes + 1)
    if len(content) > config.knowledge_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds configured size limit")
    media_type = (file.content_type or "application/octet-stream").split(";", 1)[0].lower()
    try:
        suffix = "." + validate_upload(content, media_type, filename=name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fingerprint = hashlib.sha256(
        json.dumps(
            {"sha256": hashlib.sha256(content).hexdigest(), "document_id": document_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    storage = _storage(request)
    stored = storage.save(BytesIO(content), suffix=suffix)
    try:
        result = await _repository(request).create_replacement(
            project_id=scope["project_id"],
            kb_id=scope["kb_id"],
            document_id=document_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            storage_key=stored.key,
            actor_id=context.user_id,
            correlation_id=request.headers.get("X-Correlation-ID", str(uuid4())),
        )
    except IdempotencyConflict as exc:
        storage.delete(stored.key)
        raise HTTPException(status_code=409, detail="Idempotency key conflicts with another request") from exc
    except LookupError as exc:
        storage.delete(stored.key)
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except Exception as exc:
        storage.delete(stored.key)
        raise HTTPException(status_code=503, detail="Knowledge job could not be registered") from exc
    if result["replayed"]:
        storage.delete(stored.key)
    return AcceptedUpload(**result)


@router.get("/versions/{version_id}/quality-report")
async def get_quality_report(
    version_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    scope = await _repository(request).get_version_scope(version_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Version not found")
    require_knowledge_base_access(context, scope["kb_id"])
    require_project_access(context, scope["project_id"])
    report = await _repository(request).get_quality_report(version_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Quality report not found")
    return report


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    job = await _repository(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    require_knowledge_base_access(context, job["kb_id"])
    require_project_access(context, job["project_id"])
    return job


@router.get("/knowledge-bases/{kb_id}/jobs")
async def list_jobs(
    kb_id: str,
    request: Request,
    limit: int = 50,
    context: AuthContext = Depends(get_auth_context),
) -> list[dict[str, Any]]:
    await _authorized_base(request, context, kb_id)
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return await _repository(request).list_jobs(kb_id=kb_id, limit=limit)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_job(
    job_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    job = await _repository(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    require_knowledge_base_access(context, job["kb_id"])
    require_project_access(context, job["project_id"])
    if job["status"] != "failed_retryable":
        raise HTTPException(status_code=409, detail="Only retryable jobs can be retried")
    if not await _repository(request).retry_job(job_id):
        raise HTTPException(status_code=409, detail="Job state changed")
    return {"job_id": job_id, "status": "queued"}


@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    scope = await _authorized_document(request, context, document_id)
    result = await _repository(request).enqueue_reindex(
        project_id=scope["project_id"],
        kb_id=scope["kb_id"],
        document_id=document_id,
        actor_id=context.user_id,
        correlation_id=request.headers.get("X-Correlation-ID", str(uuid4())),
    )
    return result


@router.delete("/documents/{document_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_document(
    document_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    scope = await _authorized_document(request, context, document_id)
    deleted = await _repository(request).tombstone_document(
        project_id=scope["project_id"], kb_id=scope["kb_id"], document_id=document_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document_id": document_id, "status": "tombstoned"}


@router.post("/documents/{document_id}/versions/{version_id}/activate", status_code=202)
async def activate_version(
    document_id: str,
    version_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> AcceptedOperation:
    scope = await _authorized_document(request, context, document_id)
    try:
        operation_id = await _repository(request).begin_activation(
            project_id=scope["project_id"],
            kb_id=scope["kb_id"],
            document_id=document_id,
            version_id=version_id,
        )
    except InvalidVersionTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AcceptedOperation(operation_id=operation_id)


@router.get("/activation-operations/{operation_id}")
async def get_activation_operation(
    operation_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    operation = await _repository(request).get_activation_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Activation operation not found")
    require_knowledge_base_access(context, operation["kb_id"])
    require_project_access(context, operation["project_id"])
    return {"operation_id": operation_id, "status": operation["status"]}
