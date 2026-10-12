from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from testmind.core.errors import ValidationError
from testmind.infrastructure.jev.jev_client import JevClient, JevClientError
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState

_ALLOWED_INTENTS = frozenset(
    {
        "dialogue",
        "rag_query",
        "test_case_generation",
        "script_generation",
        "graph_query",
        "general_chat",
        "clarify",
    }
)
_MIN_CONFIDENCE = 0.60

_INTENT_QUESTION = {
    "intent": {
        "type": "choice",
        "instructions": "判断用户当前最主要的测试平台操作意图，只选择一个类别。",
        "criteria": {
            "dialogue": "通用对话、技术解释或查询项目资料；由 answer_mode 决定是否检索",
            "test_case_generation": "生成、完善或评审测试用例",
            "script_generation": "生成、修改或执行自动化测试脚本",
            "graph_query": "查询项目进度、用例统计、自动化率或执行结果",
            "clarify": "信息不足，无法判断用户真实意图",
        },
    },
    "answer_mode": {
        "type": "choice",
        "instructions": "与 intent 在本次请求中一起判断回答策略。项目资料、历史缺陷、项目规则必须检索；普通技术解释直接回答。非 dialogue 选 not_applicable。",
        "criteria": {
            "direct": "普通技术问答或聊天，不依赖特定项目事实",
            "retrieval": "需要项目文档、历史缺陷、规范或环境资料作为证据",
            "not_applicable": "intent 不是 dialogue",
        },
    },
}


class IntentRecognitionNode(BaseNode):
    """Call JEV and write a normalized intent decision into GraphState."""

    name = "intent_recognition_node"

    def __init__(self, jev_client: JevClient) -> None:
        super().__init__()
        self._jev_client = jev_client

    def _build_jev_state(self, state: GraphState) -> str:
        """Build the bounded context sent to JEV instead of exposing all GraphState."""
        query = (
            state.get("resolved_query")
            or state.get("corrected_query")
            or state.get("raw_query")
            or ""
        ).strip()
        if not query:
            raise ValidationError(message="待识别的问题不能为空", node_name=self.name)

        messages = state.get("messages", [])[-6:]
        history = "\n".join(f"{message.type}: {message.content}" for message in messages)[-1500:]
        return (
            f"当前用户问题：{query[:6000]}\n"
            f"项目编号：{state.get('project_id', '')[:200]}\n最近对话：\n{history}"
        )

    @staticmethod
    def _extract_intent_answer(response: Mapping[str, Any]) -> tuple[str, float, str, str | None]:
        answers = response.get("answers")
        if not isinstance(answers, Mapping):
            raise TypeError("JEV 响应缺少 answers 对象")

        answer = answers.get("intent")
        if not isinstance(answer, Mapping):
            raise TypeError("JEV 响应缺少 answers.intent 对象")

        intent = answer.get("choice")
        if not isinstance(intent, str) or intent not in _ALLOWED_INTENTS:
            raise ValueError(f"JEV 返回了未知意图：{intent!r}")

        raw_confidence = answer.get("confidence", 0.0)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("JEV 返回的 confidence 不是数字") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("JEV 返回的 confidence 必须在 0 到 1 之间")

        explanation = answer.get("explanation", "")
        if not isinstance(explanation, str):
            explanation = ""
        mode = None
        # Accept older JEV fixtures while exposing one normalized dialogue contract.
        if intent in {"general_chat", "rag_query"}:
            mode = "direct" if intent == "general_chat" else "retrieval"
            intent = "dialogue"
        elif intent == "dialogue":
            strategy = answers.get("answer_mode")
            if not isinstance(strategy, Mapping) or strategy.get("choice") not in {
                "direct",
                "retrieval",
            }:
                raise ValueError("问答缺少有效 answer_mode")
            mode_confidence = float(strategy.get("confidence", 0))
            if not math.isfinite(mode_confidence) or not 0 <= mode_confidence <= 1:
                raise ValueError("answer_mode 置信度无效")
            confidence = min(confidence, mode_confidence)
            mode = strategy["choice"]
        return intent, confidence, explanation.strip(), mode

    def _fallback(
        self,
        reason: str,
        *,
        status_code: int | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"node": self.name, "message": reason}
        if status_code is not None:
            error["status_code"] = status_code
        return {
            "intent": "clarify",
            "answer_mode": None,
            "citations": [],
            "insufficient_evidence": False,
            "intent_confidence": 0.0,
            "route_reason": reason,
            "current_stage": "intent_recognition",
            "last_error": error,
        }

    async def process(self, state: GraphState) -> dict[str, Any]:
        jev_state = self._build_jev_state(state)
        idempotency_key = f"{state.get('trace_id', 'unknown')}-intent-{state.get('retry_count', 0)}"

        try:
            response = await self._jev_client.classify(
                state=jev_state,
                questions=_INTENT_QUESTION,
                idempotency_key=idempotency_key,
            )
        except JevClientError as exc:
            self._logger.error("JEV 意图识别失败：%s", exc)
            return self._fallback(str(exc), status_code=exc.status_code)

        try:
            intent, confidence, explanation, answer_mode = self._extract_intent_answer(response)
        except (TypeError, ValueError) as exc:
            self._logger.error("JEV 响应格式无效：%s", exc)
            return self._fallback(str(exc))

        if confidence < _MIN_CONFIDENCE:
            return {
                "intent": "clarify",
                "answer_mode": None,
                "citations": [],
                "insufficient_evidence": False,
                "intent_confidence": confidence,
                "route_reason": f"JEV 意图置信度为 {confidence:.2f}，低于阈值 {_MIN_CONFIDENCE:.2f}",
                "current_stage": "intent_recognition",
                "last_error": {},
            }

        return {
            "intent": intent,
            "answer_mode": answer_mode,
            "citations": [],
            "insufficient_evidence": False,
            "intent_confidence": confidence,
            "route_reason": explanation or f"JEV 判断结果：{intent}",
            "current_stage": "intent_recognition",
            "last_error": {},
        }
