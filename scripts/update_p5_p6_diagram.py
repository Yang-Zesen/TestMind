from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

path = Path("docs/diagrams/TestMind-单页完整流程.drawio")
tree = ET.parse(path)
root = tree.getroot()
root.set("deliveryScope", "local-demo-P0-P6-20261014")
root.set(
    "completionBasis",
    "Local demo acceptance only; real BGE and PG-Milvus-Neo4j, simulated identity and ZenTao; fixed retrieval evaluation 3/4; production targets retain independent status",
)
cells = {cell.get("id"): cell for cell in root.iter("mxCell")}
updates = {
    "demo-p4-status": "<b>2026-10-14 本地 P4 图谱闭环与 P0-P6 整体审查</b><br>本地模拟项目/成员/需求/用例/缺陷；持久化事件、独立分页水位、删除/撤权；真实 PG→Neo4j<br>私有查询子图接主图/工作台；受限实体/关系/当前指标；PG 权威版本/关系复核、历史结果撤权<br>独立 graph-worker；对账修复、隔离代际重建、最新快照/租约/维护授权校验后切换<br>来源登记时间/维护者水位；无历史不推断趋势；旧代际至少保留 24 小时，仅清理预览<br>P5/P6 本地闭环与限制见本地验收 SOP；生产身份和真实禅道仍未验收。",
    "demo-p2-p3-status": "<b>2026-10-14 本地 P2/P3 实测范围（覆盖旧 P0/P1 说明中的后续项）</b><br>真实 PG/Milvus/BGE-M3/Reranker；共享 EvidenceBundle、前后权限/版本复核、摘录回答与引用跳转<br>独立知识 Worker；有界解析、质量/索引 ready、显式激活、替换/reindex/撤回；模拟增量来源与 source_sync<br>需求/OpenAPI 规则草稿；来源快照、逐条编辑/批准/拒绝，改稿失效审批、过期来源/证据阻断发布<br>持久化操作账本 + 模拟禅道 DEMO-ID；并发幂等、未知结果待核实、禁止盲目重发<br>P4-P6 本地演示验收见对应 SOP；真实企业身份、真实生成模型质量及真实禅道读写仍未验收。",
}
for cell_id, value in updates.items():
    cells[cell_id].set("value", value)

if "demo-p5-p6-status" not in cells:
    node = ET.SubElement(
        root.find("./diagram/mxGraphModel/root"),
        "mxCell",
        {
            "id": "demo-p5-p6-status",
            "parent": "1",
            "vertex": "1",
            "implementationStatus": "demo_verified",
            "deliveryScope": "support",
            "style": "rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=middle;fontFamily=Microsoft YaHei;fontSize=16;spacing=14;fillColor=#dff3e5;strokeColor=#348054;fontColor=#182230;",
            "value": "<b>2026-10-14 本地 P5/P6 实测范围</b><br>P5：未启用能力明确终态；澄清恢复清理旧输出；知识历史结果复核当前授权/版本<br>来源、用例草稿/审批/模拟发布与图谱事件在 PG 同事务写 Outbox；投影重试不重复执行业务发布<br>P6：独立 Compose 与不可变迁移/模型指纹；API 和三个 Worker 深度就绪；心跳/有界队列/项目运维面板<br>256 入队请求：128 接受、128 达用户队列上限；仅测本机 PG 受理，不含模型推理<br>固定检索评估 3/4；PG 还原 3.359 秒，5 个源对象恢复，Milvus/Neo4j 重建通过<br>演示身份/模拟禅道；生产身份、真实外部系统、模型质量和多机容量未验收。详情见 P5/P6 SOP。",
        },
    )
    ET.SubElement(
        node,
        "mxGeometry",
        {"x": "6300", "y": "800", "width": "2000", "height": "245", "as": "geometry"},
    )

tree.write(path, encoding="utf-8", xml_declaration=False)
