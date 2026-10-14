"""Independent leased projection worker; acknowledgements are fenced by claim token."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from pydantic import ValidationError

from testmind.models.graph_sync import GraphEvent

logger = logging.getLogger(__name__)


class GraphSyncWorker:
    def __init__(
        self, repository: Any, projection: Any, *, lease_seconds: int = 60,
        heartbeat_seconds: float = 15, poll_seconds: float = 2, maintenance: Any = None,
        poller: Any = None,
    ) -> None:
        if not 0 < heartbeat_seconds < lease_seconds or poll_seconds <= 0:
            raise ValueError("Invalid worker timing")
        self.repository = repository
        self.projection = projection
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.poll_seconds = poll_seconds
        self.stopping = asyncio.Event()
        self.maintenance = maintenance
        self.poller = poller

    def stop(self) -> None:
        self.stopping.set()

    async def run(self) -> None:
        while not self.stopping.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve the worker without logging source payloads.
                logger.error("graph_sync_queue_unavailable")
                worked = False
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), self.poll_seconds)

    async def run_once(self) -> bool:
        job = await self.repository.claim(lease_seconds=self.lease_seconds)
        if job is None:
            return False
        identity = {"job_id": job["id"], "claim_token": job["claim_token"]}
        heartbeat: asyncio.Task[bool] | None = None
        work: asyncio.Task[Any] | None = None
        try:
            authorized_user = job["payload"].get("authorized_user")
            lease = {**identity, **({"authorized_user": authorized_user} if authorized_user is not None else {})}
            if job.get("operation") in {"pull", "rebuild", "reconcile"} and authorized_user is not None:
                grant = await self.repository.permission(job["project_id"], authorized_user)
                if grant is None or not grant["can_maintain"]:
                    raise PermissionError("Maintenance permission was revoked")
            if job.get("operation") == "pull":
                if self.poller is None:
                    raise ValueError("pull adapter is not configured")
                work = asyncio.create_task(self.poller.run(job["project_id"], job["payload"]["resource_type"]))
            elif job.get("operation") == "rebuild":
                if self.maintenance is None:
                    raise ValueError("maintenance handler is not configured")
                work = asyncio.create_task(
                    self.maintenance.rebuild(job["project_id"], generation=job["payload"]["generation"],
                                             lease=lease)
                )
            elif job.get("operation") == "reconcile":
                if self.maintenance is None:
                    raise ValueError("maintenance handler is not configured")
                work = asyncio.create_task(self.maintenance.reconcile(
                    job["project_id"], lease=lease
                ))
            else:
                event = GraphEvent.model_validate(job["payload"])
                work = asyncio.create_task(self.projection.apply(event))
            heartbeat = asyncio.create_task(self._heartbeat(identity))
            done, _ = await asyncio.wait((work, heartbeat), timeout=300,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError("Graph operation deadline exhausted")
            if heartbeat.done():
                await heartbeat
                logger.warning("graph_sync_lease_lost", extra={"job_id": job["id"]})
                return True
            await work
            await self.repository.finish(**identity)
        except ValidationError:
            await self.repository.fail(**identity, permanent=True, error_code="invalid_event")
        except PermissionError:
            await self.repository.fail(**identity, permanent=True, error_code="maintenance_permission_revoked")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - persist a bounded failure code, never source contents.
            logger.error("graph_sync_projection_failed", extra={"job_id": job["id"]})
            await self.repository.fail(**identity, permanent=False, error_code="projection_failed")
        finally:
            for task in (work, heartbeat):
                if task is not None:
                    task.cancel()
            for task in (work, heartbeat):
                if task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
        return True

    async def _heartbeat(self, identity: dict[str, str]) -> bool:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            if not await self.repository.renew(**identity, lease_seconds=self.lease_seconds):
                return False
