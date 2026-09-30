"""FastAPI application factory."""

from fastapi import FastAPI

from credentialing.api import health


def create_app(*, expose_docs: bool) -> FastAPI:
    """Build the HTTP application.

    Args:
        expose_docs: Serve the interactive OpenAPI pages. Off outside local development.
    """
    app = FastAPI(
        title="Credentialing API",
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.include_router(health.router)
    return app
