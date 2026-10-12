"""
1. 定义统一异常处理逻辑, 便于后续维护
2. 定义各种需要的异常类, 便于后续扩展
"""


class ProcessError(Exception):
    """基础异常"""
    def __init__(
        self,
        message: str,
        node_name: str = "",
        cuase: Exception | None = None,
    ) -> None:
        super().__init__(message, cuase)
        self._node_name = node_name
        self._cause = cuase

    def __str__(self) -> str:
        _parts = []
        if self._node_name:
            _parts.append(f"Node: {self._node_name}") # 节点信息
        _parts.append(super().__str__()) # message
        if self._cause:
            _parts.append(f"Cause: {self._cause}") # 原因异常
        return "\n".join(_parts)
    
class ValidationError(ProcessError):
    """验证异常"""
    ...
    
