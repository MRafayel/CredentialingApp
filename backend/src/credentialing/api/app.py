"""FastAPI application factory."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI

from credentialing.api import health

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(*, expose_docs: bool, lifespan: Lifespan | None = None) -> FastAPI:
    """Build the HTTP application.

    Args:
        expose_docs: Serve the interactive OpenAPI pages. Off outside local development.
        lifespan: Opens resources on startup and releases them on shutdown. Supplied by the
            composition root, so this layer never learns what those resources are.
    """
    app = FastAPI(
        title="Credentialing API",
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app
