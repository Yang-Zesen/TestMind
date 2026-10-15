# 全流程节点实现状态核对

## 2026-10-14 P5/P6 更新

P0～P6 本地演示切片已完成并按 SOP 验证：P5 主图路由/未启用终态、澄清恢复、历史结果复核和业务到图谱事务 Outbox 已接通；P6 隔离 Compose、迁移/模型指纹、深度就绪、Worker 心跳、运维状态、单机有界队列、容量及 PG 还原后 Milvus/Neo4j 投影重建均已实测。固定检索集为 3/4（一个问题按证据不足拒答）；性能数据只覆盖本机 PG 任务受理，不含模型推理。真实身份、真实禅道与生产质量/容量仍未验收。详见 `../SOP/P5-P6-本地演示验收-2026-10-14.md`。

## 2026-10-14 P2/P3 更新

主图新增独立绿色本地演示范围，保持生产目标节点的历史标色；不将模拟禅道与规则模板标为生产完成。共享 RetrievalState/EvidenceBundle、真实 BGE/Milvus、知识 Worker 有界解析与显式激活、来源轮询/水位/source_sync、需求/OpenAPI 草稿、逐条版本审批、模拟发布及未知结果核实已接入。下面 2026-10-09 清单为历史记录，其中“解析进程超时未落地”“source_sync 未装配”“用例分支未接入”等被本段覆盖。生产身份、真实禅道源与写入、OCR/图片、物理回收、P4-P6 仍未声明完成。最新命令和证据见 `../SOP/P2-P3-本地演示验收-2026-10-14.md`。

核对日期：2026-10-09。依据当前实际代码、运行时装配和单元测试，不以文件存在或设计文档代替实现。主图输入链路为拼写纠错 → 意图识别 → dialogue 分支指代消解 → 问答；其他 intent 在进入对话上下文模型前分流。

## 本次修订

- 补绿 4 个漏标节点：r1 上传、r2 授权去重与登记、r5 切块、r6 本地 Embedding。
- 原件存储与解析仍是部分实现：图片、OCR 和解析进程资源隔离尚未落地。
- 禅道知识事件已有验签与登记，但 source_sync 未装配 Worker handler，不代表自动入库闭环。
- 图谱 Webhook / 标准网关分页已实现，不代表原生禅道适配或独立消息队列已实现。
- 手动关系对账 / 重建已实现；定期调度、生产告警、旧代际 GC 仍待实现。
- 图入口、当前资产指标、TestMind 事件 API、权限撤销复核属于部分能力；主图自然语言查询、历史指标、业务自动发事件及 IAM 自动同步尚未实现。
- r15 的绿色限于更新、逻辑删除与 reindex；物理源文件 / 向量回收执行器尚未装配。

## 图中未独立成节点的已有能力

- 质量准入：`src/testmind/infrastructure/knowledge/quality.py` / `ingestion.py`，error 导致 rejected，warning 不阻断。
- 索引验收：`milvus_index.py` / `save_index_report`，inactive upsert、flush、Strong 确认，验收通过变 ready。
- 显式激活：`activation.py` / `begin_activation` / `switch_active_version`，异步作业、PG 权威映射、旧标志补偿。
- 对应测试：`test_knowledge_document_pipeline.py`、`test_knowledge_ingestion_handler.py`、`test_knowledge_milvus_index.py`、`test_knowledge_activation.py`。
- 质量 / 索引 / 激活代码与图内 r7 / r8 的阶段描述对应；它们未新增为独立图形节点。
- 当前问答链路：拼写纠错 → 意图与回答策略识别 → intent 路由；仅 dialogue 执行指代消解再进入问答子图，其他意图跳过该模型。

## 标色标准

浅绿色表示该节点明确描述的代码能力已存在，不表示真实数据库、真实账号或生产部署验收通过。混合节点未整体完成时保持非绿色，并标注已有与待实现范围。容器、图例、路由标签、开始 / 结束及说明文字不作为待开发节点计数。

## 逐节点清单

| ID | 节点 | 状态 | 代码 / 测试依据 |
| --- | --- | --- | --- |
| `sync-register` | [已实现] 同步变更登记 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `sync-dispatch` | [已实现] 关系同步任务调度 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `support-cache` | [部分实现] [Cache] 缓存与失效契约 | 部分实现 / 未标绿 | knowledge/cache_invalidation.py; worker; postgres/postgresql_client.py; orchestration/nodes/clarify.py |
| `support-fallback` | [部分实现] [容错] 容错与回退 | 部分实现 / 未标绿 | knowledge/cache_invalidation.py; worker; postgres/postgresql_client.py; orchestration/nodes/clarify.py |
| `support-hitl` | [部分实现] [HITL] 人工参与 | 部分实现 / 未标绿 | knowledge/cache_invalidation.py; worker; postgres/postgresql_client.py; orchestration/nodes/clarify.py |
| `knowledge-manual` | 手动变更入口 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `knowledge-zentaot` | [部分实现] 禅道自动变更入口 | 部分实现 / 未标绿 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `knowledge-register` | 统一变更登记 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `knowledge-dispatch` | [已实现] 知识后台任务调度 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `m1` | 前端提交任务 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m2` | [部分实现] 鉴权与加载上下文 | 部分实现 / 未标绿 | src/testmind/core/security.py; main.py; api/chat.py; tests/unit/test_knowledge_authorization.py |
| `m3` | 短反馈 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m4` | [已实现] [AI] 拼写纠错 | 已有代码 / 浅绿色 | src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py |
| `m5` | [已实现] [AI] 对话分支指代消解 | 已有代码 / 浅绿色 | src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py |
| `m6` | [已实现] [AI] 意图识别与回答策略 | 已有代码 / 浅绿色 | src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py |
| `m7` | [HITL / 容错] 澄清与等待恢复（部分完成） | 部分实现 / 未标绿 | src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py |
| `m16` | [部分实现] 主图与共享子图 State 边界 | 部分实现 / 未标绿 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `m17` | [部分实现] 统一权限边界 | 部分实现 / 未标绿 | src/testmind/core/security.py; main.py; api/chat.py; tests/unit/test_knowledge_authorization.py |
| `router` | [已实现] 按 intent 路由 | 已有代码 / 浅绿色 | src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py |
| `m12` | [已实现] 问答子图入口（DialogueState） | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `m8` | [已实现] 按 answer_mode 条件路由 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `m9` | 用例生成子图入口 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m10` | 脚本生成子图入口 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m11` | [AI] 图谱查询意图与参数提取 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `chat-answer` | [已实现] [AI] 普通技术问答 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r9` | [已实现] 范围校验 / 目标 RAG 子图入口 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r10` | [已实现] [AI] BGE-M3 向量化 / 稠密召回 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r11` | [已实现] 稀疏召回 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r12` | [已实现] [AI] 融合 / 去重 / BGE-Reranker | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r13` | [已实现] [AI] 消费证据并生成项目回答 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `r14` | [已实现] 统一问答输出 | 已有代码 / 浅绿色 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `s1` | 选择数据来源 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s2` | [AI] 提取测试因子 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s3` | [AI] 生成用例草稿 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s4` | [HITL] 用例评审与修改 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s5` | 写入禅道用例 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s6` | 脚本上下文组装 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s7` | [AI] pytest 脚本生成 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s8` | 静态校验与风险检查 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s9` | [HITL] 人工确认脚本 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s10` | 隔离 Worker 执行 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s11` | 指标与报告 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s12` | [HITL] 人工结论与缺陷协作 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s13` | 短反馈与长反馈 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s16` | N8N 定时回归 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `g7` | [部分实现] 身份与查询入口 | 部分实现 / 未标绿 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g8` | [已实现] 服务端授权过滤 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g9` | [已实现] 受限只读图查询 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g10` | [部分实现] 指标计算 | 部分实现 / 未标绿 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g11` | [已实现] 结果与审计 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g12` | [已实现] Streamlit 图谱操作页 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `m13` | 结果整形与流式返回 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m14` | [HITL] 人工确认 / 外部写入门 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `m15` | [部分实现] 长反馈与记忆落库 | 部分实现 / 未标绿 | src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py |
| `s17` | [部分实现] 任务与测试资产持久化 | 部分实现 / 未标绿 | knowledge/cache_invalidation.py; worker; postgres/postgresql_client.py; orchestration/nodes/clarify.py |
| `r1` | [已实现] 上传资料 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r2` | [已实现] 授权、去重与版本登记 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r3` | [部分实现] 原件与图片存储 | 部分实现 / 未标绿 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r4` | [部分实现] 解析与格式转换 | 部分实现 / 未标绿 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r5` | [已实现] 切块与元数据 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r6` | [已实现] [AI] 本地 Embedding | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `s14` | [HITL] 数据飞轮准入 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `s15` | 审核资产进入知识库 | 待实现 / 未标绿 | 对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清 |
| `r15` | [已实现] 更新、逻辑删除与重建索引 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r8` | [已实现] PostgreSQL 文档与版本登记 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `r7` | [已实现] Milvus 写入与索引验收 | 已有代码 / 浅绿色 | src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_* |
| `g1` | 禅道数据源 | 待实现 / 未标绿 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g2` | [已实现] 签名 Webhook 入口 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g3` | [已实现] 标准网关全量 / 增量补偿 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g4` | [已实现] PostgreSQL 权威资产 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g5` | [已实现] 关系投影构建 / 更新 / 撤回 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g6` | [已实现] Neo4j 图谱 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g13` | [部分实现] [Cache] 权限撤销与缓存失效 | 部分实现 / 未标绿 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `g14` | [已实现] [容错] 同步重试与最终失败 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `tm-graph-events` | [部分实现] TestMind 资产变更事件 | 部分实现 / 未标绿 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |
| `graph-reconcile` | [已实现] 手动对账 / 隔离代际重建 | 已有代码 / 浅绿色 | src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_* |

流程图共 36 个绿色能力节点。唯一正式源文件为 docs/diagrams/TestMind-单页完整流程.drawio。
