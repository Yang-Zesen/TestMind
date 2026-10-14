"""Manage the unmodified official executable and localhost Basic bridge."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import httpx

from testmind.infrastructure.zentao.mcp import ZenTaoSettings


def compatible_schema(source: Path) -> dict[str, Any]:
    schema = json.loads(source.read_text(encoding="utf-8"))
    allowed = {
        "/projects",
        "/stories/:storyID",
        "/testcases/:caseID",
        "/bugs/:bugID",
        "/projects/:projectID/stories",
        "/projects/:projectID/testcases",
        "/projects/:projectID/bugs",
    }
    paths = {}
    for path, item in schema["paths"].items():
        if path not in allowed:
            continue
        operation = item["get"]
        parameters = operation.setdefault("parameters", [])
        for name in re.findall(r":([A-Za-z][A-Za-z0-9_]*)", path):
            if not any(p.get("name") == name and p.get("in") == "path" for p in parameters):
                parameters.append(
                    {
                        "name": name,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "minimum": 1},
                    }
                )
        if path.endswith(("/stories", "/testcases", "/bugs")) or path == "/projects":
            for name in ("pageID", "recPerPage"):
                if not any(p.get("name") == name for p in parameters):
                    parameters.append(
                        {"name": name, "in": "query", "schema": {"type": "integer", "minimum": 1}}
                    )
        normalized = re.sub(r":([A-Za-z][A-Za-z0-9_]*)", r"{\1}", path)
        paths[normalized] = {"get": operation}
    schema["paths"] = paths
    fields = paths["/projects"]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"
    ]["projects"]["items"]["properties"]
    fields["progress"] = {"oneOf": [{"type": "string"}, {"type": "number"}]}
    for key in ("PM", "PMAvatar", "PMUserID"):
        fields[key] = {"oneOf": [{"type": "boolean"}, {"type": "string"}]}
    return cast(dict[str, Any], schema)


@contextmanager
def official_mcp(settings: ZenTaoSettings, runtime_dir: Path, port: int = 9090) -> Iterator[str]:
    executable = runtime_dir / "runtime/bin/zentao-mcp-windows-amd64.exe"
    source = runtime_dir / "runtime/docs/zentao-openapi.json"
    if not executable.is_file() or not source.is_file():
        raise RuntimeError(
            "Download the official Windows amd64 v1.0.2 release into tmp/zentao-mcp/runtime"
        )
    if port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    upstream = settings.base_url.rstrip("/") + "/api.php/v2"

    class Bridge(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            # Ignore client Authorization: only the server-owned Basic credential is used.
            try:
                with httpx.Client(
                    auth=(settings.basic_user, settings.basic_password.get_secret_value())
                    if settings.basic_user
                    else None,
                    timeout=settings.timeout,
                    trust_env=False,
                    follow_redirects=False,
                ) as client:
                    response = client.get(
                        upstream + self.path, headers={"Token": self.headers.get("Token", "")}
                    )
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            except httpx.HTTPError:
                self.send_error(502, "ZenTao upstream unavailable")

        def log_message(self, *_: Any) -> None:
            pass

    bridge = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    if not port:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    schema_path = runtime_dir / "testmind-openapi.json"
    config_path = runtime_dir / "testmind-config.yaml"
    process = None
    try:
        schema_path.write_text(json.dumps(compatible_schema(source)), encoding="utf-8")
        config_path.write_text(
            json.dumps(
                {
                    "port": f"127.0.0.1:{port}",
                    "enable_toon": False,
                    "telemetry": {"endpoint": "", "insecure": True},
                    "servers": [
                        {
                            "name": "zentao",
                            "schema_url": str(schema_path.resolve()),
                            "base_url": f"http://127.0.0.1:{bridge.server_port}",
                            "allow": [{"methods": ["GET"], "regex": ".*"}],
                            "block": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with (runtime_dir / "testmind-mcp.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(executable.resolve()), "-config", str(config_path.resolve())],
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Official MCP startup failed; inspect local runtime log")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Official MCP startup timed out")
            yield f"http://127.0.0.1:{port}/zentao/mcp"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        bridge.shutdown()
        bridge.server_close()
