"""Exercise actual local BGE models offline, with an in-memory Milvus stand-in."""

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from testmind.core.config import Config
from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService
from testmind.models.retrieval import RetrievalScope


class CaptureMilvus:
    dense_dim = 0
    sparse_terms = 0

    def hybrid_search(self, **kwargs):
        dense, sparse = kwargs["reqs"]
        self.dense_dim = len(dense.data[0])
        self.sparse_terms = len(sparse.data[0])
        return [
            [
                {
                    "entity": {
                        "chunk_id": ident,
                        "document_id": "smoke-doc",
                        "document_version": "v1",
                        "project_id": "local-smoke",
                        "title": "Synthetic model smoke test",
                        "content": content,
                        "source_locator": "synthetic://local-smoke",
                        "is_active": True,
                    }
                }
                for ident, content in (
                    ("login-rule", "登录密码连续输错五次后，账号将被锁定三十分钟。"),
                    ("unrelated-rule", "导出的测试报告采用 HTML 格式。"),
                )
            ]
        ]

    def close(self):
        pass


async def main():
    import torch

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    client = CaptureMilvus()
    config = Config()
    service = MilvusRetrievalService(config, client=client)
    scope = RetrievalScope(
        user_id="smoke", project_id="local-smoke", document_versions={"smoke-doc": "v1"}
    )
    result = await service.retrieve("密码连续输错几次会锁定账号？", scope)
    assert client.dense_dim == 1024 and client.sparse_terms > 0
    assert result[0].chunk_id == "login-rule" and result[0].relevance > result[1].relevance
    report = {
        "models": "real local BGE-M3 + BGE-Reranker",
        "milvus": "in-memory test double",
        "offline": True,
        "dense_dimensions": client.dense_dim,
        "sparse_terms": client.sparse_terms,
        "ranking": [{"chunk_id": item.chunk_id, "score": item.relevance} for item in result],
    }
    print(json.dumps(report, indent=2))
    await service.aclose()


if __name__ == "__main__":
    asyncio.run(main())
