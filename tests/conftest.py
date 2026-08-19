from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
import uvicorn

from core_console.app import app


@pytest.fixture(scope="session")
def console_url() -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=3010, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time

    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield "http://127.0.0.1:3010"
    server.should_exit = True
