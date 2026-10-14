"""
日志配置
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

# src/testmind/core/logging.py -> 项目根目录
_ROOT_DIR = Path(__file__).resolve().parents[3]
_LOG_DIR = _ROOT_DIR / "logs"
_LOG_FILE = _LOG_DIR / "testmind.log"

# 文件日志格式（纯文本；控制台配色交给 rich 处理）
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False


class DecisionAuditFormatter(logging.Formatter):
    """Render approved decision events as JSON while preserving other log lines."""

    _FIELDS = frozenset(
        {
            "event", "trace_id", "task_id", "attempt_id", "node", "project_id",
            "intent", "answer_mode", "outcome", "stop_reason", "error_code",
            "policy_decision", "tool_name", "model_version", "prompt_version",
            "schema_version", "duration_ms", "retry_count", "result_count",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        decision = getattr(record, "decision_event", None)
        if not isinstance(decision, dict):
            return super().format(record)
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        payload.update({key: value for key, value in decision.items() if key in self._FIELDS})
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

def setup_logging(level: int = logging.DEBUG) -> None:
    """
    配置根日志记录器: 控制台彩色 + 文件轮转

    Args:
        level (int, optional): 日志级别. Defaults to logging.DEBUG.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    # 确保日志目录存在
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # ---- 控制台 Handler：rich 自动配色 + 美化异常堆栈 ----
    console_handler = RichHandler(
        rich_tracebacks=True,
        markup=True,
        show_time=False,
        show_level=False,
        show_path=False,
    )
    console_handler.setLevel(level)
    console_handler.setFormatter(DecisionAuditFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    # ---- 文件 Handler（轮转，单文件 5MB，保留 3 份）----
    file_handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(DecisionAuditFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # asyncio 的 selector 调试信息对应用日志没有帮助，避免启动时刷屏。
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    for dependency in ("httpx", "httpcore", "langchain", "langgraph", "mcp", "dotenv"):
        logging.getLogger(dependency).setLevel(logging.WARNING)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """
    获取日志记录器。

    若尚未初始化会自动调用 setup_logging()。

    Args:
        name: 日志记录器名称，通常传 __name__
    Returns:
        日志记录器实例
    """
    if not _INITIALIZED:
        setup_logging()
    return logging.getLogger(name)


