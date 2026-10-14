"""Reload permissions and active versions around evidence consumption."""

from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.models.retrieval import RetrievalScope
from testmind.services.request_context import RequestContextService


class RetrievalScopeLoader:
    def __init__(self, tasks: TaskRepository, knowledge: KnowledgeRepository):
        self.tasks, self.contexts = tasks, RequestContextService(knowledge)

    async def __call__(self, previous: RetrievalScope) -> RetrievalScope:
        auth = await self.tasks.context(previous.user_id)
        if auth is None or previous.project_id not in auth.authorized_project_ids or not previous.knowledge_base_ids.issubset(auth.authorized_knowledge_base_ids):
            raise PermissionError("retrieval_access_revoked")
        context = await self.contexts.build(auth, project_id=previous.project_id)
        if context.retrieval_scope is None:
            raise PermissionError("retrieval_scope_unavailable")
        return context.retrieval_scope
