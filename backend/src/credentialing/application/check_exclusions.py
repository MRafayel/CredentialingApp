"""Use case: check one provider against every connected exclusion list."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID, uuid4

from credentialing.application.errors import ProviderNotFoundError
from credentialing.domain.enums import (
    CredentialType,
    Finding,
    MatchLevel,
    MatchRelation,
    Outcome,
    RecordStatus,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    MatchResult,
    Provenance,
    Provider,
    ProviderVerdict,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.policies import (
    RECORD_STATUS_DETAIL,
    FreshnessPolicyTable,
    derive_verdict,
    match_provider_to_record,
    record_has_ended,
    record_is_removed,
    record_status,
)
from credentialing.domain.ports import (
    BulkListConnector,
    Clock,
    ConnectorDirectory,
    ExclusionRecordRepository,
    ProviderRepository,
    SnapshotRepository,
    VerificationRepository,
)

_STRENGTH: Final = {level: rank for rank, level in enumerate(MatchLevel)}
"""Match levels ranked strongest first, following the enum's declared order."""

_ROLE: Final = {relation: rank for rank, relation in enumerate(MatchRelation)}
"""Within a level, a match as the listed party ranks before one as a related party."""

_FOUND: Final = frozenset({MatchLevel.EXACT, MatchLevel.HIGH})
_REVIEW: Final = frozenset({MatchLevel.POTENTIAL, MatchLevel.AMBIGUOUS})

_REMOVED_LEAD: Final = "Removed from published list of"
"""The summary lead of found records that left their list; reinstatement is not confirmed."""


class CheckExclusions:
    """Checks a provider against the stored exclusion lists and records each result."""

    def __init__(
        self,
        connectors: ConnectorDirectory,
        providers: ProviderRepository,
        records: ExclusionRecordRepository,
        snapshots: SnapshotRepository,
        verifications: VerificationRepository,
        freshness: FreshnessPolicyTable,
        clock: Clock,
    ) -> None:
        self._connectors = connectors
        self._providers = providers
        self._records = records
        self._snapshots = snapshots
        self._verifications = verifications
        self._freshness = freshness
        self._clock = clock

    async def run(self, organization_id: UUID, provider_id: UUID) -> ProviderVerdict:
        """Check the provider against every connected exclusion list, federal and state.

        The provider's licence and address states do not narrow the check: an exclusion in
        any state matters, since states must terminate providers that another state
        terminated for cause.

        One result per list, each appended to the verification history, and the verdict
        derived from them: every list has just answered, so older results do not count. A
        list that was never loaded gives ``SOURCE_UNAVAILABLE``, never a clean result.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        connectors = self._exclusion_lists()
        if not connectors:
            return derive_verdict(())
        now = self._clock.now()
        candidates = await self._records.find_candidates(
            provider.npi,
            provider.person_name.last if provider.person_name else None,
            provider.organization_name,
            license_numbers=[license.number for license in provider.licenses],
        )
        results: list[VerificationResult] = []
        for connector in connectors:
            capabilities = connector.capabilities
            own = [record for record in candidates if record.source_id == capabilities.source_id]
            result = await self._check(provider, capabilities, own, now)
            await self._verifications.add(result)
            results.append(result)
        return derive_verdict(results)

    def _exclusion_lists(self) -> list[BulkListConnector]:
        """Every connected exclusion list, federal and state, ordered by source id."""
        return [
            connector
            for connector in self._connectors.bulk_connectors()
            if connector.capabilities.credential_type is CredentialType.EXCLUSION
        ]

    async def _check(
        self,
        provider: Provider,
        capabilities: ConnectorCapabilities,
        candidates: Sequence[ExclusionRecord],
        now: datetime,
    ) -> VerificationResult:
        source_id = capabilities.source_id
        latest = await self._snapshots.latest(source_id)
        completed = await self._snapshots.latest_completed(source_id)
        policy = self._freshness.policy_for(CredentialType.EXCLUSION, source_id)

        def result(
            *,
            outcome: Outcome,
            summary: str,
            provenance: Provenance,
            fetched_at: datetime,
            details: dict[str, str],
            finding: Finding | None = None,
            match: MatchResult | None = None,
            matched: tuple[UUID, ...] = (),
        ) -> VerificationResult:
            if latest is not None and latest.outcome is not Outcome.COMPLETED:
                details["latest_fetch_outcome"] = latest.outcome.value
                details["latest_fetch_at"] = latest.fetched_at.isoformat()
            return VerificationResult(
                id=uuid4(),
                organization_id=provider.organization_id,
                provider_id=provider.id,
                credential_type=CredentialType.EXCLUSION,
                source_id=source_id,
                outcome=outcome,
                finding=finding,
                match=match,
                matched_record_ids=matched,
                summary=summary,
                details=details,
                provenance=provenance,
                freshness=policy.evaluate(fetched_at, now, capabilities.method),
                created_at=now,
            )

        if completed is None:
            return result(
                outcome=Outcome.SOURCE_UNAVAILABLE,
                summary=(
                    f"{capabilities.display_name} has never been loaded, "
                    "so this check could not run"
                ),
                provenance=_provenance(capabilities, now, latest),
                fetched_at=now,
                details={},
            )

        ranked = sorted(
            ((match_provider_to_record(provider, record), record) for record in candidates),
            key=lambda pair: (
                _STRENGTH[pair[0].level],
                _ROLE[pair[0].relation],
                str(pair[1].id),
            ),
        )
        best = ranked[0][0].level if ranked else MatchLevel.NONE
        strongest = [
            pair for pair in ranked if pair[0].level is best and best is not MatchLevel.NONE
        ]
        matched = tuple(record.id for _, record in strongest)
        matched_records = [record for _, record in strongest]
        today = now.astimezone(UTC).date()
        details = {
            "list_fetched_at": completed.fetched_at.isoformat(),
            "candidates_compared": str(len(candidates)),
        }
        if matched_records:
            details[RECORD_STATUS_DETAIL] = record_status(matched_records, today).value
        if best in _FOUND and any(m.relation is MatchRelation.PARTY for m, _ in strongest):
            finding = Finding.RECORD_FOUND
            summary = _found_summary(
                capabilities,
                matched_records,
                today,
                standing="Listed in",
                ended="Previously listed in",
            )
        elif best in _FOUND:
            finding = Finding.RELATED_PARTY
            summary = _found_summary(
                capabilities,
                matched_records,
                today,
                standing="Flagged in",
                ended="Previously flagged in",
                context="named with a listed party, ",
            )
        elif best in _REVIEW:
            finding = Finding.AMBIGUOUS
            summary = f"Possible match in {capabilities.display_name}: review required"
        else:
            finding = Finding.NO_RECORD
            summary = (
                f"No record in {capabilities.display_name} "
                f"(list fetched {_moment(completed.fetched_at)})"
            )
        provenance = _provenance(
            capabilities,
            now,
            completed,
            raw_record_ref=f"exclusion_records/{matched[0]}" if len(matched) == 1 else None,
        )
        return result(
            outcome=Outcome.COMPLETED,
            finding=finding,
            match=strongest[0][0] if strongest else None,
            matched=matched,
            summary=summary,
            provenance=provenance,
            fetched_at=completed.fetched_at,
            details=details,
        )


def _provenance(
    capabilities: ConnectorCapabilities,
    now: datetime,
    snapshot: SourceSnapshot | None,
    *,
    raw_record_ref: str | None = None,
) -> Provenance:
    completed = snapshot is not None and snapshot.outcome is Outcome.COMPLETED
    return Provenance(
        source_id=capabilities.source_id,
        source_url=capabilities.source_url,
        method=capabilities.method,
        verified_at=now,
        source_updated_at=(snapshot.last_modified or snapshot.fetched_at)
        if snapshot is not None and completed
        else None,
        snapshot_id=snapshot.id if snapshot is not None else None,
        raw_record_ref=raw_record_ref,
    )


def _found_summary(
    capabilities: ConnectorCapabilities,
    records: Sequence[ExclusionRecord],
    today: date,
    *,
    standing: str,
    ended: str,
    context: str = "",
) -> str:
    """The summary of found records: the ``standing`` lead ("Listed in") while one record
    stands on its list, the removed lead ("Removed from published list of") and the date it
    left when every standing record was removed from its list, otherwise the ``ended`` lead
    ("Previously listed in") and the end date; ``context`` goes before the action ("named
    with a listed party, ")."""
    status = record_status(records, today)
    has_ended = status is RecordStatus.ENDED
    current = [record for record in records if not record_has_ended(record, today)]
    if status is RecordStatus.ACTIVE:
        current = [record for record in current if not record_is_removed(record)]
    record = records[0] if has_ended else current[0]
    effective = (
        f"effective {record.effective_date.isoformat()}"
        if record.effective_date
        else "effective date not published"
    )
    lead = {RecordStatus.ENDED: ended, RecordStatus.REMOVED: _REMOVED_LEAD}.get(status, standing)
    summary = f"{lead} {capabilities.display_name}: {context}{record.action.value} {effective}"
    if record.end_date:
        summary += f" \u2013 {'ended' if has_ended else 'ends'} {record.end_date.isoformat()}"
    if status is RecordStatus.REMOVED and record.valid_to is not None:
        left = record.valid_to.astimezone(UTC).date()
        summary += f" \u2013 not in the list since {left.isoformat()}"
    if len(records) > 1:
        summary += f" ({len(records)} matching records)"
    return summary


def _moment(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
