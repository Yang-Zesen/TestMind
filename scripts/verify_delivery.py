"""Reproducible live HTTP delivery checks using the frozen demo corpus."""

import argparse
import asyncio
import json
import time
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx


async def run(url: str) -> None:
    headers = {"X-Demo-User": "demo-user"}
    checks = []
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=30) as client:
        async def request(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        async def wait(path, expected, timeout=180):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                value = await request("GET", path)
                if value["status"] == expected:
                    return value
                if value["status"] in {"failed", "failed_permanent", "rejected"}:
                    raise RuntimeError(f"Delivery check failed: {path}: {value['status']}")
                await asyncio.sleep(1)
            raise TimeoutError(path)

        async def task(query, expected="completed"):
            submitted = await request("POST", "/api/v1/tasks", json={"query": query, "project_id": "demo-project", "session_id": str(uuid4())}, headers={"Idempotency-Key": str(uuid4())})
            return await wait("/api/v1/tasks/" + submitted["task_id"], expected)

        ready = await request("GET", "/ready?deep=true")
        assert ready["status"] == "ready"
        checks.append("deep_ready_four_components")
        content = await asyncio.to_thread(Path("tests/fixtures/demo/testing-guide.md").read_bytes)
        upload = await request("POST", "/api/v1/knowledge-bases/demo-kb/documents", files={"file": ("testing-guide.md", BytesIO(content), "text/markdown")}, data={"title": "P6 frozen guide"}, headers={"Idempotency-Key": str(uuid4())})
        await wait("/api/v1/jobs/" + upload["job_id"], "succeeded")
        activated = await request("POST", f"/api/v1/documents/{upload['document_id']}/versions/{upload['version_id']}/activate")
        await wait("/api/v1/activation-operations/" + activated["operation_id"], "succeeded")
        checks.append("knowledge_upload_activation")
        golden = json.loads(Path("tests/fixtures/demo/golden.json").read_text(encoding="utf-8"))
        evaluations = []
        for sample in golden["questions"]:
            result = (await task("knowledge: " + sample["query"]))["result"]
            supported = bool(result.get("citations"))
            if sample.get("expected") == "insufficient_evidence":
                supported = not supported
            elif supported:
                supported = all(term in result["answer"].lower() for term in sample["expected_terms"])
            evaluations.append({"query": sample["query"], "passed": supported, "citations": len(result.get("citations", []))})
        checks.append(f"frozen_golden_{sum(item['passed'] for item in evaluations)}_of_{len(evaluations)}")
        await task("pytest direct answer")
        await task("generate script", "not_enabled")
        await task("zentao projects")
        checks.extend(["direct", "not_enabled", "readonly"])
        clarified = await task("?", "waiting_input")
        await request("POST", "/api/v1/tasks/" + clarified["task_id"] + "/resume", json={"query": "pytest", "expected_version": clarified["version"]})
        await wait("/api/v1/tasks/" + clarified["task_id"], "completed")
        checks.append("clarify_resume")
        source = await request("POST", "/api/v1/case-sources", json={"project_id": "demo-project", "kind": "requirement", "title": "P6 Login", "content": "Login locks after five failed attempts for thirty minutes."})
        design = await request("POST", "/api/v1/case-design", json={"project_id": "demo-project", "source_ids": [source["id"]], "session_id": str(uuid4())}, headers={"Idempotency-Key": str(uuid4())})
        await wait("/api/v1/tasks/" + design["task_id"], "waiting_review")
        assets = await request("GET", "/api/v1/tasks/" + design["task_id"] + "/cases")
        for asset in assets:
            payload = {"version": asset["version"], "content_hash": asset["content_hash"]}
            await request("POST", "/api/v1/test-cases/" + asset["id"] + "/review", json=payload | {"decision": "approved"})
            publication = await request("POST", "/api/v1/test-cases/" + asset["id"] + "/publish", json=payload)
            assert publication["external_id"].startswith("DEMO-")
        checks.append("design_review_publication")
        end = time.monotonic() + 120
        while time.monotonic() < end:
            graph = (await task('graph-query:{"query_type":"entities","entity_type":"test_case","limit":200}'))["result"]["graph_result"]
            if assets[0]["id"] in json.dumps(graph):
                break
            await asyncio.sleep(2)
        else:
            raise TimeoutError("published case projection")
        checks.append("publication_to_graph")
        denied = await client.get("/api/v1/tasks/" + design["task_id"], headers={"X-Demo-User": "demo-other"})
        assert denied.status_code == 404
        checks.append("cross_user_denied")
    output = Path("output/p6-delivery.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"url": url, "scope": "local simulation; real PG/Milvus/Neo4j/BGE", "checks": checks, "evaluation": evaluations}
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:26114")
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
