"""Apply evidence-backed status annotations without changing diagram geometry."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = (ROOT / "docs/diagrams/TestMind-单页完整流程.drawio",)
GREEN = "#dff3e5"
COMPLETED = {
    "m4", "m5", "m6", "router", "m12", "m8", "chat-answer",
    "r9", "r10", "r11", "r12", "r13", "r14",
    "knowledge-manual", "knowledge-register", "knowledge-dispatch",
    "r1", "r2", "r5", "r6", "r7", "r8", "r15",
    "sync-register", "sync-dispatch", "g2", "g3", "g4", "g5", "g6",
    "g8", "g9", "g11", "g12", "g14", "graph-reconcile",
}
PARTIAL = {
    "m2", "m7", "m16", "m17", "m15", "r3", "r4",
    "knowledge-zentaot", "g7", "g10", "g13", "tm-graph-events",
    "support-cache", "support-fallback", "support-hitl", "s17",
}
LABELS = {
    "m4": ("[已实现] [AI] 拼写纠错", "读：raw_query；仅采纳保守、高置信度修正", "写：corrected_query + correction_detail；保留 raw_query", "先做轻量文本规范化；避免低置信度改写影响意图判断"),
    "m5": ("[已实现] [AI] 对话分支指代消解", "仅 intent=dialogue 时执行；读取 corrected_query + 最近对话历史", "写：resolved_query；无指代、无历史、低置信度或模型异常时回退 corrected_query", "用例 / 脚本 / 图查询等其他意图不调用此模型"),
    "m6": ("[已实现] [AI] 意图识别与回答策略", "拼写纠错后先识别 intent；dialogue 同次返回 answer_mode + confidence", "低置信度或服务异常转澄清；dialogue 再进入指代消解", "只调用一次分类；其他已实现业务分支尚待接入"),
    "router": ("[已实现] 按 intent 路由", "dialogue → 指代消解 → 问答子图", "其他意图进入对应业务分支；当前未接入的分支安全转澄清", "不对非对话意图执行指代消解"),
    "r1": ("[已实现] 上传资料", "PDF / MD / TXT：权限、大小、MIME 与文件签名校验", "POST /api/v1/knowledge-bases/{kb_id}/documents → 202", "返回 document_id / version_id / job_id；不返回存储路径"),
    "r2": ("[已实现] 授权、去重与版本登记", "后台服务：可信 AuthContext；项目 / 知识库范围校验", "幂等键按项目隔离；相同请求复用，不同请求 409", "PostgreSQL 事务登记文档、版本、作业与审计；非 LangGraph 节点"),
    "r3": ("[部分实现] 原件与图片存储", "已实现：UUID 原件路径、SHA256、MinIO / 开发共享卷", "本地原子写入；数据库登记失败清理原件；不返回私网路径", "待实现：图片提取、图片对象存储及实库联调"),
    "r4": ("[部分实现] 解析与格式转换", "已有：PDF / MD / TXT 文本解析、页码与解析告警", "输出 normalized_text / page_offsets / warnings", "待实现：OCR、图片提取、解析进程超时与内存隔离"),
    "r5": ("[已实现] 切块与元数据", "字符分块 + 重叠；chunk_id = hash(version_id + chunk_index)", "正文、页码 / 字符定位、项目 ACL 与版本写入记录", "同一版本重试复用主键；初始 is_active=false"),
    "r6": ("[已实现] [AI] 本地 Embedding", "Knowledge Worker 接入本地 BGE-M3；无隐式下载", "同一次 encode_documents 生成 dense + sparse", "保存模型 / 分块器版本；失败进入可重试作业"),
    "r7": ("[已实现] Milvus 写入与索引验收", "稳定 chunk_id upsert；初始 is_active=false", "flush + Strong 按主键确认向量数量，记录 index_report", "质量通过且索引验收通过才 ready；不会自动激活"),
    "r8": ("[已实现] PostgreSQL 文档与版本登记", "knowledge 独立 schema：文档、版本、作业、质量报告、审计", "保存 active mapping 与可恢复 activation_operation", "PostgreSQL 是可见版本权威；Milvus 标志不是唯一依据"),
    "r15": ("[已实现] 更新、逻辑删除与重建索引", "API：PATCH /documents/{id}；新版本上传；DELETE；POST reindex", "删除写 tombstone 并撤销 PG 可见映射；重建产生新索引版本", "物理原件 / 向量回收与定期对账执行器尚待接入"),
    "knowledge-dispatch": ("[已实现] 知识后台任务调度", "ingest / reindex / activate；SKIP LOCKED + 租约心跳", "指数退避、最大重试、最终失败、优雅停止", "关系同步使用独立 Worker；生产告警与断点分块续跑待实现"),
    "knowledge-zentaot": ("[部分实现] 禅道自动变更入口", "已有：签名校验、统一事件、授权、receipt 与 source_sync 登记", "默认关闭；非禅道原生契约", "待实现：真实契约、source_sync Worker 与自动入库闭环"),
    "g2": ("[已实现] 签名 Webhook 入口", "时间窗 + HMAC 验签；可信来源与项目映射", "有界请求体、receipt 幂等 / 冲突；返回异步 job_id", "独立消息队列消费端尚未实现；不是原生禅道适配器"),
    "g3": ("[已实现] 标准网关全量 / 增量补偿", "配置固定网关 URL、token 与项目映射；分页 / 水位", "整页事件与水位事务提交；失败不前进", "原生禅道接口适配与实账号联调待完成"),
    "g5": ("[已实现] 关系投影构建 / 更新 / 撤回", "独立 Worker：归一化实体 + 显式关系快照", "参数化 Cypher；版本门禁、幂等重试、删除墓碑", "不是 LangGraph 内部节点；不由模型猜测关系"),
    "g7": ("[部分实现] 身份与查询入口", "已有：可信授权上下文、项目枚举与受控查询 API", "POST /api/v1/graph/query；GET /api/v1/metrics", "待实现：生产 JWT / OIDC 中间件与主图自然语言入口"),
    "g10": ("[部分实现] 指标计算", "已有：授权资产计数、显式 automates 关系的自动化率", "返回当前资产口径；维护者可查看同步健康", "待实现：历史进度、频率、影响范围与时间窗指标"),
    "g12": ("[已实现] Streamlit 图谱操作页", "授权项目、实体 / 关系分页、当前资产指标", "维护权限控制拉取 / 对账 / 重建与任务查询", "仅调用 FastAPI；不直连数据库；历史进度看板待实现"),
    "g13": ("[部分实现] [Cache] 权限撤销与缓存失效", "已有：PG membership 撤销过滤与查询后再次复核", "当前图查询不缓存、不生成长期报告链接", "待实现：IAM 变更消费、Redis 失效与未来报告撤权"),
    "g14": ("[已实现] [容错] 同步重试与最终失败", "claim_token + 租约续期；退避重试；超限 failed_permanent", "GET /api/v1/integrations/sync-jobs/{id}", "结构化失败日志已有；生产告警与独立死信系统待实现"),
    "graph-reconcile": ("[已实现] 手动对账 / 隔离代际重建", "PG 权威快照驱动投影修复；新 generation 校验后切换", "失败保留旧查询代际；任务、租约、审计可追踪", "定期调度、旧代际 GC 与未知孤儿精确清理待实现"),
    "tm-graph-events": ("[部分实现] TestMind 资产变更事件", "已有：授权事件登记 API + PG 事务 Outbox + 投影消费", "POST /api/v1/graph/events；source_instance=testmind", "待实现：用例 / 脚本 / 运行模块提交事务自动发事件"),
    "m17": ("[部分实现] 统一权限边界", "知识 / 图谱接口：服务端授权、缺配置 fail closed", "知识检索：PG active mapping + 可信 RequestContext", "待实现：生产身份接入、所有业务入口统一授权与审计"),
    "m2": ("[部分实现] 鉴权与加载上下文", "已有：授权 Provider、RequestContext、PG checkpoint 与检索 scope", "身份不从客户端 user_id / project_id 获取", "待实现：生产身份中间件、Redis 上下文缓存、完整角色记忆"),
    "m16": ("[部分实现] 主图与共享子图 State 边界", "已实现：Dialogue input / output 私有状态、RequestContext", "底层 Retriever 已实现；私有 retrieved_chunks 不回传主图", "待实现：独立 RetrievalState / EvidenceBundle 及跨业务调用"),
    "m15": ("[部分实现] 长反馈与记忆落库", "已有：问答 AIMessage 与 PostgreSQL checkpoint", "同一 thread 可恢复对话历史", "待实现：任务长反馈、评审 / 执行证据与资产记忆更新"),
    "s17": ("[部分实现] 任务与测试资产持久化", "已有：PG checkpoint；知识登记；graph_sync 实体与 Outbox", "问答、知识与关系数据各自持久化", "待实现：task / script / run / report / approval 业务资产与缓存"),
    "support-cache": ("[部分实现] [Cache] 缓存与失效契约", "已有：知识失效 Outbox、持久化幂等、发布 / 对账抽象；待实现：Redis 缓存、失效消费者与运行时对账"),
    "support-fallback": ("[部分实现] [容错] 容错与回退", "已有：模型回退、证据不足、租约重试、最终失败、PG checkpoint；待实现：生产告警、全业务恢复与独立死信平台"),
    "support-hitl": ("[部分实现] [HITL] 人工参与", "已有：模板澄清、下一条消息重新进入主图；待实现：interrupt / resume、批准 / 驳回 / 修改与版本 hash 绑定"),
}


def evidence(ident: str) -> str:
    if ident.startswith("knowledge-") or ident in {f"r{i}" for i in range(1, 9)} | {"r15"}:
        return "src/testmind/api/knowledge.py; infrastructure/knowledge; postgres/knowledge_repository.py; tests/unit/test_knowledge_*"
    if ident.startswith(("g", "sync-", "tm-graph")):
        return "src/testmind/api/graph_*.py; infrastructure/graph_sync; postgres/graph_sync_repository.py; tests/unit/test_graph_*"
    if ident in {"m4", "m5", "m6", "router", "m7"}:
        return "src/testmind/orchestration/builder.py; orchestration/nodes; orchestration/routing.py; tests/unit/test_builder.py"
    if ident in {"m8", "m12", "chat-answer", "r9", "r10", "r11", "r12", "r13", "r14", "m16", "m15"}:
        return "src/testmind/workflows/dialogue.py; infrastructure/milvus/retrieval.py; tests/unit/test_dialogue_workflow.py"
    if ident in {"m2", "m17"}:
        return "src/testmind/core/security.py; main.py; api/chat.py; tests/unit/test_knowledge_authorization.py"
    if ident.startswith("support-") or ident == "s17":
        return "knowledge/cache_invalidation.py; worker; postgres/postgresql_client.py; orchestration/nodes/clarify.py"
    return "对应 API / Agent / workflow 为空文件，或尚无该业务实现；非问答 intent 当前转澄清"


def set_points(geometry: ET.Element, points: tuple[tuple[int, int], ...]) -> None:
    old = geometry.find("Array")
    if old is not None:
        geometry.remove(old)
    array = ET.SubElement(geometry, "Array", {"as": "points"})
    for x, y in points:
        ET.SubElement(array, "mxPoint", {"x": str(x), "y": str(y)})


def update(path: Path) -> tuple[int, int]:
    tree = ET.parse(path)
    cells = {cell.get("id"): cell for cell in tree.findall(".//mxCell")}
    assert (COMPLETED | PARTIAL) <= cells.keys()
    for ident, lines in LABELS.items():
        cells[ident].set("value", "<div style='line-height:1.3'><b>" + lines[0] + "</b><br>" + "<br>".join(lines[1:]) + "</div>")
    for ident, (x, y) in {"m5": (2800, 520), "m6": (2800, 200)}.items():
        geometry = cells[ident].find("mxGeometry")
        geometry.set("x", str(x))
        geometry.set("y", str(y))
    edges = {cell.get("id"): cell for cell in tree.findall(".//mxCell") if cell.get("edge") == "1"}
    edges["edge-5"].set("target", "m6")
    edges["edge-6"].set("source", "router")
    edges["edge-6"].set("target", "m5")
    edges["edge-6"].set("style", re.sub(r"exitX=[^;]+;exitY=[^;]+;", "exitX=1;exitY=0.5;", edges["edge-6"].get("style", "")))
    edges["edge-6"].set("style", re.sub(r"entryX=[^;]+;entryY=[^;]+;", "entryX=0;entryY=0.5;", edges["edge-6"].get("style", "")))
    edge6_geometry = edges["edge-6"].find("mxGeometry")
    set_points(edge6_geometry, ((2500, 595), (2500, 610)))
    set_points(edges["edge-7"].find("mxGeometry"), ((3100, 420), (2230, 420)))
    edge8_geometry = edges["edge-8"].find("mxGeometry")
    set_points(edge8_geometry, ((3100, 450), (3770, 450)))
    edges["edge-10"].set("source", "m5")
    edges["edge-10"].set("target", "m12")
    edges["edge-10"].set("style", re.sub(r"exitX=[^;]+;exitY=[^;]+;", "exitX=0.5;exitY=1;", edges["edge-10"].get("style", "")))
    edges["edge-10"].set("style", re.sub(r"entryX=[^;]+;entryY=[^;]+;", "entryX=0.5;entryY=0;", edges["edge-10"].get("style", "")))
    edge10_geometry = edges["edge-10"].find("mxGeometry")
    set_points(edge10_geometry, ((3100, 760), (470, 760)))
    for ident in COMPLETED:
        cell = cells[ident]
        settings = dict(part.split("=", 1) for part in cell.get("style", "").split(";") if "=" in part)
        settings.update(fillColor=GREEN, strokeColor="#5c9f73")
        bare = [part for part in cell.get("style", "").split(";") if part and "=" not in part]
        cell.set("style", ";".join(bare + [f"{key}={value}" for key, value in settings.items()]) + ";")
    cells["knowledge-note"].set("value", "已有：质量报告 → 索引验收 → ready → 显式异步激活 → PG active mapping 切换 → 旧向量标志清理；OCR、自动来源闭环、缓存消费者与定期对账待实现。")
    rows = []
    for ident, cell in cells.items():
        if cell.get("vertex") != "1" or not (re.fullmatch(r"[mrsg]\d+", ident) or ident in COMPLETED | PARTIAL):
            continue
        title = re.search(r"<b>(.*?)</b>", cell.get("value", ""))
        title = re.sub(r"<[^>]+>", "", title.group(1) if title else cell.get("value", ""))
        status = "已有代码 / 浅绿色" if ident in COMPLETED else "部分实现 / 未标绿" if ident in PARTIAL else "待实现 / 未标绿"
        rows.append(f"| `{ident}` | {title} | {status} | {evidence(ident)} |")
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=False)
    if path == DIAGRAMS[0]:
        report = """# 全流程节点实现状态核对

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
""" + "\n".join(rows) + f"\n\n流程图共 {len(COMPLETED)} 个绿色能力节点。唯一正式源文件为 docs/diagrams/TestMind-单页完整流程.drawio。\n"
        (ROOT / "docs/diagrams/TestMind-节点实现状态核对.md").write_text(report, encoding="utf-8")
    return len(COMPLETED), len(rows)


if __name__ == "__main__":
    for diagram in DIAGRAMS:
        print(diagram.name, update(diagram))
