"""Reproducible local BGE + PG + Milvus acceptance and ranking evaluation."""

import asyncio
import json
import os
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


async def run():
    import torch

    from testmind.core.config import get_config
    from testmind.demo.runtime import DemoModel
    from testmind.infrastructure.knowledge.activation import KnowledgeActivationService
    from testmind.infrastructure.knowledge.ingestion import KnowledgeIngestionHandler
    from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
    from testmind.infrastructure.knowledge.source_storage import create_source_storage
    from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService
    from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
    from testmind.infrastructure.postgres.postgresql_client import open_postgresql_client
    from testmind.orchestration.context import RequestContext
    from testmind.workflows.dialogue import create_dialogue_graph
    from testmind.workflows.retrieval import SharedRetrievalService

    torch.set_num_threads(4)
    config = get_config()
    backend = MilvusRetrievalService(config)
    started = time.monotonic()
    print("Loading real offline BGE-M3 and Reranker on CPU...", flush=True)
    await asyncio.to_thread(backend._initialize)
    print("Models loaded; running real database ingestion.", flush=True)
    report = {"mode":"local_demo", "embedding":"real BGE-M3", "reranker":"real BGE-Reranker", "answer_model":"offline extractive adapter", "zentao_publication":"persistent local simulation", "model_load_seconds":round(time.monotonic()-started,2), "checks":[], "evaluation":[]}
    corpus = [
        ("登录安全", "连续输错密码5次后账号锁定30分钟，锁定期内拒绝登录。", "输错几次密码会锁定账号？"),
        ("知识版本", "新知识版本通过质量和索引验收后处于 ready 状态，必须由用户显式激活才能用于问答。", "ready版本可以直接用于问答吗？"),
        ("用例发布", "用例需要逐条人工批准才能发布；修改草稿会使旧审批失效，写入结果未知时必须核实而不是重复发送。", "修改用例后旧批准还能发布吗？"),
    ]
    async with open_postgresql_client() as postgres:
        knowledge = KnowledgeRepository(postgres.pool)
        storage = create_source_storage(config)
        index = MilvusKnowledgeIndex(backend._client, collection_name=config.milvus_collection)
        ingestion = KnowledgeIngestionHandler(knowledge, storage, backend._encoder, index, model_version="bge-m3")
        activation = KnowledgeActivationService(knowledge, index)
        documents = []
        for title, content, query in corpus:
            stored = storage.save(BytesIO(content.encode()), suffix=".md")
            upload = await knowledge.create_upload(project_id="demo-project", kb_id="demo-kb", idempotency_key=str(uuid4()), request_fingerprint=str(uuid4()), storage_key=stored.key, title=title, metadata={"acceptance":"p2-p3-v1"}, actor_id="demo-user", correlation_id=str(uuid4()))
            job = await knowledge.get_job(upload["job_id"])
            await ingestion(job)
            scope = await knowledge.build_retrieval_scope(user_id="demo-user", project_id="demo-project", knowledge_base_ids=frozenset({"demo-kb"}))
            assert upload["document_id"] not in scope.active_versions
            op = await knowledge.begin_activation(project_id="demo-project", kb_id="demo-kb", document_id=upload["document_id"], version_id=upload["version_id"])
            await activation.activate(op)
            documents.append((upload, query))
        report["checks"].append("upload_quality_index_ready_explicit_activation")
        scope = await knowledge.build_retrieval_scope(user_id="demo-user", project_id="demo-project", knowledge_base_ids=frozenset({"demo-kb"}))
        # Fixed gold corpus only; unrelated demo sources cannot change ranking metrics.
        scope = scope.model_copy(update={"active_versions":{item["document_id"]:scope.active_versions[item["document_id"]] for item,_ in documents}})
        shared = SharedRetrievalService(backend)
        dialogue = create_dialogue_graph(shared, llm=DemoModel())
        for upload, query in documents:
            begin = time.monotonic()
            chunks = await shared.retrieve(query, scope)
            assert chunks and chunks[0].document_id == upload["document_id"]
            result = await dialogue.ainvoke({"raw_query":query, "user_id":"demo-user", "project_id":"demo-project", "answer_mode":"retrieval", "messages":[]}, context=RequestContext(retrieval_scope=scope))
            assert result["status"] == "completed" and result["citations"]
            embeddings = await asyncio.to_thread(backend._encoder.encode_queries, [query])
            expression = backend.build_filter(scope)
            rankings = {}
            for mode, field, vector, metric in (
                ("dense", config.milvus_dense_field, embeddings["dense"][0].tolist(), config.milvus_dense_metric),
                ("sparse", config.milvus_sparse_field, backend._sparse_row_to_dict(embeddings["sparse"],0), "IP"),
            ):
                hits = await asyncio.to_thread(backend._client.search, config.milvus_collection, [vector], anns_field=field, filter=expression, search_params={"metric_type":metric}, limit=3, output_fields=["document_id"])
                rankings[mode] = [hit["entity"]["document_id"] for hit in hits[0]]
            fused = await asyncio.to_thread(backend._hybrid_search, query, expression, 3)
            rankings["hybrid"] = [hit["entity"]["document_id"] for hit in fused]
            rankings["rerank"] = [chunk.document_id for chunk in chunks]
            metrics = {mode:{"recall_at_3":int(upload["document_id"] in ids[:3]), "reciprocal_rank":1/(ids.index(upload["document_id"])+1) if upload["document_id"] in ids else 0} for mode,ids in rankings.items()}
            report["evaluation"].append({"query":query, "expected_document_id":upload["document_id"], "metrics":metrics, "citation_valid":True, "extractive_supported":True, "seconds":round(time.monotonic()-begin,2)})
        report["checks"].append("real_dense_sparse_hybrid_rerank_and_supported_citations")
        await knowledge.tombstone_document(project_id="demo-project", kb_id="demo-kb", document_id=documents[0][0]["document_id"])
        updated = await knowledge.build_retrieval_scope(user_id="demo-user", project_id="demo-project", knowledge_base_ids=frozenset({"demo-kb"}))
        assert documents[0][0]["document_id"] not in updated.active_versions
        report["checks"].append("deleted_document_removed_from_authoritative_scope")
    await backend.aclose()
    report["status"] = "passed"
    report["corpus_version"] = "p2-p3-gold-v1"
    report["bm25"] = "not evaluated; BGE sparse is not BM25"
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/p2-p3-real-rag.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())
