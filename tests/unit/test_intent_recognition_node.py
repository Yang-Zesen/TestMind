import pytest

from testmind.infrastructure.jev.jev_client import JevClientError
from testmind.orchestration.nodes.intent_recognition_node import IntentRecognitionNode


class FakeJevClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def make_state(**overrides):
    state = {
        "raw_query": "这个项目怎么查历史缺陷？",
        "corrected_query": "这个项目怎么查历史缺陷？",
        "resolved_query": "如何查询这个项目的历史缺陷？",
        "messages": [],
        "project_id": "project-1",
        "trace_id": "trace-1",
        "retry_count": 0,
        "entities": {},
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_process_writes_valid_intent_and_confidence():
    client = FakeJevClient(
        response={
            "answers": {
                "intent": {
                    "choice": "rag_query",
                    "confidence": 0.93,
                    "explanation": "用户正在查询历史缺陷",
                }
            }
        }
    )
    updates = await IntentRecognitionNode(client).process(make_state())

    assert updates["intent"] == "dialogue"
    assert updates["answer_mode"] == "retrieval"
    assert updates["intent_confidence"] == 0.93
    assert updates["route_reason"] == "用户正在查询历史缺陷"
    assert updates["current_stage"] == "intent_recognition"
    assert client.calls[0]["idempotency_key"] == "trace-1-intent-0"
    assert "如何查询这个项目的历史缺陷？" in client.calls[0]["state"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "retrieval"])
async def test_dialogue_and_answer_strategy_are_classified_in_one_request(mode):
    client = FakeJevClient(
        response={
            "answers": {
                "intent": {"choice": "dialogue", "confidence": 0.95},
                "answer_mode": {"choice": mode, "confidence": 0.92},
            }
        }
    )
    result = await IntentRecognitionNode(client).process(make_state())
    assert result["intent"] == "dialogue" and result["answer_mode"] == mode
    assert len(client.calls) == 1
    assert set(client.calls[0]["questions"]) == {"intent", "answer_mode"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [None, {"choice": "unknown", "confidence": 0.9}, {"choice": "retrieval", "confidence": 0.1}],
)
async def test_missing_or_uncertain_answer_mode_clarifies_without_reclassifying(mode):
    response = {"answers": {"intent": {"choice": "dialogue", "confidence": 0.95}}}
    if mode is not None:
        response["answers"]["answer_mode"] = mode
    client = FakeJevClient(response=response)
    result = await IntentRecognitionNode(client).process(make_state(answer_mode="direct"))
    assert result["intent"] == "clarify" and result["answer_mode"] is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_low_confidence_routes_to_clarify():
    client = FakeJevClient(
        response={"answers": {"intent": {"choice": "script_generation", "confidence": 0.4}}}
    )

    updates = await IntentRecognitionNode(client).process(make_state())

    assert updates["intent"] == "clarify"
    assert updates["intent_confidence"] == 0.4
    assert "置信度" in updates["route_reason"]


@pytest.mark.asyncio
async def test_malformed_response_falls_back_to_clarify():
    client = FakeJevClient(response={"answers": {}})

    updates = await IntentRecognitionNode(client).process(make_state())

    assert updates["intent"] == "clarify"
    assert updates["intent_confidence"] == 0.0
    assert updates["last_error"]["node"] == "intent_recognition_node"


@pytest.mark.asyncio
async def test_jev_failure_falls_back_to_clarify():
    client = FakeJevClient(error=JevClientError("service unavailable", status_code=503))

    updates = await IntentRecognitionNode(client).process(make_state())

    assert updates["intent"] == "clarify"
    assert updates["intent_confidence"] == 0.0
    assert updates["last_error"]["status_code"] == 503


@pytest.mark.asyncio
async def test_missing_query_is_rejected():
    from testmind.core.errors import ValidationError

    client = FakeJevClient(response={})

    with pytest.raises(ValidationError):
        await IntentRecognitionNode(client).process(
            make_state(resolved_query="", corrected_query="", raw_query="")
        )


def test_classifier_input_stays_within_jev_budget_for_long_chat_history():
    from langchain_core.messages import HumanMessage

    node = IntentRecognitionNode(FakeJevClient())
    history = [HumanMessage(content="历史问题" * 1000) for _ in range(6)]
    prompt = node._build_jev_state(make_state(resolved_query="项目问题" * 1500, messages=history))
    assert len(prompt) <= 8000
    assert "项目问题" in prompt
