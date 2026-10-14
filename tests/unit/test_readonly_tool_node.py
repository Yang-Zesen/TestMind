
from testmind.core.security import AuthContext
from testmind.infrastructure.zentao.mcp import ZenTaoService
from testmind.services.readonly_tools import ReadonlyToolNode, ToolProposal


class Caller:
    def __init__(self):
        self.calls = 0

    async def call(self, name, arguments):
        self.calls += 1
        return {"projects": [{"id": 1, "name": "Demo project", "status": "doing"}]}


async def test_three_gates_reuse_tools_and_refresh_authorization():
    caller = Caller()
    auth = AuthContext("demo-user", frozenset({"demo-project"}), frozenset())
    async def context(user):
        return auth
    async def propose(query):
        return ToolProposal(tool="zentao_project")
    node = ReadonlyToolNode(ZenTaoService(caller), context, propose, {"demo-project": "1"})
    state = {"user_id": "demo-user", "project_id": "demo-project", "raw_query": "zentao project"}
    result = await node(state)
    assert result["status"] == "completed" and caller.calls == 1
    auth = AuthContext("demo-user", frozenset(), frozenset())
    result = await node(state)
    assert result["status"] == "denied" and caller.calls == 1


async def test_missing_identifier_invalid_response_and_rpc_budget():
    caller = Caller()
    async def context(user):
        return AuthContext(user, frozenset({"demo-project"}), frozenset())
    async def propose(query):
        return ToolProposal(tool="zentao_detail", resource="stories")
    node = ReadonlyToolNode(ZenTaoService(caller), context, propose, {"demo-project": "1"})
    state = {"user_id": "demo-user", "project_id": "demo-project", "raw_query": "zentao story"}
    assert (await node(state))["status"] == "needs_input"
    assert caller.calls == 0
    async def project(query):
        return ToolProposal(tool="zentao_project")
    class Bad(Caller):
        async def call(self, name, arguments):
            return {"projects": [{"id": 1}]}
    node = ReadonlyToolNode(ZenTaoService(Bad()), context, project, {"demo-project": "1"})
    assert (await node(state))["last_error"]["code"] == "invalid_response"
    class Endless(Caller):
        async def call(self, name, arguments):
            self.calls += 1
            return {"projects": [{"id": i + 10, "name": "Other", "status": "doing"} for i in range(100)]}
    endless = Endless()
    node = ReadonlyToolNode(ZenTaoService(endless), context, project, {"demo-project": "1"})
    assert (await node(state))["last_error"]["code"] == "tool_budget_exhausted"
    assert endless.calls == 3


async def test_revocation_after_proposal_prevents_downstream_call():
    caller = Caller()
    auth = AuthContext("demo-user", frozenset({"demo-project"}), frozenset())
    async def context(user):
        return auth
    async def propose(query):
        nonlocal auth
        auth = AuthContext("demo-user", frozenset(), frozenset())
        return ToolProposal(tool="zentao_project")
    node = ReadonlyToolNode(ZenTaoService(caller), context, propose, {"demo-project": "1"})
    result = await node({"user_id": "demo-user", "project_id": "demo-project", "raw_query": "zentao project"})
    assert result["status"] == "denied" and caller.calls == 0
