# 本地模型目录

TestMind 从自己的项目目录加载检索模型，不搜索其他仓库或开发者机器上的课程目录。

```text
pretrained_model/
├── README.md
├── bge-m3/
│   ├── config.json
│   ├── pytorch_model.bin
│   ├── sparse_linear.pt
│   ├── colbert_linear.pt
│   └── tokenizer.json 等配置文件
└── bge-reranker-base/
    ├── config.json
    ├── model.safetensors
    └── tokenizer.json 等配置文件
```

两套模型目录均在 `.gitignore` 中；Git 只跟踪本说明，避免提交大型权重、下载缓存和模型附带文件。
当前开发环境已在上述目录保存真实文件，不是指向其他目录的软链接。

## 首次克隆后准备

在 TestMind 根目录执行：

```powershell
uv sync
uv run hf download BAAI/bge-m3 --local-dir pretrained_model/bge-m3 --exclude "onnx/*"
uv run hf download BAAI/bge-reranker-base --local-dir pretrained_model/bge-reranker-base --exclude "onnx/*"
```

上述命令会联网下载模型；应用启动和查询流程本身不会自动下载。下载可能需要数 GB 磁盘空间。
如需固定部署版本，在命令中增加 `--revision <官方模型仓库的提交 SHA>`，并让入库与查询使用同一版本；不要仅凭目录名称假定模型版本一致。

官方来源与模型说明：

- `BAAI/bge-m3`：https://huggingface.co/BAAI/bge-m3
- `BAAI/bge-reranker-base`：https://huggingface.co/BAAI/bge-reranker-base

模型的使用和再分发遵循各自官方仓库的许可证、模型卡和附带声明；TestMind 的代码许可不替代模型许可。

## 配置与验证

`.env.example` 使用项目内相对路径：

```dotenv
BGE_M3_PATH=pretrained_model/bge-m3
BGE_RERANKER_BASE=pretrained_model/bge-reranker-base
BGE_DEVICE=cpu
BGE_FP16=False
BGE_RERANKER_DEVICE=cpu
BGE_RERANKER_FP16=False
```

不设置两个路径变量也会使用上述默认目录。相对路径以 TestMind 根目录为基准，不受启动时工作目录影响。
如果自己的 `.env` 曾使用外部绝对路径，需要将这两个变量改为上述相对路径。

```powershell
uv run python scripts/smoke_local_retrieval.py
```

该命令离线加载真实本地 BGE-M3 与 Reranker，检查 1024 维稠密向量、非空稀疏向量及相关性排序。
Milvus 使用测试替身，不代表已通过实库或聊天模型端到端联调。
