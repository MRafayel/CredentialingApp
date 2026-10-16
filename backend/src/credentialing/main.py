"""Composition root: the one place where concrete implementations are wired together.

Run with ``uvicorn credentialing.main:app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from credentialing.api.app import create_app
from credentialing.infrastructure.config import get_settings
from credentialing.infrastructure.database import create_pool

settings = get_settings()
pool = create_pool(settings.database_url.get_secret_value())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Keep the database pool open for the life of the process."""
    await pool.open()
    try:
        yield
    finally:
        await pool.close()


app = create_app(expose_docs=settings.environment == "local", lifespan=lifespan)
