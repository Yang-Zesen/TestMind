import pytest

from testmind.core.errors import ValidationError
from testmind.orchestration.nodes.base import BaseNode


class EchoNode(BaseNode):
    called = False
    async def process(self, state):
        self.called = True
        return state.get("result", {})


async def test_large_input_is_rejected_before_node_execution():
    node = EchoNode()
    with pytest.raises(ValidationError):
        await node({"raw_query": "x" * 6001})
    assert not node.called


@pytest.mark.parametrize("result", ["string", {"answer": "x" * 12001}])
async def test_invalid_or_large_output_is_rejected(result):
    with pytest.raises(ValidationError):
        await EchoNode()({"raw_query": "hello", "result": result})
