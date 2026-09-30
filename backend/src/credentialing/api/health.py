"""Liveness endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: Literal["ok"] = "ok"


@router.get("/health")
async def health() -> HealthResponse:
    """Report that the process is up. Says nothing about external sources."""
    return HealthResponse()
