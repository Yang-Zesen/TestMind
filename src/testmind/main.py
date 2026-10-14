"""FastAPI application entrypoint."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from testmind.api.router import api_router
from testmind.core.authentication import DemoAuthorizationProvider
from testmind.core.config import get_config
from testmind.core.runtime_limits import RuntimeLimitsMiddleware
from testmind.core.security import (
    AuthContext,
    MockAuthorizationProvider,
    RequestStateAuthorizationProvider,
)
from testmind.infrastructure.graph_sync.query import GraphQueryService, Neo4jGraphReader
from testmind.infrastructure.graph_sync.runtime import GraphSettings
from testmind.infrastructure.graph_sync.source import SignedGraphSource
from testmind.infrastructure.jev.jev_client import JevClient
from testmind.infrastructure.knowledge.source_events import ZenTaoEventAdapter
from testmind.infrastructure.knowledge.source_storage import create_source_storage
from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService
from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.infrastructure.postgres.postgresql_client import open_postgresql_client
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.infrastructure.zentao.mcp import ZenTaoMCPClient, ZenTaoService, ZenTaoSettings
from testmind.orchestration.builder import create_main_graph
from testmind.services.case_publication import CasePublicationService, DemoCaseWriter
from testmind.services.retrieval_scope import RetrievalScopeLoader
from testmind.workflows.retrieval import SharedRetrievalService


def selector_loop_factory(**_: object) -> asyncio.AbstractEventLoop:
    """Use an event loop compatible with psycopg async on Windows."""
    return asyncio.SelectorEventLoop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = get_config()
    jev_client = None if config.demo_mode else JevClient.from_config()
    retrieval = MilvusRetrievalService(get_config())
    graph_settings = GraphSettings.from_env()
    graph_driver = None
    zentao_client = None
    if not getattr(app.state, "authorization_provider", None):
        if config.knowledge_auth_mode == "mock":
            app.state.authorization_provider = MockAuthorizationProvider(
                AuthContext(
                    user_id=config.knowledge_mock_user_id,
                    authorized_project_ids=frozenset(
                        value.strip()
                        for value in config.knowledge_mock_project_ids.split(",")
                        if value.strip()
                    ),
                    authorized_knowledge_base_ids=frozenset(
                        value.strip()
                        for value in config.knowledge_mock_base_ids.split(",")
                        if value.strip()
                    ),
                )
            )
        elif config.knowledge_auth_mode == "request_state":
            app.state.authorization_provider = RequestStateAuthorizationProvider()
        else:
            app.state.authorization_provider = None
    try:
        zentao_settings = ZenTaoSettings.from_env()
        if zentao_settings.enabled:
            zentao_client = ZenTaoMCPClient(zentao_settings)
        app.state.zentao_service = ZenTaoService(zentao_client) if zentao_client else None
        async with open_postgresql_client() as postgres:
            app.state.postgresql = postgres
            app.state.knowledge_repository = KnowledgeRepository(postgres.pool)
            app.state.task_repository = TaskRepository(postgres.pool)
            app.state.case_repository = CaseRepository(postgres.pool)
            app.state.case_publication = CasePublicationService(app.state.case_repository, DemoCaseWriter(app.state.case_repository)) if config.demo_mode else None
            shared_retrieval = SharedRetrievalService(retrieval, refresh_scope=RetrievalScopeLoader(app.state.task_repository, app.state.knowledge_repository))
            if config.knowledge_auth_mode == "demo":
                app.state.authorization_provider = DemoAuthorizationProvider(app.state.task_repository)
            app.state.graph_sync_repository = GraphSyncRepository(postgres.pool)
            app.state.graph_query_service = None
            app.state.graph_event_source = None
            app.state.graph_pull_enabled = graph_settings.enabled and bool(graph_settings.snapshot_url)
            if graph_settings.enabled:
                from neo4j import AsyncGraphDatabase

                graph_driver = AsyncGraphDatabase.driver(
                    graph_settings.uri,
                    auth=(graph_settings.read_user, graph_settings.read_password),
                )
                app.state.graph_query_service = GraphQueryService(
                    app.state.graph_sync_repository,
                    Neo4jGraphReader(graph_driver, database=graph_settings.database),
                )
                if graph_settings.event_secret:
                    app.state.graph_event_source = SignedGraphSource(
                        graph_settings.source_instance, secret=graph_settings.event_secret,
                        project_mapping=graph_settings.project_mapping,
                    )
            if not getattr(app.state, "source_storage", None):
                app.state.source_storage = create_source_storage(config)
            if not getattr(app.state, "zentao_event_adapter", None):
                app.state.zentao_event_adapter = (
                    ZenTaoEventAdapter(secret=config.zentao_event_secret, enabled=config.zentao_event_enabled)
                    if config.zentao_event_secret
                    else None
                )
            app.state.jev_client = jev_client
            app.state.retrieval = retrieval
            if config.demo_mode:
                from testmind.demo.runtime import create_demo_graph
                app.state.graph = create_demo_graph(postgres.checkpointer, app.state.task_repository, shared_retrieval)
            else:
                app.state.graph = create_main_graph(
                    checkpointer=postgres.checkpointer,
                    jev_client=jev_client,
                    retriever=shared_retrieval,
                )
            yield
    finally:
        app.state.zentao_service = None
        if zentao_client is not None:
            await zentao_client.aclose()
        if graph_driver is not None:
            await graph_driver.close()
        try:
            await retrieval.aclose()
        finally:
            if jev_client is not None:
                await jev_client.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(RuntimeLimitsMiddleware)
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "testmind.main:app",
        host=config.app_host,
        port=config.app_port,
        loop="testmind.main:selector_loop_factory",
        log_config=None,
        log_level=config.log_level.lower(),
    )

