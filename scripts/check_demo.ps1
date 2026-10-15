$ErrorActionPreference = 'Stop'
$env:DEMO_TEST_DSN = 'postgresql://testmind@127.0.0.1:15432/testmind'
$env:KNOWLEDGE_TEST_DSN = $env:DEMO_TEST_DSN
$env:GRAPH_TEST_DSN = $env:DEMO_TEST_DSN
$env:KNOWLEDGE_TEST_MILVUS_URI = 'http://127.0.0.1:29530'
$env:KNOWLEDGE_TEST_COLLECTION = 'testmind_demo_chunks'
$env:GRAPH_TEST_NEO4J_URI = 'bolt://127.0.0.1:17687'
$env:GRAPH_TEST_NEO4J_USER = 'neo4j'
$env:GRAPH_TEST_NEO4J_PASSWORD = 'demo-no-auth'
uv run pytest -q --tb=short
if ($LASTEXITCODE -ne 0) { throw 'Demo regression failed' }
uv run python scripts/measure_capacity.py
if ($LASTEXITCODE -ne 0) { throw 'Demo queue capacity measurement failed' }
