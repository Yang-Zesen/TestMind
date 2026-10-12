import pytest
from langchain_core.messages import AIMessage, HumanMessage

from testmind.models.coreference_resolution_model import CoreferenceResult
from testmind.orchestration.nodes import coreference_resolution_node as module


class FakeModel:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.messages = None
        self.calls = 0

    def with_structured_output(self, schema):
        assert schema is CoreferenceResult
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.error:
            raise self.error
        return self.result


def make_state():
    return {
        "raw_query": "How does it run?",
        "corrected_query": "How does it run?",
        "messages": [
            HumanMessage(content="The pytest script is ready."),
            AIMessage(content="The script runs with pytest."),
            HumanMessage(content="How does it run?"),
        ],
    }


@pytest.mark.asyncio
async def test_resolves_without_duplicate_current_question(monkeypatch):
    result = CoreferenceResult(
        resolved_query="How does the pytest script run?",
        has_coreference=True,
        references=[{"expression": "it", "referent": "pytest script", "evidence": "The pytest script is ready."}],
        confidence=0.95,
    )
    fake = FakeModel(result=result)
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    state = make_state()
    updates = await module.CoreferenceResolutionNode().process(state)
    assert updates["resolved_query"] == result.resolved_query
    assert updates["coreference_detail"]["references"][0]["referent"] == "pytest script"
    assert "resolved_query" not in state
    human_contents = [message.content for message in fake.messages if message.type == "human"]
    assert len(human_contents) == 2
    assert human_contents[0] == "The pytest script is ready."
    assert human_contents[1].endswith("How does it run?")


@pytest.mark.asyncio
async def test_no_previous_history_preserves_question_without_model_call(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    state = make_state()
    state["messages"] = [state["messages"][-1]]
    updates = await module.CoreferenceResolutionNode().process(state)
    assert updates["resolved_query"] == "How does it run?"
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_uncertain_rewrite_preserves_question(monkeypatch):
    fake = FakeModel(result=CoreferenceResult(
        resolved_query="How does the unrelated agent run?",
        has_coreference=True, confidence=0.2,
    ))
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    updates = await module.CoreferenceResolutionNode().process(make_state())
    assert updates["resolved_query"] == "How does it run?"
    assert updates["coreference_detail"]["resolved_query"] == "How does it run?"


@pytest.mark.asyncio
async def test_model_failure_preserves_question(monkeypatch):
    fake = FakeModel(error=TimeoutError("unavailable"))
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    updates = await module.CoreferenceResolutionNode().process(make_state())
    assert updates["resolved_query"] == "How does it run?"
    assert updates["coreference_detail"]["references"] == []

@pytest.mark.asyncio
async def test_model_cannot_change_query_when_no_coreference(monkeypatch):
    fake = FakeModel(result=CoreferenceResult(
        resolved_query="An invented topic",
        has_coreference=False,
        confidence=0.95,
    ))
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    updates = await module.CoreferenceResolutionNode().process(make_state())
    assert updates["resolved_query"] == "How does it run?"


@pytest.mark.asyncio
async def test_empty_question_raises_validation_error(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: fake)
    state = make_state()
    state["corrected_query"] = "  "
    with pytest.raises(module.ValidationError, match="待消解问题不能为空"):
        await module.CoreferenceResolutionNode().process(state)
    assert fake.calls == 0
