from fastapi import APIRouter

from testmind.api.chat import router as chat_router
from testmind.api.graph_events import router as graph_events_router
from testmind.api.graph_query import router as graph_router
from testmind.api.knowledge import router as knowledge_router
from testmind.api.knowledge_events import router as knowledge_events_router

api_router = APIRouter()
api_router.include_router(chat_router)
api_router.include_router(graph_router)
api_router.include_router(graph_events_router)
api_router.include_router(knowledge_router)
api_router.include_router(knowledge_events_router)
