"""Mapping of application errors onto HTTP responses, registered once on the app."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from credentialing.application.errors import InvalidProviderInputError, ProviderNotFoundError


def register_error_handlers(app: FastAPI) -> None:
    """404 for an unknown provider, 422 for provider data that breaks a domain rule."""

    @app.exception_handler(ProviderNotFoundError)
    async def provider_not_found(_request: Request, error: ProviderNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(error)})

    @app.exception_handler(InvalidProviderInputError)
    async def invalid_provider(_request: Request, error: InvalidProviderInputError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(error)}
        )
