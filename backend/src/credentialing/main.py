"""Composition root of the HTTP API. Run with ``uvicorn credentialing.main:app``."""

from credentialing.bootstrap import create_api
from credentialing.infrastructure.config import get_settings

app = create_api(get_settings())
