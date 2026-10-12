"""Source identities and deterministic graph synchronization decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["project", "member", "requirement", "test_case", "bug", "script", "test_run"]
RevisionDecision = Literal["accepted", "duplicate", "conflict", "superseded"]


@dataclass(frozen=True, slots=True)
class EntityRef:
    source_instance: str
    project_id: str
    entity_type: str
    source_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.source_instance, self.project_id, self.entity_type, self.source_id
        )):
            raise ValueError("Entity identity components must not be empty")

    @property
    def key(self) -> str:
        identity = json.dumps(
            [self.source_instance, self.project_id, self.entity_type, self.source_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class RelationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["belongs_to", "covers", "automates", "executes", "reports", "assigned_to"]
    target_source_instance: str = Field(min_length=1, max_length=200)
    target_type: EntityType
    target_source_id: str = Field(min_length=1, max_length=200)


class GraphEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=200)
    source_instance: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    entity_type: EntityType
    source_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=0)
    operation: Literal["upsert", "delete"]
    owner_id: str | None = Field(default=None, max_length=200)
    properties: dict[str, Any] = Field(default_factory=dict)
    relations: tuple[RelationSpec, ...] = ()

    @property
    def entity_key(self) -> str:
        return EntityRef(
            self.source_instance, self.project_id, self.entity_type, self.source_id
        ).key

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"event_id"})
        payload["relations"] = sorted(
            payload["relations"],
            key=lambda relation: json.dumps(relation, sort_keys=True),
        )
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decide_revision(current: GraphEvent | None, incoming: GraphEvent) -> RevisionDecision:
    if current is None:
        return "accepted"
    if current.entity_key != incoming.entity_key:
        raise ValueError("Cannot compare revisions of different entities")
    if incoming.revision < current.revision:
        return "superseded"
    if incoming.revision == current.revision:
        return "duplicate" if incoming.fingerprint == current.fingerprint else "conflict"
    return "accepted"
