"""Explicit, disabled-by-default normalized ZenTao event boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedSourceEvent:
    source: str
    source_event_id: str
    project_ref: str
    kb_ref: str
    resource_type: str
    resource_id: str
    operation: str
    source_version: str
    content_hash: str | None
    snapshot: Mapping[str, Any]


class DisabledZenTaoEventAdapter:
    def verify_and_normalize(self, headers: Mapping[str, str], body: bytes) -> NormalizedSourceEvent:
        del headers, body
        raise RuntimeError("ZenTao adapter is disabled until its external contract is configured")


class ZenTaoEventAdapter:
    _SUPPORTED_RESOURCE_TYPES = frozenset({"requirement", "api_document"})

    def __init__(self, *, secret: str, enabled: bool = False) -> None:
        self._secret = secret.encode()
        self._enabled = enabled

    def verify_and_normalize(
        self, headers: Mapping[str, str], body: bytes
    ) -> NormalizedSourceEvent:
        if not self._enabled:
            raise RuntimeError("ZenTao adapter is disabled until its external contract is configured")
        supplied = headers.get("X-ZenTao-Signature", "")
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise PermissionError("Invalid ZenTao event signature")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("ZenTao event payload is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("ZenTao event payload must be an object")
        required = ("event_id", "project", "knowledge_base", "resource_type", "resource_id", "operation", "version")
        if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in required):
            raise ValueError("ZenTao event is missing required fields")
        resource_type = payload["resource_type"].strip()
        if resource_type not in self._SUPPORTED_RESOURCE_TYPES:
            raise ValueError("Unsupported ZenTao resource type")
        snapshot = payload.get("snapshot", {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        return NormalizedSourceEvent(
            source="zentao",
            source_event_id=payload["event_id"],
            project_ref=payload["project"],
            kb_ref=payload["knowledge_base"],
            resource_type=resource_type,
            resource_id=payload["resource_id"],
            operation=payload["operation"],
            source_version=payload["version"],
            content_hash=payload.get("content_hash"),
            snapshot=snapshot,
        )
