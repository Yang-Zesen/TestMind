"""Case design route: explicit sources, optional shared evidence, durable drafts."""

import asyncio
import json
import logging
from typing import Any

from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.models.test_case import CaseSourceInput, design_cases
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.services.request_context import RequestContextService
from testmind.workflows.retrieval import SharedRetrievalService

PREFIX = "case-design:"


class CaseDesignNode(BaseNode):
    name = "case_design"

    def __init__(self, repository: CaseRepository, retrieval: SharedRetrievalService | None = None):
        super().__init__()
        self.repository, self.retrieval = repository, retrieval

    async def process(self, state: GraphState) -> GraphState:
        query = state.get("corrected_query", state.get("raw_query", ""))
        if not query.startswith(PREFIX):
            return {"status":"needs_input", "answer":"请先选择需求或 OpenAPI 来源。", "last_error":{"code":"case_source_required"}}
        stage = "parse_request"
        try:
            specification = json.loads(query[len(PREFIX):])
            source_ids = specification.get("source_ids") if isinstance(specification, dict) else specification
            use_knowledge = isinstance(specification, dict) and specification.get("use_knowledge") is True
            if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 3 or any(not isinstance(item, str) or len(item) > 100 for item in source_ids):
                raise ValueError("invalid_case_sources")
            auth = await self.repository.context(state["user_id"])
            if auth is None or state["project_id"] not in auth.authorized_project_ids:
                raise PermissionError("case_access_denied")
            sources = []
            bodies = []
            stage = "load_sources"
            for source_id in source_ids:
                source = await self.repository.source(source_id)
                if source is None or source["deleted"] or source["project_id"] != state["project_id"] or source["user_id"] != state["user_id"]:
                    raise PermissionError("case_source_access_denied")
                sources.append(source)
                payload = CaseSourceInput(**{key:source[key] for key in ("project_id","kind","title","content")})
                stage = "design_cases"
                bodies.extend(design_cases(payload))
            evidence: list[dict[str, Any]] = []
            if use_knowledge and self.retrieval is not None:
                context = await RequestContextService(KnowledgeRepository(self.repository.pool)).build(auth, project_id=state["project_id"])
                scope = context.retrieval_scope
                if scope and scope.active_versions:
                    bundle = await asyncio.wait_for(self.retrieval.evidence(sources[0]["title"], scope, caller_id=state["task_id"]), timeout=45)
                    evidence = [chunk.model_dump(exclude={"content", "relevance"}) for chunk in bundle.chunks]
            claim = {"id":state["task_id"], "claim_token":state["attempt_id"]}
            stage = "persist_drafts"
            assets = await self.repository.save_drafts(claim, sources, bodies[:36], evidence)
            return {"status":"waiting_review", "answer":f"已生成 {len(assets)} 条待审核用例（本地规则草稿），请逐条核对具体输入与预期。", "current_stage":"case_review", "case_asset_ids":[asset["id"] for asset in assets], "citations":evidence, "last_error":{}}
        except PermissionError:
            return {"status":"denied", "answer":"无权访问所选用例来源。", "last_error":{"code":"case_source_access_denied"}}
        except (ValueError, KeyError, TypeError) as exc:
            logging.getLogger(__name__).warning(
                "case_design_input_rejected stage=%s error_type=%s", stage, type(exc).__name__
            )
            return {"status":"needs_input", "answer":"来源格式不完整或版本已变化，请重新选择来源。", "last_error":{"code":"case_source_invalid"}}
