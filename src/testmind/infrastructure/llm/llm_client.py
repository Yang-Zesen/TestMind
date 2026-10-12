from langchain.chat_models import init_chat_model
from testmind.core.config import get_config
from langchain_core.language_models import BaseChatModel

from testmind.core.logging import get_logger

_CONFIG = get_config()
_LOGGER = get_logger(__name__)
_CACHE_LLM_CLIENT = {}


def get_llm_client(
    model_name: str = _CONFIG.model,
    temperature: float = 0.2,
    response_format: bool = True,
) -> BaseChatModel:
    """获取 LLM 客户端"""

    _model_names: str = model_name.strip() or _CONFIG.model
    _api_key: str = _CONFIG.api_key.strip() or ""
    _base_url: str = _CONFIG.base_url.strip() or ""
    _cache_key: tuple[str, bool] = (_model_names, response_format)

    # 如果命中缓存, 直接返回
    if _cache_key in _CACHE_LLM_CLIENT:
        return _CACHE_LLM_CLIENT[_cache_key]
    
    # 返回的内容格式
    # 如果需要返回json格式, 需要设置response_format为True
    # 否则, 返回的字符串会包含json格式的错误信息
    _model_kwargs: dict[str, dict] | None = {}
    if response_format:
        _model_kwargs["response_format"] = {"type": "json_object"}
    try:
        _client: BaseChatModel = init_chat_model(
            model = _model_names,
            api_key = _api_key,
            base_url = _base_url,
            temperature = temperature,
            extra_body = {"enable_thinking": False},
            model_kwargs = _model_kwargs,
        )

        return _client
    except Exception as e:
        _LOGGER.error(f"初始化 LLM 客户端失败: {e}")
        raise e
