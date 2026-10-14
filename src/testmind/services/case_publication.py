"""Explicit simulated writes with persisted dispatch and reconciliation."""

import asyncio
from typing import Any, Protocol

from testmind.infrastructure.postgres.case_repository import CaseRepository


class CaseWriter(Protocol):
    async def create(self, operation: dict[str, Any]) -> str: ...
    async def lookup(self, operation: dict[str, Any]) -> str | None: ...


class DemoCaseWriter:
    def __init__(self, repository: CaseRepository):
        self.repository = repository

    async def create(self, operation: dict[str, Any]) -> str:
        return await self.repository.demo_create(operation)

    async def lookup(self, operation: dict[str, Any]) -> str | None:
        return await self.repository.demo_lookup(operation["id"])


class CasePublicationService:
    def __init__(self, repository: CaseRepository, writer: CaseWriter):
        self.repository, self.writer = repository, writer

    async def _lookup(self, operation: dict[str, Any]) -> str | None:
        try:
            async with asyncio.timeout(5):
                return await self.writer.lookup(operation)
        except Exception:  # noqa: BLE001 - unknown external result stays unresolved.
            return None

    async def publish(self, asset_id: str, user_id: str, *, version: int, content_hash: str) -> dict[str, Any]:
        operation = await self.repository.prepare_publish(asset_id, user_id, version=version, content_hash=content_hash)
        external_id: str | None
        if operation["status"] == "prepared" and await self.repository.mark_in_flight(operation["id"]):
            try:
                async with asyncio.timeout(10):
                    external_id = await self.writer.create(operation)
                if not external_id or not isinstance(external_id, str):
                    raise ValueError("invalid_external_id")
            except asyncio.CancelledError:
                await asyncio.shield(self.repository.complete_operation(operation["id"], None))
                raise
            except Exception:  # noqa: BLE001 - verify before any external retry.
                external_id = await self._lookup(operation)
            return await self.repository.complete_operation(operation["id"], external_id)
        current = await self.repository.operation(operation["id"])
        assert current is not None
        return current

    async def reconcile(self, operation_id: str, user_id: str) -> dict[str, Any]:
        operation = await self.repository.operation(operation_id)
        if operation is None:
            raise LookupError("operation_not_found")
        asset = await self.repository.asset(operation["asset_id"])
        if asset is None or asset["user_id"] != user_id:
            raise PermissionError("operation_not_authorized")
        external_id = await self._lookup(operation)
        return await self.repository.complete_operation(operation_id, external_id)
