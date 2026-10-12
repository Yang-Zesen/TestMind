"""
定义各个节点中需要的一些共性能力
1. 日志的输出
2. 配置信息
3. 异常处理过程
"""
from abc import ABC, abstractmethod
from typing import Optional, TypeVar

from testmind.core.config import Config, get_config
from testmind.core.logging import get_logger
from testmind.core.errors import ProcessError
from testmind.orchestration.state import GraphState


class BaseNode(ABC):
    """
    基础节点类
    定义了节点的基本属性和方法
    """
    name: str = "base_node"

    def __init__(self, config: Optional[Config] | None = None) -> None:
        self._config = config or get_config()
        self._logger = get_logger(self.name)

    async def __call__(self, state: GraphState) -> GraphState:
        self._logger.info("%s 开始执行", self.name)
        try:
            updates = await self.process(state)
        except ProcessError:
            raise
        except Exception:
            self._logger.exception("%s 执行异常", self.name)
            raise

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
        msg = f"[{step_name}]"
        if step_description:
            msg += f" - {step_description}"
        self._logger.debug(f"---{self.name} 步骤: {msg}---")
