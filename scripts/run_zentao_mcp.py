"""Run the official MCP locally, or smoke-test TestMind against the sandbox."""

import argparse
import asyncio
import json
import threading
from pathlib import Path

from dotenv import load_dotenv

from testmind.infrastructure.zentao.mcp import ZenTaoMCPClient, ZenTaoService, ZenTaoSettings
from testmind.infrastructure.zentao.runtime import official_mcp

ROOT = Path(__file__).resolve().parents[1]


async def smoke(settings):
    client = ZenTaoMCPClient(settings)
    service = ZenTaoService(client)
    try:
        project = await service.project("1")
        print(json.dumps({"project_id": project["id"], "status": "ok"}))
        for resource in ("stories", "testcases", "bugs"):
            page = await service.list_records("1", resource)
            detail = await service.detail("1", resource, "1")
            print(
                json.dumps(
                    {
                        "resource": resource,
                        "count": len(page["records"]),
                        "id": detail["id"],
                        "status": "ok",
                    }
                )
            )
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    settings = ZenTaoSettings.from_env()
    with official_mcp(settings, ROOT / "tmp/zentao-mcp", 0 if args.smoke else args.port) as url:
        settings.mcp_url = url
        if args.smoke:
            asyncio.run(smoke(settings))
        else:
            print(f"ZenTao MCP: {url}", flush=True)
            try:
                threading_event = threading.Event()
                threading_event.wait()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
