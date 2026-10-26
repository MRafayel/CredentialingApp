"""Which organization a request acts for.

TODO(auth): authentication is an open decision (proposed: Supabase Auth behind a port). Until
it is taken, local development names the organization in the ``X-Organization-Id`` header,
and every other environment refuses the request, because a header anyone can set must never
decide tenant access outside a developer's machine.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from credentialing.api.container import Container


def current_organization_id(
    container: Container,
    x_organization_id: Annotated[UUID | None, Header()] = None,
) -> UUID:
    """The organization from ``X-Organization-Id``, accepted only in local development."""
    if not container.organization_header_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is not configured for this environment.",
        )
    if x_organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Send the X-Organization-Id header (local development only).",
        )
    return x_organization_id


OrganizationId = Annotated[UUID, Depends(current_organization_id)]
