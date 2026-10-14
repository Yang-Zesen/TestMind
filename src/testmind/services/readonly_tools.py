"""Three deterministic gates around one bounded, read-only tool proposal."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testmind.core.decision_audit import emit_decision_event
from testmind.core.security import AuthContext, require_project_access
from testmind.infrastructure.zentao.mcp import ZenTaoError, ZenTaoService
from testmind.infrastructure.zentao.tools import create_zentao_tools
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState


class ToolProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["zentao_project", "zentao_list", "zentao_detail"]
    resource: Literal["stories", "testcases", "bugs"] | None = None
    identifier: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,18}$")
    page: int = Field(default=1, ge=1, le=100)


class ReadonlyToolNode(BaseNode):
    name = "readonly_tools"

    def __init__(
        self,
        service: ZenTaoService,
        context: Callable[[str], Awaitable[AuthContext | None]],
        propose: Callable[[str], Awaitable[ToolProposal]],
        project_mapping: dict[str, str],
    ) -> None:
        super().__init__()
        self.service, self.context, self.propose = service, context, propose
        self.mapping = dict(project_mapping)

    async def process(self, state: GraphState) -> GraphState:
        project, user = state.get("project_id", ""), state.get("user_id", "")
        audit = {
            "task_id": state.get("task_id"),
            "trace_id": state.get("trace_id"),
            "attempt_id": state.get("attempt_id"),
            "node": self.name,
            "project_id": project,
            "schema_version": "zentao-read-v1",
        }

        async def authorize() -> AuthContext:
            auth = await self.context(user)
            if auth is None or auth.user_id != user:
                raise HTTPException(401)
            require_project_access(auth, project)
            if project not in self.mapping:
                raise HTTPException(403)
            return AuthContext(user, frozenset({self.mapping[project]}), frozenset())

        try:
            bound_auth = await authorize()
            emit_decision_event(
                self._logger, "tool_gate", **audit, policy_decision="allowed", outcome="authorized"
            )
            proposal = ToolProposal.model_validate(
                await self.propose(state.get("resolved_query") or state.get("raw_query", ""))
            )
            if proposal.tool != "zentao_project" and (
                proposal.resource is None
                or (proposal.tool == "zentao_detail" and proposal.identifier is None)
            ):
                return {
                    "status": "needs_input",
                    "answer": "Please specify the resource and, for a detail query, the record ID.",
                    "last_error": {"code": "tool_arguments_required"},
                }
            await authorize()
            downstream = self.service.caller
            count = 0

            class BoundedCaller:
                async def call(inner_self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    nonlocal count
                    await authorize()
                    if count >= 3:
                        raise ZenTaoError(
                            "Tool call budget exhausted", code="tool_budget_exhausted"
                        )
                    count += 1
                    async with asyncio.timeout(10):
                        result = await downstream.call(name, arguments)
                    if not isinstance(result, dict) or len(json.dumps(result, default=str)) > 24000:
                        raise ZenTaoError("Invalid response", code="invalid_response")
                    return result

            tools = {
                tool.name: tool
                for tool in create_zentao_tools(ZenTaoService(BoundedCaller()), bound_auth)
            }
            arguments: dict[str, Any] = {"project_id": self.mapping[project]}
            if proposal.tool != "zentao_project":
                arguments["resource"] = proposal.resource
                if proposal.tool == "zentao_list":
                    arguments["page"] = proposal.page
                else:
                    arguments["identifier"] = proposal.identifier
            result = await tools[proposal.tool].ainvoke(arguments)
            await authorize()
            records = result.get("records", []) if proposal.tool == "zentao_list" else [result]
            public = []
            for record in records:
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("id"), (str, int))
                    or isinstance(record.get("id"), bool)
                    or not isinstance(record.get("name", record.get("title")), str)
                    or not isinstance(record.get("status"), str)
                ):
                    raise ZenTaoError("Invalid response", code="invalid_response")
                public.append(
                    {key: record[key] for key in ("id", "name", "title", "status") if key in record}
                )
            answer = json.dumps(public, ensure_ascii=False)
            if len(answer) > 12000:
                raise ZenTaoError("Invalid response", code="invalid_response")
            emit_decision_event(
                self._logger,
                "tool_stopped",
                **audit,
                tool_name=proposal.tool,
                outcome="completed",
                stop_reason="completed",
                result_count=len(public),
            )
            return {
                "status": "completed",
                "answer": answer,
                "current_stage": "readonly_tools",
                "last_error": {},
            }
        except HTTPException:
            code, status = "project_access_denied", "denied"
        except (ValidationError, ValueError):
            code, status = "invalid_tool_arguments", "failed"
        except TimeoutError:
            code, status = "dependency_timeout", "failed"
        except ZenTaoError as exc:
            code, status = exc.code, "denied" if exc.status_code == 403 else "failed"
        emit_decision_event(
            self._logger, "tool_stopped", **audit, outcome=status, stop_reason=code, error_code=code
        )
        return {
            "status": status,
            "answer": "The read-only query could not be completed.",
            "current_stage": "readonly_tools",
            "last_error": {"code": code},
        }
