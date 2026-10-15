"""Validate the offline local model artifacts used by the demo."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from testmind.core.config import get_config


def fingerprint(path: Path) -> dict[str, object]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if len(files) > 20000:
        raise ValueError("model_file_count_exceeded")
    digest = hashlib.sha256()
    total = 0
    for item in files:
        size = item.stat().st_size
        total += size
        if total > 20 * 1024 * 1024 * 1024:
            raise ValueError("model_size_exceeded")
        digest.update(item.relative_to(path).as_posix().encode())
        with item.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return {"path": str(path.resolve()), "files": len(files), "bytes": total, "sha256": digest.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default="deploy/demo.env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    config = get_config()
    models = {"embedding": Path(config.embedding_model), "reranker": Path(config.reranker_model)}
    missing = [role for role, path in models.items() if not path.is_dir() or not any(path.iterdir())]
    if missing:
        raise SystemExit(f"Missing offline model artifacts: {', '.join(missing)}")
    report = {"mode": "offline", "models": {role: fingerprint(path) for role, path in models.items()},
              "device": config.embedding_device, "reranker_device": config.reranker_device}
    output = Path("output/model-manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
