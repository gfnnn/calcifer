
import pytest
from aiohttp.test_utils import TestClient, TestServer

from ftabot.db import Database
from ftabot.ingest import Ingest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture()
async def ingest_client():
    """Factory: ``client, ingest = await ingest_client(config)`` for the inbound
    listener (in-process, loopback only). Clients are closed on teardown."""
    clients: list[TestClient] = []

    async def make(config):
        ingest = Ingest(config)
        client = TestClient(TestServer(ingest.app()))
        await client.start_server()
        clients.append(client)
        return client, ingest

    try:
        yield make
    finally:
        for client in clients:
            await client.close()
