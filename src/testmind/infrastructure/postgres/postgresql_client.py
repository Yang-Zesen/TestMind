"""PostgreSQL resources shared by one FastAPI application process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.core.config import get_config


@dataclass(frozen=True, slots=True)
class PostgresSQLClient:
    """One application-scoped pool and its LangGraph checkpoint adapter."""

    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]]
    checkpointer: AsyncPostgresSaver


@asynccontextmanager
async def open_postgresql_client() -> AsyncIterator[PostgresSQLClient]:
    """Open at application startup; close when FastAPI's lifespan ends."""
    dsn = get_config().postgres_dsn.strip()
    if not dsn:
        raise RuntimeError("缺少 POSTGRES_DSN 配置")

    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=5,
        timeout=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    async with pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield PostgresSQLClient(pool=pool, checkpointer=checkpointer)
