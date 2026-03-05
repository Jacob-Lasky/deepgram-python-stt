# tests/conftest.py
# Source: Haseeb Majid's blog (UvicornTestServer pattern)
#         + python-socketio issue #263 teardown fix (await client.wait())
#         + pytest-asyncio 0.23+ loop_scope pattern
import asyncio
import pytest_asyncio
import socketio
import uvicorn
from app import app  # socketio.ASGIApp instance — NOT fastapi_app

HOST = "127.0.0.1"
PORT = 9765  # distinct from dev server port (8001) and environment port 8765
BASE_URL = f"http://{HOST}:{PORT}"


class UvicornTestServer(uvicorn.Server):
    def __init__(self, application=app, host=HOST, port=PORT):
        self._startup_done = asyncio.Event()
        super().__init__(config=uvicorn.Config(
            application, host=host, port=port, log_level="error"
        ))

    async def startup(self, sockets=None) -> None:
        # sockets=None required — uvicorn 0.30+ passes this keyword argument
        await super().startup(sockets=sockets)
        self._startup_done.set()

    async def start_up(self) -> None:
        self._serve_task = asyncio.create_task(self.serve())
        await self._startup_done.wait()

    async def tear_down(self) -> None:
        self.should_exit = True
        await self._serve_task


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def server():
    """Start the real ASGI app once for the whole test session."""
    srv = UvicornTestServer()
    await srv.start_up()
    yield
    await srv.tear_down()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def sio_client(server):
    """Connected socketio.AsyncClient for the test session."""
    client = socketio.AsyncClient()
    await client.connect(BASE_URL, transports=["websocket"])
    yield client
    await client.disconnect()
    await client.wait()  # CRITICAL: prevents hang (python-socketio issue #263)
