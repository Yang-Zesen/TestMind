"""Small synchronous client for the authorized knowledge API."""

from __future__ import annotations

from typing import Any, Self

import httpx


class KnowledgeApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class KnowledgeApiClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 30.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/knowledge-bases")

    def list_documents(self, project_id: str, kb_id: str) -> list[dict[str, Any]]:
        del project_id
        return self._request("GET", f"/api/v1/knowledge-bases/{kb_id}/documents")

    def list_jobs(self, project_id: str, kb_id: str) -> list[dict[str, Any]]:
        del project_id
        return self._request("GET", f"/api/v1/knowledge-bases/{kb_id}/jobs")

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/knowledge/documents/{document_id}/versions")

    def get_quality_report(self, version_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/knowledge/versions/{version_id}/quality-report")

    def upload_document(self, project_id: str, kb_id: str, filename: str, content: bytes, *, idempotency_key: str) -> dict[str, Any]:
        del project_id
        return self._request(
            "POST",
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            files={"file": (filename, content)},
            headers={"Idempotency-Key": idempotency_key},
        )

    def retry_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/knowledge/jobs/{job_id}/retry")

    def reindex(self, document_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/knowledge/documents/{document_id}/reindex")

    def activate(self, document_id: str, version_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/knowledge/documents/{document_id}/versions/{version_id}/activate")

    def activation_status(self, activation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/knowledge/activations/{activation_id}")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise KnowledgeApiError(503, "Knowledge service is unavailable") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", "Knowledge request failed")
            except ValueError:
                detail = "Knowledge request failed"
            raise KnowledgeApiError(response.status_code, str(detail))
        return response.json() if response.content else {}
