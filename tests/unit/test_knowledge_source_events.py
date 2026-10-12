import hashlib
import hmac

import pytest

from testmind.infrastructure.knowledge.source_events import (
    DisabledZenTaoEventAdapter,
    NormalizedSourceEvent,
    ZenTaoEventAdapter,
)


def test_zentao_event_signature_and_resource_filtering():
    body = b'{"event_id":"evt-1","project":"p1","knowledge_base":"kb1","resource_type":"requirement","resource_id":"r1","operation":"updated","version":"3"}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    adapter = ZenTaoEventAdapter(secret="secret", enabled=True)
    event = adapter.verify_and_normalize({"X-ZenTao-Signature": signature}, body)

    assert isinstance(event, NormalizedSourceEvent)
    assert event.source_event_id == "evt-1"
    assert event.resource_type == "requirement"

    invalid_body = body.replace(b"requirement", b"relation")
    invalid_signature = hmac.new(b"secret", invalid_body, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError, match="resource type"):
        adapter.verify_and_normalize({"X-ZenTao-Signature": invalid_signature}, invalid_body)


def test_disabled_adapter_is_explicitly_unavailable():
    with pytest.raises(RuntimeError, match="disabled"):
        DisabledZenTaoEventAdapter().verify_and_normalize({}, b"{}")
