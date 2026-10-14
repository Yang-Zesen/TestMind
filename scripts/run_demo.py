"""Start one explicit local-demo component without loading personal secrets."""

import argparse
import asyncio
import os

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=("api", "worker"))
    parser.add_argument("--port", type=int, default=26104)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    if args.component == "api":
        import uvicorn

        uvicorn.run("testmind.main:app", host="127.0.0.1", port=args.port, loop="testmind.main:selector_loop_factory")
    else:
        from testmind.worker.task_worker import run

        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run(once=args.once, task_id=args.task_id))


if __name__ == "__main__":
    main()
