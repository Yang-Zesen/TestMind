import hashlib
import hmac
import json

import pytest

from testmind.infrastructure.graph_sync.source import SignedGraphSource


def body():
    return json.dumps({"event_id": "e1", "source_instance": "s1", "project_id": "external1",
                       "entity_type": "requirement", "source_id": "42", "revision": 1,
                       "operation": "upsert", "properties": {"title": "Test"}}).encode()


def signature(payload, timestamp=1000):
    return hmac.new(b"secret", str(timestamp).encode()+b"."+payload, hashlib.sha256).hexdigest()


def test_signature_maps_project_using_trusted_configuration():
    source = SignedGraphSource("s1", secret="secret", project_mapping={"external1": "p1"})
    event = source.normalize(body(), timestamp=1000, signature=signature(body()), now=1001)
    assert event.project_id == "p1"


def test_expired_signature_and_unknown_project_fail_closed():
    source = SignedGraphSource("s1", secret="secret", project_mapping={})
    with pytest.raises(PermissionError):
        source.normalize(body(), timestamp=1000, signature=signature(body()), now=2000)
    with pytest.raises(PermissionError):
        source.normalize(body(), timestamp=1000, signature=signature(body()), now=1001)


def test_empty_secret_cannot_enable_source():
    with pytest.raises(ValueError):
        SignedGraphSource("s1", secret="", project_mapping={"external1": "p1"})
