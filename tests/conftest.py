
import pytest

from ftabot.db import Database


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
