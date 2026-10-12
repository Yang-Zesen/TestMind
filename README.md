# TestMind

**面向软件测试团队的 AI 增强与质量协作平台。** TestMind 将项目知识、需求与测试资产组织为可追溯的工作流，通过检索和多步骤 AI 编排辅助测试人员理解问题、查询项目知识、设计测试用例，并逐步连接现有测试管理与工程系统。

TestMind 不替代测试人员作出质量决策：用例、外部系统写入和执行等关键操作应保留授权、审核与结果追踪。

## 主流程

![TestMind 主流程图：统一入口、业务子图和独立后台工作流](https://files.catbox.moe/l2tfwx.png)

图中绿色表示代码层已有实现，不代表已完成生产部署或真实外部系统联调；无色表示纳入当前交付范围但尚未整体完成；红色表示暂缓。

## 当前 MVP 范围

当前 MVP 聚焦以下能力：

- **统一对话入口**：拼写纠错、意图识别、按意图分流；问答分支执行指代消解。
- **项目知识问答**：区分直接回答与基于项目证据的检索回答，支持来源引用。
- **共享 RAG 能力**：围绕授权项目和文档版本进行检索、排序与证据组织。
- **测试用例生成流程**：解析输入资料，形成可评审的用例草稿，并为后续受控写入留出边界。
- **图谱查询**：以服务端权限约束查询和指标访问。
- **独立后台工作流**：知识资料入库，以及外部关系数据同步和图谱投影。

脚本生成与审批、隔离执行、执行报告、自动调度、UI/专项测试、完整测试计划和修复回归报告暂不属于当前 MVP。MVP 是分阶段交付范围，不等于 PRD 全量验收完成。

## 架构概览

```text
Streamlit 工作台
       │ HTTP
       ▼
FastAPI API ── 服务端认证与项目授权
       │
       ├── LangGraph 对话主图
       │      ├── 问答 / 共享 RAG
       │      ├── 用例生成（逐步接入）
       │      └── 受控图谱查询（逐步接入）
       │
       ├── 知识入库 API + 独立 Worker
       └── 关系同步 API + 独立 Worker
              │
              ├── PostgreSQL：业务数据、版本、任务与工作流检查点
              ├── Milvus：知识片段向量检索
              └── Neo4j：关系数据投影与查询
```

后台 Worker 与交互请求分开运行。PostgreSQL 保存权威业务状态；Milvus 和 Neo4j 分别服务于知识检索与关系查询。生产部署需接入可信身份、权限和外部系统配置。

## 技术栈

**应用与 API**

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-4051B5?style=flat)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=flat&logo=pydantic&logoColor=white)

Streamlit 提供本地工作台；FastAPI、Uvicorn 和 Pydantic 支撑 HTTP 接口、输入校验与服务端授权边界。

**AI 与工作流**

![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=flat&logo=langgraph&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=flat&logo=langchain&logoColor=white)
![DeepSeek](https://img.shields.io/badge/DeepSeek-API-4D6BFE?style=flat)
![BGE-M3](https://img.shields.io/badge/BGE--M3-Embedding-00897B?style=flat)
![BGE Reranker](https://img.shields.io/badge/BGE-Reranker-7B1FA2?style=flat)

LangGraph 和 LangChain 管理对话状态与多步骤编排；模型服务用于语言理解与生成，本地 BGE-M3 和 BGE Reranker 用于向量生成与重排。

**数据与检索**

![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=flat&logo=sqlalchemy&logoColor=white)
![Alembic](https://img.shields.io/badge/Alembic-646464?style=flat)
![Milvus](https://img.shields.io/badge/Milvus-00A1EA?style=flat&logo=milvus&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-4581C3?style=flat&logo=neo4j&logoColor=white)

PostgreSQL 保存业务数据、版本和检查点；SQLAlchemy 与 Alembic 支撑数据访问和迁移；Milvus 提供知识检索，Neo4j 提供关系投影与查询。

**开发与质量检查**

![uv](https://img.shields.io/badge/uv-DE5FE9?style=flat&logo=uv&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat&logo=pytest&logoColor=white)
![Ruff](https://img.shields.io/badge/Ruff-D7FF64?style=flat&logo=ruff&logoColor=black)

uv 管理依赖与虚拟环境，pytest 和 Ruff 用于测试及代码检查。

## 当前实现状态

仓库包含聊天与问答链路、知识入库和 Worker、关系同步及图谱相关模块，以及对应单元测试。代码已具备相应组件，不等于所有 MVP 流程已经端到端验收。

当前仍需按部署逐项完成真实数据库、模型服务、授权提供方、禅道契约及其他外部系统的配置和联调。真实身份接入、真实项目权限和生产数据上的端到端验收，不能由本地单元测试代替。脚本生成、审批、隔离执行等暂缓能力也不应视为已交付。

## 本地开发

### 环境准备

- Python 3.11 或更新版本
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL；知识检索流程还需要 Milvus
- 按需配置模型服务凭据和本地 BGE 模型

```powershell
uv sync
Copy-Item .env.example .env
```

按实际环境编辑 `.env`：设置 PostgreSQL 连接、模型凭据和所需服务地址。`.env` 含本机配置，不要提交；`.env.example` 只提供配置项示例。BGE 权重需按 [`pretrained_model/README.md`](pretrained_model/README.md) 单独准备，不会随 Git 仓库下载。

### 启动

API、知识 Worker 和 Streamlit 工作台分别在独立终端启动：

```powershell
uv run python -m testmind.main
```

```powershell
uv run python -m testmind.worker.knowledge_worker
```

```powershell
$env:TESTMIND_API_URL = "http://127.0.0.1:26104"
uv run streamlit run frontend/streamlit/app.py --server.port 8501
```

知识入库还需要完成 PostgreSQL 迁移、知识库预置和本地开发授权配置。完整步骤见项目维护者提供的本地 Docker 与知识入库验收 SOP；该文档当前不包含在公开 Git 仓库中。

### 测试与检查

```powershell
uv run pytest -q
uv run ruff check src tests
```

需要外部数据库或服务的集成测试应在配置好对应环境后单独运行；跳过的集成测试不代表外部服务验收通过。

## 安全说明

- 聊天和项目资源访问使用服务端认证上下文；客户端传入的用户或项目字段不能作为授权凭据。
- 知识与关系数据应按项目和有效版本隔离；生产环境不得使用仅供本地开发的 Mock 授权。
- 禅道事件适配器默认关闭；启用前需配置签名密钥、外部事件契约和访问控制。
- 不要将 `.env`、访问令牌、数据库凭据或模型权重提交到仓库。

## 项目资料

产品需求、技术设计和可编辑流程图用于项目设计与评审，目前保存在维护者本地的 `docs/` 目录，该目录被 Git 忽略，不会随本仓库发布。此 README 根据这些资料整理；如需公开 PRD、TDD 或流程图源文件，应先单独确认文档发布范围并调整仓库跟踪策略。
