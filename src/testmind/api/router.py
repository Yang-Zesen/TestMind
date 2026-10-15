from fastapi import APIRouter

from testmind.api.approvals import router as approvals_router
from testmind.api.chat import router as chat_router
from testmind.api.graph_events import router as graph_events_router
from testmind.api.graph_query import router as graph_router
from testmind.api.health import router as health_router
from testmind.api.knowledge import router as knowledge_router
from testmind.api.knowledge_events import router as knowledge_events_router
from testmind.api.operations import router as operations_router
from testmind.api.tasks import router as tasks_router
from testmind.api.test_cases import router as cases_router
from testmind.api.zentao import router as zentao_router

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(tasks_router)
api_router.include_router(approvals_router)
api_router.include_router(health_router)
api_router.include_router(graph_router)
api_router.include_router(graph_events_router)
api_router.include_router(knowledge_router)
api_router.include_router(knowledge_events_router)
api_router.include_router(zentao_router)
api_router.include_router(cases_router)
api_router.include_router(operations_router)
