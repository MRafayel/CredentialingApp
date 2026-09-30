"""Composition root: the one place where concrete implementations are wired together.

Run with ``uvicorn credentialing.main:app``.
"""

from credentialing.api.app import create_app
from credentialing.infrastructure.config import get_settings

settings = get_settings()
app = create_app(expose_docs=settings.environment == "local")
