"""Mapping of application errors onto HTTP responses, registered once on the app."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from credentialing.application.errors import (
    InvalidProviderInputError,
    ProviderHasNpiError,
    ProviderNotFoundError,
    RecordNotFoundError,
    RegistryUnavailableError,
)


def register_error_handlers(app: FastAPI) -> None:
    """404 for an unknown provider or record, 422 for provider data that breaks a domain
    rule, 409 for setting an NPI on a provider that has one, 503 when NPPES could not be
    checked for an NPI to set. A well-formed NPI is never 404: "not found" and "could not
    check" are answers."""

    @app.exception_handler(ProviderNotFoundError)
    async def provider_not_found(_request: Request, error: ProviderNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(error)})

    @app.exception_handler(RecordNotFoundError)
    async def record_not_found(_request: Request, error: RecordNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(error)})

    @app.exception_handler(InvalidProviderInputError)
    async def invalid_provider(_request: Request, error: InvalidProviderInputError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(error)}
        )

    @app.exception_handler(ProviderHasNpiError)
    async def provider_has_npi(_request: Request, error: ProviderHasNpiError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(error)})

    @app.exception_handler(RegistryUnavailableError)
    async def registry_unavailable(
        _request: Request, error: RegistryUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(error)}
        )
