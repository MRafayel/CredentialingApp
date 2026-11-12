"""Use case: verify a roster provider's NPI against NPPES, one appended result per check.

The provider's NPI is looked up as :class:`~credentialing.application.npi_lookup.NpiLookup`
does (the mirror first, the live registry when asked or needed, answers stored when newer),
and the registry status becomes the result: active, deactivated, not in NPPES, a contradiction
between the mirror and the live registry to review, or a check that could not run. Nothing
collapses into "clean": a provider without an NPI is a manual step, a failed request with no
mirror row is that failure's outcome. Summaries carry the NPI and dates, never a name. The
exclusion verdict is untouched: this result is of its own credential type.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.npi_lookup import LiveCheck, MirrorSource, NpiLookup, NpiView
from credentialing.application.provider_status import StatusRefreshing, newest
from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    Finding,
    Freshness,
    Outcome,
    RegistryStatus,
    VerificationMethod,
)
from credentialing.domain.models import (
    MatchResult,
    NpiRecord,
    Provenance,
    Provider,
    VerificationResult,
)
from credentialing.domain.policies import (
    NO_NPI_REASON,
    NPI_STATUS_DETAIL,
    match_provider_to_npi,
    result_change,
)
from credentialing.domain.ports import Clock, ProviderRepository, VerificationRepository
from credentialing.domain.types import SourceId

_NO_NPI = "No NPI on file: search NPPES to find it"


@dataclass(frozen=True, slots=True)
class _Draft:
    """A result's parts before it is stamped with ids, the provider and the match.

    ``record`` is the registry record the result rests on, matched against the provider.
    """

    outcome: Outcome
    summary: str
    provenance: Provenance
    freshness: Freshness
    finding: Finding | None = None
    details: dict[str, str] = field(default_factory=dict)
    record: NpiRecord | None = None


class VerifyNpi:
    """Checks a provider's NPI in NPPES and appends the result to the history.

    An answer carries what changed since the provider's previous NPI answer (its last
    completed NPI result), whichever NPPES file or request gave it
    (:func:`~credentialing.domain.policies.result_change`); a result that could not be
    checked carries none.
    """

    def __init__(
        self,
        providers: ProviderRepository,
        npi_lookup: NpiLookup,
        verifications: VerificationRepository,
        clock: Clock,
        *,
        source_id: SourceId,
        source_url: str,
        status: StatusRefreshing | None = None,
    ) -> None:
        """``source_id`` and ``source_url`` name the live registry, for the results that rest
        on no answer (no NPI on file, no way to ask). ``status`` keeps the provider's stored
        monitoring status in step after each verification."""
        self._providers = providers
        self._lookup = npi_lookup
        self._verifications = verifications
        self._clock = clock
        self._source_id = source_id
        self._source_url = source_url
        self._status = status

    async def run(
        self,
        organization_id: UUID,
        provider_id: UUID,
        *,
        live: bool = False,
        mirror_only: bool = False,
        details: Mapping[str, str] | None = None,
        view: NpiView | None = None,
    ) -> VerificationResult:
        """Verify the provider's NPI (asking the live registry when ``live``, or when the
        mirror has no row or is stale) and append exactly one result.

        ``mirror_only`` never asks the live registry (the scheduled recheck): an NPI the
        mirror lacks is then a check that could not run, never "not in NPPES". ``details``
        are added to the result's own (who set the NPI, when). ``view`` is a lookup of the
        provider's NPI the caller has just made: the result rests on it instead of a second
        lookup, so it records the very answer the caller acted on.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
            ValueError: Both ``live`` and ``mirror_only``.
        """
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        now = self._clock.now()
        if view is not None and view.npi == provider.npi:
            draft = self._draft(view, now)
        elif provider.npi is None:
            draft = _Draft(
                outcome=Outcome.MANUAL_REQUIRED,
                summary=_NO_NPI,
                provenance=self._own_provenance(now),
                freshness=Freshness.LIVE,
                details={"reason": NO_NPI_REASON},
            )
        else:
            found = await self._lookup.get(provider.npi, live=live, mirror_only=mirror_only)
            draft = self._draft(found, now)
        match = _match(provider, draft.record)
        merged = dict(draft.details)
        if merged.get(NPI_STATUS_DETAIL) == RegistryStatus.ACTIVE.value and match is not None:
            merged["name_match"] = match.level.value
        merged.update(details or {})
        previous = newest(
            await self._verifications.latest_per_source(
                organization_id, provider_id, CredentialType.NPI, completed_only=True
            )
        )
        result = VerificationResult(
            id=uuid4(),
            organization_id=organization_id,
            provider_id=provider_id,
            credential_type=CredentialType.NPI,
            source_id=draft.provenance.source_id,
            outcome=draft.outcome,
            finding=draft.finding,
            match=match,
            summary=draft.summary,
            details=merged,
            provenance=draft.provenance,
            freshness=draft.freshness,
            created_at=now,
        )
        result = result.model_copy(update={"changed_from": result_change(previous, result)})
        await self._verifications.add(result)
        if self._status is not None:
            await self._status.refresh(provider)
        return result

    def _draft(self, view: NpiView, now: datetime) -> _Draft:
        """The result's parts for each registry status."""
        npi, live, mirror = view.npi, view.live, view.mirror
        status = view.registry_status
        if status is RegistryStatus.NOT_FOUND and live is not None:
            checked = _moment(live.provenance.verified_at)
            return _on_live(
                live,
                finding=Finding.NO_RECORD,
                summary=f"NPI {npi} is not in NPPES (checked live {checked})",
                details={NPI_STATUS_DETAIL: status.value, "checked_live": "true"},
            )
        if status is RegistryStatus.MIRROR_ONLY_ACTIVE and live is not None and mirror:
            return _on_live(
                live,
                finding=Finding.AMBIGUOUS,
                summary=(
                    f"The NPPES file of {_file_date(mirror)} lists NPI {npi} as active, "
                    "but the live registry returned no result: review"
                ),
                details={NPI_STATUS_DETAIL: status.value, "checked_live": "true"},
                record=view.mirror_record,
            )
        live_found = live is not None and live.outcome is Outcome.COMPLETED and bool(live.found)
        if status is RegistryStatus.ACTIVE and live_found and live and view.record:
            return _on_live(
                live,
                finding=Finding.RECORD_FOUND,
                summary=_active(view.record),
                details=_record_details(status, view.record, live),
                record=view.record,
            )
        record = view.mirror_record
        if status in (RegistryStatus.ACTIVE, RegistryStatus.DEACTIVATED) and record and mirror:
            summary = _active(record) if status is RegistryStatus.ACTIVE else _deactivated(record)
            return _Draft(
                outcome=Outcome.COMPLETED,
                finding=Finding.RECORD_FOUND,
                summary=summary,
                provenance=mirror.provenance.model_copy(
                    update={"raw_record_ref": f"npi_registry/{record.npi}"}
                ),
                freshness=mirror.freshness,
                details=_record_details(status, record, live),
                record=record,
            )
        if live is None and mirror is not None and view.mirror_record is None:
            return _Draft(
                outcome=Outcome.SOURCE_UNAVAILABLE,
                summary=(
                    f"NPPES could not be checked: NPI {npi} is not in the NPPES files of "
                    f"{_file_date(mirror)} and the live registry was not asked"
                ),
                provenance=mirror.provenance,
                freshness=mirror.freshness,
                details={"reason": "not_in_mirror"},
            )
        return self._could_not_check(live, now)

    def _could_not_check(self, live: LiveCheck | None, now: datetime) -> _Draft:
        """No usable answer: the live request's failure, or no way to ask at all."""
        if live is not None and live.outcome is not Outcome.COMPLETED:
            return _Draft(
                outcome=live.outcome,
                summary=f"NPPES could not be checked: {live.note or live.outcome.value}",
                provenance=live.provenance,
                freshness=live.freshness,
                details={"live_check_outcome": live.outcome.value},
            )
        return _Draft(
            outcome=Outcome.SOURCE_UNAVAILABLE,
            summary="NPPES could not be checked: no live registry answer and no mirror load",
            provenance=self._own_provenance(now),
            freshness=Freshness.LIVE,
            details={"reason": "no_registry_answer"},
        )

    def _own_provenance(self, now: datetime) -> Provenance:
        return Provenance(
            source_id=self._source_id,
            source_url=self._source_url,
            method=VerificationMethod.OFFICIAL_API,
            verified_at=now,
        )


def _on_live(
    live: LiveCheck,
    *,
    finding: Finding,
    summary: str,
    details: dict[str, str],
    record: NpiRecord | None = None,
) -> _Draft:
    """A completed result on the live answer's provenance, pointing at the record used."""
    provenance = (
        live.provenance.model_copy(update={"raw_record_ref": f"npi_registry/{record.npi}"})
        if record is not None
        else live.provenance
    )
    return _Draft(
        outcome=Outcome.COMPLETED,
        finding=finding,
        summary=summary,
        provenance=provenance,
        freshness=live.freshness,
        details=details,
        record=record,
    )


def _record_details(
    status: RegistryStatus, record: NpiRecord, live: LiveCheck | None
) -> dict[str, str]:
    """What a result resting on a record says: the status, the kind, the record's date,
    whether the live registry answered, and a live request's failure when it did not."""
    details = {NPI_STATUS_DETAIL: status.value, "entity_kind": record.entity_kind.value}
    if record.last_updated is not None:
        details["registry_last_updated"] = record.last_updated.isoformat()
    answered = live is not None and live.outcome is Outcome.COMPLETED
    details["checked_live"] = "true" if answered else "false"
    if live is not None and not answered:
        details["live_check_outcome"] = live.outcome.value
        details["live_check_at"] = live.provenance.verified_at.isoformat()
    return details


def _match(provider: Provider, record: NpiRecord | None) -> MatchResult | None:
    """The provider against the record used; none for a record without a name (a stub)."""
    if record is None or record.entity_kind is EntityKind.UNKNOWN:
        return None
    return match_provider_to_npi(provider, record)


def _active(record: NpiRecord) -> str:
    updated = record.last_updated.isoformat() if record.last_updated else "not stated"
    return f"NPI {record.npi} is active in NPPES (record last updated {updated})"


def _deactivated(record: NpiRecord) -> str:
    since = record.deactivation_date
    summary = (
        f"NPI {record.npi} is deactivated in NPPES since {since.isoformat()}"
        if since
        else f"NPI {record.npi} is deactivated in NPPES (date not stated)"
    )
    if record.replacement_npi:
        summary += f", replaced by {record.replacement_npi}"
    return summary


def _file_date(mirror: MirrorSource) -> str:
    """The date of the file the mirror is as of: CMS's own, else when it was fetched."""
    stated = mirror.provenance.source_updated_at or mirror.provenance.verified_at
    return stated.astimezone(UTC).date().isoformat()


def _moment(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
