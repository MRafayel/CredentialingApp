"""Read side of verifications: results with the exclusion records they point at, a
provider's history a page of moments at a time."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import ProviderNotFoundError
from credentialing.domain.models import ExclusionRecord, VerificationResult
from credentialing.domain.policies import HISTORY_RETENTION
from credentialing.domain.ports import (
    ExclusionRecordRepository,
    ProviderRepository,
    VerificationRepository,
)

DEFAULT_MOMENTS: Final = 10
"""Moments on one history page unless the caller asks for another number."""

MAX_MOMENTS: Final = 50
"""The most moments one history page holds."""


class VerificationView(BaseModel):
    """A verification result with its matched exclusion records expanded."""

    model_config = ConfigDict(frozen=True)

    result: VerificationResult
    matched_records: tuple[ExclusionRecord, ...] = ()


class VerificationHistoryPage(BaseModel):
    """One page of a provider's history (:meth:`VerificationHistory.page`) and how far back
    the history goes, so no screen hardcodes it."""

    model_config = ConfigDict(frozen=True)

    views: tuple[VerificationView, ...]
    next_before: datetime | None = None
    retention: timedelta = HISTORY_RETENTION


class VerificationHistory:
    """Reads a provider's verification history and expands matched records."""

    def __init__(
        self,
        providers: ProviderRepository,
        verifications: VerificationRepository,
        records: ExclusionRecordRepository,
    ) -> None:
        self._providers = providers
        self._verifications = verifications
        self._records = records

    async def page(
        self, organization_id: UUID, provider_id: UUID, *, before: datetime | None, moments: int
    ) -> VerificationHistoryPage:
        """The provider's results at the newest ``moments`` instants older than ``before``,
        newest first, with the next page's ``before``. A moment is one instant results were
        stored at: a check run's results per list share one, an NPI verification has its own.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        await self._require(organization_id, provider_id)
        found = await self._verifications.history_page(
            organization_id, provider_id, before=before, moments=moments
        )
        return VerificationHistoryPage(
            views=await self.expand(found.results), next_before=found.next_before
        )

    async def latest_npi(self, organization_id: UUID, provider_id: UUID) -> VerificationView | None:
        """The provider's newest NPI verification from any source, which may be older than
        the history's first page.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        await self._require(organization_id, provider_id)
        result = await self._verifications.latest_npi(organization_id, provider_id)
        return (await self.expand([result]))[0] if result else None

    async def _require(self, organization_id: UUID, provider_id: UUID) -> None:
        if await self._providers.get(organization_id, provider_id) is None:
            raise ProviderNotFoundError(provider_id)

    async def expand(self, results: Sequence[VerificationResult]) -> tuple[VerificationView, ...]:
        """Attach each result's matched records, in the result's own order, in one query."""
        ids = sorted({record_id for result in results for record_id in result.matched_record_ids})
        by_id = {record.id: record for record in await self._records.get_many(ids)}
        return tuple(
            VerificationView(
                result=result,
                matched_records=tuple(
                    by_id[record_id]
                    for record_id in result.matched_record_ids
                    if record_id in by_id
                ),
            )
            for result in results
        )
