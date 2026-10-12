import os

import pytest


@pytest.mark.integration
def test_knowledge_workflow_requires_external_services():
    if not os.getenv("KNOWLEDGE_TEST_DSN"):
        pytest.skip("Set KNOWLEDGE_TEST_DSN to run PostgreSQL/Milvus integration tests")
    pytest.fail("Integration harness is environment-specific and must be supplied by deployment CI")
