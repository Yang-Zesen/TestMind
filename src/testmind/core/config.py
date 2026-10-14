from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

_ROOT_DIR = Path(__file__).resolve().parents[3]

if os.getenv("TESTMIND_DEMO_MODE", "false").lower() != "true":
    load_dotenv(_ROOT_DIR / ".env")


def _local_model_path(variable: str, name: str) -> str:
    explicit = os.getenv(variable, "").strip()
    if explicit:
        path = Path(explicit)
        return str(path if path.is_absolute() else _ROOT_DIR / path)
    return str(_ROOT_DIR / "pretrained_model" / name)

class Config(BaseModel):
    """
    管理整个项目的配置
    """

    # 服务配置
    app_host: str = Field(default_factory=lambda: os.getenv("APP_HOST", "127.0.0.1"), description="FastAPI 监听地址")
    app_port: int = Field(default_factory=lambda: int(os.getenv("APP_PORT", "26104")), ge=1, le=65535, description="FastAPI 监听端口")
    app_env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    demo_mode: bool = Field(default_factory=lambda: os.getenv("TESTMIND_DEMO_MODE", "false").lower() == "true")
    knowledge_auth_mode: str = Field(default_factory=lambda: os.getenv("KNOWLEDGE_AUTH_MODE", "disabled"))
    knowledge_mock_user_id: str = Field(default_factory=lambda: os.getenv("KNOWLEDGE_MOCK_USER_ID", "dev-user"))
    knowledge_mock_project_ids: str = Field(default_factory=lambda: os.getenv("KNOWLEDGE_MOCK_PROJECT_IDS", ""))
    knowledge_mock_base_ids: str = Field(default_factory=lambda: os.getenv("KNOWLEDGE_MOCK_BASE_IDS", ""))
    knowledge_source_dir: str = Field(default_factory=lambda: os.getenv("KNOWLEDGE_SOURCE_DIR", "data/knowledge-sources"))
    knowledge_storage_mode: str = Field(
        default_factory=lambda: os.getenv(
            "KNOWLEDGE_STORAGE_MODE",
            "filesystem" if os.getenv("APP_ENV", "development").lower() == "development" else "minio",
        )
    )
    minio_endpoint: str = Field(default_factory=lambda: os.getenv("MINIO_ENDPOINT", ""))
    minio_access_key: str = Field(default_factory=lambda: os.getenv("MINIO_ACCESS_KEY", ""), repr=False)
    minio_secret_key: str = Field(default_factory=lambda: os.getenv("MINIO_SECRET_KEY", ""), repr=False)
    minio_bucket: str = Field(default_factory=lambda: os.getenv("MINIO_BUCKET", "testmind-knowledge"))
    minio_secure: bool = Field(
        default_factory=lambda: os.getenv("MINIO_SECURE", "true").lower() in {"1", "true", "yes"}
    )
    knowledge_max_upload_bytes: int = Field(
        default_factory=lambda: int(os.getenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
        ge=1,
    )
    zentao_event_enabled: bool = Field(
        default_factory=lambda: os.getenv("ZENTAO_EVENT_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
    zentao_event_secret: str = Field(default_factory=lambda: os.getenv("ZENTAO_EVENT_SECRET", ""), repr=False)

    # 日志配置
    log_level: str = Field(default="DEBUG", description="日志级别")

    # 路径配置


    # LLM 相关配置
    api_key: str = Field(default=os.getenv("DEEPSEEK_API_KEY", ""), repr=False, description="Deepseek API Key")
    model: str = Field(default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), description="Deepseek 模型名称")
    base_url: str = Field(default=os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.cn/v1"), description="Deepseek API Base URL")

    # PostgreSql 相关配置
    postgres_dsn: str = Field(default_factory=lambda: os.getenv("POSTGRES_DSN", ""), repr=False, description="PostgreSQL DSN")

    # JEV 相关配置
    jev_api_key: str = Field(default=os.getenv("JEV_API_KEY", ""), repr=False, description="JEV API Key")
    jev_base_url: str = Field(default=os.getenv("JEV_BASE_URL", ""), repr=False, description="JEV Base URL")
    jev_model: str = Field(default=os.getenv("JEV_MODEL", ""), repr=False, description="JEV Model")

    # Retrieval 相关配置
    milvus_uri: str = Field(default_factory=lambda: os.getenv("MILVUS_URI", "http://127.0.0.1:19530"))
    milvus_token: str = Field(default_factory=lambda: os.getenv("MILVUS_TOKEN", ""), repr=False)
    milvus_collection: str = Field(default_factory=lambda: os.getenv("MILVUS_COLLECTION", "testmind_chunks"))
    milvus_dense_field: str = Field(default_factory=lambda: os.getenv("MILVUS_DENSE_FIELD", "dense_vector"))
    milvus_dense_metric: str = Field(default_factory=lambda: os.getenv("MILVUS_DENSE_METRIC", "IP"))
    milvus_sparse_field: str = Field(default_factory=lambda: os.getenv("MILVUS_SPARSE_FIELD", "sparse_vector"))
    milvus_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("MILVUS_TIMEOUT_SECONDS", "5")), gt=0
    )
    retrieval_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("RETRIEVAL_TIMEOUT_SECONDS", "60")), gt=0
    )
    retrieval_min_relevance: float = Field(
        default_factory=lambda: float(os.getenv("RETRIEVAL_MIN_RELEVANCE", "0.5")), ge=0, le=1
    )
    embedding_model: str = Field(default_factory=lambda: _local_model_path("BGE_M3_PATH", "bge-m3"))
    embedding_device: str = Field(default_factory=lambda: os.getenv("BGE_DEVICE", "cpu"))
    embedding_fp16: bool = Field(default_factory=lambda: os.getenv("BGE_FP16", "false").lower() in {"1", "true", "yes"})
    reranker_model: str = Field(default_factory=lambda: _local_model_path("BGE_RERANKER_BASE", "bge-reranker-base"))
    reranker_device: str = Field(default_factory=lambda: os.getenv("BGE_RERANKER_DEVICE", "cpu"))
    reranker_fp16: bool = Field(default_factory=lambda: os.getenv("BGE_RERANKER_FP16", "false").lower() in {"1", "true", "yes"})
    dialogue_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("DIALOGUE_TIMEOUT_SECONDS", "60")), gt=0
    )
    ai_node_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("AI_NODE_TIMEOUT_SECONDS", "60")),
        gt=0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def validate_knowledge_auth(self) -> Config:
        if self.knowledge_auth_mode not in {"disabled", "request_state", "mock", "demo"}:
            raise ValueError("KNOWLEDGE_AUTH_MODE must be disabled, request_state, mock, or demo")
        if (self.demo_mode or self.knowledge_auth_mode in {"mock", "demo"}) and self.app_env.lower() != "development":
            raise ValueError("Mock knowledge authorization is allowed only when APP_ENV=development")
        if self.knowledge_storage_mode not in {"filesystem", "minio"}:
            raise ValueError("KNOWLEDGE_STORAGE_MODE must be filesystem or minio")
        if self.knowledge_storage_mode == "filesystem" and self.app_env.lower() != "development":
            raise ValueError("Filesystem knowledge storage is allowed only in development")
        if self.knowledge_storage_mode == "minio" and not all(
            (self.minio_endpoint, self.minio_access_key, self.minio_secret_key, self.minio_bucket)
        ):
            raise ValueError("MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, and MINIO_BUCKET are required")
        return self

    @classmethod
    def from_env(cls) -> Config:
        """通过类构建对象, 从环境变量中获取配置"""
        return cls()

_CONFIG: Config | None = None

def get_config() -> Config:
    """获取配置实例"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config.from_env()
    return _CONFIG
