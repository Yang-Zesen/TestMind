import json

import httpx
import pytest

from testmind.infrastructure.jev.jev_client import JevClient, JevClientError


@pytest.mark.asyncio
async def test_classify_posts_documented_payload_and_idempotency_key():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, json={"answers": {"intent": {"choice": "general_chat"}}})

    transport = httpx.MockTransport(handler)
    async with JevClient(
        api_key="test-key",
        base_url="https://jevmodel.org",
        model="jev-latest",
        http_client=httpx.AsyncClient(transport=transport, base_url="https://jevmodel.org"),
    ) as client:
        result = await client.classify(
            state="请解释 pytest fixture",
            questions={
                "intent": {
                    "type": "choice",
                    "instructions": "判断用户意图",
                    "criteria": {"general_chat": "普通技术问答"},
                }
            },
            idempotency_key="trace-001-intent-0",
        )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://jevmodel.org/v1/systemone"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["idempotency-key"] == "trace-001-intent-0"
    assert json.loads(captured["body"]) == {
        "model": "jev-latest",
        "state": "请解释 pytest fixture",
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "判断用户意图",
                "criteria": {"general_chat": "普通技术问答"},
            }
        },
    }
    assert result["answers"]["intent"]["choice"] == "general_chat"


@pytest.mark.asyncio
async def test_classify_rejects_invalid_question_definition():
    async with JevClient(api_key="test-key", base_url="https://jevmodel.org") as client:
        with pytest.raises(ValueError, match="1 到 8"):
            await client.classify("问题", {})


@pytest.mark.asyncio
async def test_classify_wraps_http_errors_with_response_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    async with JevClient(
        api_key="test-key",
        base_url="https://jevmodel.org",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://jevmodel.org"
        ),
    ) as client:
        with pytest.raises(JevClientError, match="401") as error:
            await client.classify(
                "问题",
                {
                    "intent": {
                        "type": "choice",
                        "instructions": "判断意图",
                        "criteria": {"general_chat": "普通问答"},
                    }
                },
            )

    assert error.value.status_code == 401
    assert "invalid key" in error.value.response_body
