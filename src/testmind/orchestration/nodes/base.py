"""
定义各个节点中需要的一些共性能力
1. 日志的输出
2. 配置信息
3. 异常处理过程
"""
import asyncio
import json
from abc import ABC, abstractmethod
from time import monotonic

from testmind.core.config import Config, get_config
from testmind.core.decision_audit import emit_decision_event
from testmind.core.errors import ProcessError, ValidationError
from testmind.core.logging import get_logger
from testmind.orchestration.state import GraphState


class BaseNode(ABC):
    """
    基础节点类
    定义了节点的基本属性和方法
    """
    name: str = "base_node"

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or get_config()
        self._logger = get_logger(self.name)

    async def __call__(self, state: GraphState) -> GraphState:
        started = monotonic()
        audit_context = {
            "trace_id": state.get("trace_id"),
            "task_id": state.get("task_id"),
            "attempt_id": state.get("attempt_id", state.get("retry_count")),
            "node": self.name,
            "project_id": state.get("project_id"),
            "intent": state.get("intent"),
            "answer_mode": state.get("answer_mode"),
        }
        emit_decision_event(self._logger, "ai_node_started", **audit_context)
        self._logger.info("%s 开始执行", self.name)
        deadline = asyncio.timeout(self._config.ai_node_timeout_seconds)
        try:
            async with deadline:
                for key in ("raw_query", "corrected_query", "resolved_query"):
                    value = state.get(key, "")
                    if not isinstance(value, str) or len(value) > 6000:
                        raise ValidationError("invalid_node_input", node_name=self.name)
                if len(json.dumps(state, default=str, ensure_ascii=False)) > 64000:
                    raise ValidationError("node_context_budget_exhausted", node_name=self.name)
                updates = await self.process(state)
                if not isinstance(updates, dict) or len(json.dumps(updates, default=str, ensure_ascii=False)) > 32000:
                    raise ValidationError("invalid_node_output", node_name=self.name)
                if "answer" in updates and (not isinstance(updates["answer"], str) or len(updates["answer"]) > 12000):
                    raise ValidationError("invalid_node_answer", node_name=self.name)
            if deadline.expired():
                raise TimeoutError
        except TimeoutError:
            if not deadline.expired():
                emit_decision_event(
                    self._logger,
                    "ai_node_stopped",
                    **audit_context,
                    outcome="failed",
                    stop_reason="exception",
                    error_code="TimeoutError",
                    duration_ms=round((monotonic() - started) * 1000),
                )
                self._logger.error("%s 执行异常 (TimeoutError)", self.name)
                raise
            emit_decision_event(
                self._logger,
                "ai_node_stopped",
                **audit_context,
                outcome="failed",
                stop_reason="timeout",
                error_code="ai_node_timeout",
                duration_ms=round((monotonic() - started) * 1000),
            )
            raise
        except asyncio.CancelledError:
            emit_decision_event(
                self._logger,
                "ai_node_stopped",
                **audit_context,
                outcome="cancelled",
                stop_reason="cancelled",
                error_code="task_cancelled",
                duration_ms=round((monotonic() - started) * 1000),
            )
            raise
        except ProcessError as exc:
            emit_decision_event(
                self._logger,
                "ai_node_stopped",
                **audit_context,
                outcome="failed",
                stop_reason="validation_or_processing_error",
                error_code=type(exc).__name__,
                duration_ms=round((monotonic() - started) * 1000),
            )
            raise
        except Exception as exc:
            emit_decision_event(
                self._logger,
                "ai_node_stopped",
                **audit_context,
                outcome="failed",
                stop_reason="exception",
                error_code=type(exc).__name__,
                duration_ms=round((monotonic() - started) * 1000),
            )
            self._logger.error("%s 执行异常 (%s)", self.name, type(exc).__name__)
            raise

        outcome = updates.get("status", "completed")
        stop_reasons = {
            "completed": "completed",
            "needs_input": "needs_input",
            "waiting_review": "waiting_review",
            "insufficient_evidence": "no_usable_evidence",
            "failed": "failed",
            "denied": "permission_denied",
        }
        last_error = updates.get("last_error", {})
        error_code = last_error.get("code") if isinstance(last_error, dict) else None
        emit_decision_event(
            self._logger,
            "ai_node_stopped",
            **audit_context,
            outcome=outcome,
            stop_reason=stop_reasons.get(outcome, "completed") if isinstance(outcome, str) else "completed",
            error_code=error_code,
            duration_ms=round((monotonic() - started) * 1000),
        )
        self._logger.info("%s 执行完成", self.name)
        return updates

    @abstractmethod
    async def process(self, state: GraphState) -> GraphState:
        ...

    def log_step(self, step_name: str, step_description: str) -> None:
        """
        记录节点执行的步骤（日志为同步操作，无需 async）
        Args:
            step_name: 步骤名称
            step_description: 步骤描述
        """
        self._logger.debug(
            "---%s 步骤: [%s] description_length=%d---",
            self.name,
            step_name,
            len(step_description),
        )
