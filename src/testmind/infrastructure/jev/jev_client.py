"""Async HTTP client for the JEV System One evaluation endpoint."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Self

import httpx

from testmind.core.config import get_config

_MAX_STATE_LENGTH = 8_000
_MAX_QUESTIONS = 8
_MAX_INSTRUCTIONS_LENGTH = 1_800
_MAX_IDEMPOTENCY_KEY_LENGTH = 100
_QUESTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_QUESTION_TYPES = {"choice", "noul", "score"}


class JevClientError(RuntimeError):
    """A request or response error returned by the JEV client."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class JevClient:
    """Application-scoped asynchronous client for ``POST /v1/systemone``."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://jevmodel.org",
        model: str = "jev-latest",
        timeout: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("JEV_API_KEY 不能为空")
        if not base_url.strip():
            raise ValueError("JEV_BASE_URL 不能为空")
        if timeout <= 0:
            raise ValueError("JEV 请求超时时间必须大于 0")

        self._api_key = api_key.strip()
        self._model = model.strip() or "jev-latest"
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )
        self._owns_client = http_client is None

    @classmethod
    def from_config(cls) -> JevClient:
        """Build one client from the project's environment-backed configuration."""
        config = get_config()
        return cls(
            api_key=config.jev_api_key,
            base_url=config.jev_base_url or "https://jevmodel.org",
            model=config.jev_model or "jev-latest",
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection when this client owns it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def classify(
        self,
        state: str | Mapping[str, Any] | list[Any],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate a state against JEV questions and return the raw response."""
        serialized_state = self._serialize_state(state)
        normalized_questions = self._validate_questions(questions)
        normalized_key = self._validate_idempotency_key(idempotency_key)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if normalized_key:
            headers["Idempotency-Key"] = normalized_key

        try:
            response = await self._client.post(
                "/v1/systemone",
                headers=headers,
                json={
                    "model": self._model,
                    "state": serialized_state,
                    "questions": normalized_questions,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:2_000]
            raise JevClientError(
                f"JEV 请求失败，HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
                response_body=body,
            ) from exc
        except httpx.RequestError as exc:
            raise JevClientError(f"JEV 网络请求失败：{exc}") from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise JevClientError("JEV 返回的内容不是合法 JSON", response_body=response.text[:2_000]) from exc

        if not isinstance(result, dict):
            raise JevClientError("JEV 返回结果必须是 JSON 对象")
        return result

    @staticmethod
    def _serialize_state(state: str | Mapping[str, Any] | list[Any]) -> str:
        if isinstance(state, str):
            serialized = state
        elif isinstance(state, (Mapping, list)):
            try:
                serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise ValueError("JEV state 必须可以序列化为 JSON") from exc
        else:
            raise TypeError("JEV state 只能是字符串、对象或数组")

        if not serialized.strip():
            raise ValueError("JEV state 不能为空")
        if len(serialized) > _MAX_STATE_LENGTH:
            raise ValueError(f"JEV state 不能超过 {_MAX_STATE_LENGTH} 个字符")
        return serialized

    @staticmethod
    def _validate_questions(
        questions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(questions, Mapping) or not 1 <= len(questions) <= _MAX_QUESTIONS:
            raise ValueError("JEV questions 数量必须在 1 到 8 题之间")

        normalized: dict[str, dict[str, Any]] = {}
        for name, definition in questions.items():
            if not isinstance(name, str) or not _QUESTION_NAME_PATTERN.fullmatch(name):
                raise ValueError(f"JEV question 名称非法：{name!r}")
            if not isinstance(definition, Mapping):
                raise TypeError(f"JEV question {name!r} 必须是对象")

            question_type = definition.get("type")
            if question_type not in _QUESTION_TYPES:
                raise ValueError(f"JEV question {name!r} 的 type 必须是 choice、noul 或 score")

            instructions = definition.get("instructions")
            if not isinstance(instructions, str) or not instructions.strip():
                raise ValueError(f"JEV question {name!r} 缺少 instructions")
            if len(instructions) > _MAX_INSTRUCTIONS_LENGTH:
                raise ValueError(
                    f"JEV question {name!r} 的 instructions 不能超过 {_MAX_INSTRUCTIONS_LENGTH} 个字符"
                )

            criteria = definition.get("criteria")
            if question_type == "choice" and not isinstance(criteria, Mapping):
                raise ValueError(f"JEV choice question {name!r} 必须提供 criteria 对象")
            if question_type == "score" and not isinstance(criteria, list):
                raise ValueError(f"JEV score question {name!r} 必须提供 criteria 数组")

            normalized[name] = dict(definition)
        return normalized

    @staticmethod
    def _validate_idempotency_key(key: str | None) -> str | None:
        if key is None:
            return None
        if not key.strip():
            raise ValueError("Idempotency-Key 不能为空字符串")
        if len(key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError("Idempotency-Key 不能超过 100 个字符")
        return key
