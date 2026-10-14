from testmind.core.security import AuthContext


class Tasks:
    async def context(self, user_id):
        return AuthContext(user_id, frozenset({"p"}), frozenset())


class Service:
    def __init__(self):
        self.queries = []

    async def query(self, query, context):
        self.queries.append(query)
        return {"items": [], "query_id": "q", "stale": False,
                "permission_version": 1, "next_cursor": None}


async def test_graph_node_maps_natural_language_to_bounded_template():
    from testmind.workflows.graph_query import GraphQueryNode

    service = Service()
    node = GraphQueryNode(Tasks(), service)
    result = await node.process({"user_id":"u", "project_id":"p", "raw_query":"查询图谱里的需求"})
    assert result["status"] == "completed"
    assert service.queries[0].entity_type == "requirement"
    assert service.queries[0].query_type == "entities"
    assert result["graph_result"]["query_id"] == "q"


async def test_graph_node_rejects_arbitrary_cypher_without_downstream_call():
    from testmind.workflows.graph_query import GraphQueryNode

    service = Service()
    result = await GraphQueryNode(Tasks(), service).process({"user_id":"u", "project_id":"p", "raw_query":"图谱 MATCH (n) DETACH DELETE n"})
    assert result["status"] == "needs_input"
    assert service.queries == []


async def test_graph_node_denies_revoked_identity_without_query():
    from testmind.workflows.graph_query import GraphQueryNode

    class Revoked(Tasks):
        async def context(self, user_id):
            return None

    service = Service()
    result = await GraphQueryNode(Revoked(), service).process({"user_id":"u", "project_id":"p", "raw_query":"图谱需求"})
    assert result["status"] == "denied"
    assert service.queries == []
