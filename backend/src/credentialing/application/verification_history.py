"""Read side of verifications: results with the exclusion records they point at."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import ProviderNotFoundError
from credentialing.domain.models import ExclusionRecord, VerificationResult
from credentialing.domain.ports import (
    ExclusionRecordRepository,
    ProviderRepository,
    VerificationRepository,
)


class VerificationView(BaseModel):
    """A verification result with its matched exclusion records expanded."""

    model_config = ConfigDict(frozen=True)

    result: VerificationResult
    matched_records: tuple[ExclusionRecord, ...] = ()


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

    async def history(
        self, organization_id: UUID, provider_id: UUID
    ) -> tuple[VerificationView, ...]:
        """Every result for the provider across credential types, newest first.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        if await self._providers.get(organization_id, provider_id) is None:
            raise ProviderNotFoundError(provider_id)
        return await self.expand(
            await self._verifications.history_all(organization_id, provider_id)
        )

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
