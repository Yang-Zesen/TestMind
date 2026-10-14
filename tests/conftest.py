"""Use the same psycopg-compatible event loop as the Windows runtime."""

import asyncio


def pytest_asyncio_loop_factories():
    return {"selector": asyncio.SelectorEventLoop}
