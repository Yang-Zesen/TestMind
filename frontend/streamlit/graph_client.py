"""Graph operator client; project grants come from the backend."""

from typing import Any

from frontend.streamlit.api_client import KnowledgeApiClient


class GraphApiClient(KnowledgeApiClient):
    def projects(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/graph/projects")

    def query(self, project_id: str, query_type: str, *, limit: int = 50,
              cursor: str | None = None) -> dict[str, Any]:
        if query_type not in {"entities", "relationships"}:
            raise ValueError("Unsupported query template")
        params: dict[str, str | int] = {"project_id": project_id, "query_type": query_type, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._request("GET", "/api/v1/graph/query", params=params)

    def metrics(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", "/api/v1/metrics", params={"project_id": project_id})

    def maintain(self, project_id: str, operation: str) -> dict[str, Any]:
        if operation not in {"rebuild", "reconcile"}:
            raise ValueError("Unsupported maintenance operation")
        return self._request("POST", f"/api/v1/graph/{operation}", json={"project_id": project_id})

    def pull(self, project_id: str, resource_type: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/integrations/sync-runs", json={
            "project_id": project_id, "resource_type": resource_type,
        })

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/integrations/sync-jobs/{job_id}")
