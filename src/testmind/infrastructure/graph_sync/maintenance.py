"""Recoverable generation rebuild with an authoritative switch guard."""

from typing import Any
from uuid import uuid4


class GraphMaintenance:
    def __init__(self, repository: Any, projection_factory: Any) -> None:
        self.repository = repository
        self.projection_factory = projection_factory

    async def reconcile(self, project_id: str) -> dict[str, Any]:
        for _ in range(10):
            generation = await self.repository.generation(project_id)
            projection = self.projection_factory(generation)
            snapshot = await self.repository.snapshot(project_id)
            superseded = False
            for event in snapshot:
                if not await self._apply(projection, event):
                    superseded = True
            if not superseded and generation == await self.repository.generation(project_id):
                return {"generation": generation, "projected": len(snapshot)}
        raise RuntimeError("Reconciliation could not catch up")

    @staticmethod
    async def _apply(projection: Any, event: Any) -> bool:
        if await projection.apply(event):
            return True
        current = await projection.current(event) if hasattr(projection, "current") else None
        if current and current["revision"] > event.revision:
            return False
        raise RuntimeError("Projection rejected conflicting authoritative revision")

    async def rebuild(self, project_id: str, *, generation: str | None = None,
                      lease: dict[str, str] | None = None) -> dict[str, Any]:
        generation = generation or str(uuid4())
        projection = self.projection_factory(generation)
        fingerprints: dict[str, str] = {}
        for _ in range(10):
            snapshot = await self.repository.snapshot(project_id)
            superseded = False
            for event in snapshot:
                if fingerprints.get(event.entity_key) != event.fingerprint:
                    if not await self._apply(projection, event):
                        superseded = True
                        continue
                    fingerprints[event.entity_key] = event.fingerprint
            if superseded:
                continue
            if await self.repository.switch_generation(project_id, generation, fingerprints, **(lease or {})):
                return {"generation": generation, "projected": len(fingerprints)}
        raise RuntimeError("Rebuild could not catch up; online generation remains unchanged")


class ActiveGenerationProjection:
    def __init__(self, repository: Any, projection_factory: Any) -> None:
        self.repository = repository
        self.projection_factory = projection_factory

    async def apply(self, event: Any) -> bool:
        for _ in range(10):
            generation = await self.repository.generation(event.project_id)
            applied = await self.projection_factory(generation).apply(event)
            if generation == await self.repository.generation(event.project_id):
                return bool(applied)
        raise RuntimeError("Online generation changed repeatedly")
