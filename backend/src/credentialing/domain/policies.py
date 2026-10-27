"""Domain policies: freshness windows, provider-to-record matching and the verdict roll-up.

Freshness numbers live only here. Matching is deterministic and explainable: every rule that
fires leaves a piece of evidence, and the explanation is built from that evidence. The verdict
is decided here once, so no screen ever derives a status of its own.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final, Self

from pydantic import Field, model_validator

from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    Finding,
    Freshness,
    MatchField,
    MatchLevel,
    Outcome,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    DomainModel,
    ExclusionRecord,
    License,
    MatchEvidence,
    MatchResult,
    PersonName,
    Provider,
    ProviderVerdict,
    VerificationResult,
)
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.types import SourceId

# --- Freshness -----------------------------------------------------------------------------


class FreshnessPolicy(DomainModel):
    """How long a result may be served before it is stale.

    ``live_window`` is how recent a result must be to count as fetched during this request.
    """

    max_age: timedelta = Field(gt=timedelta(0))
    live_window: timedelta = Field(default=timedelta(minutes=1), ge=timedelta(0))

    def evaluate(
        self, verified_at: datetime, now: datetime, method: VerificationMethod
    ) -> Freshness:
        """Classify a result verified at ``verified_at`` as seen at ``now``."""
        if method is VerificationMethod.MANUAL:
            return Freshness.MANUAL
        age = now - verified_at
        if age <= self.live_window:
            return Freshness.LIVE
        if age <= self.max_age:
            return Freshness.CACHED
        return Freshness.STALE


type PolicyKey = tuple[CredentialType, SourceId | None]


class FreshnessPolicyTable(DomainModel):
    """Freshness policies by ``(credential type, source)``, with a per-credential-type default.

    A ``None`` source is the default for its credential type; every credential type must have
    one, so a lookup always succeeds.
    """

    policies: Mapping[PolicyKey, FreshnessPolicy]

    @model_validator(mode="after")
    def _every_credential_type_has_a_default(self) -> Self:
        missing = [kind for kind in CredentialType if (kind, None) not in self.policies]
        if missing:
            raise ValueError(f"no default freshness policy for {', '.join(missing)}")
        return self

    def policy_for(self, credential_type: CredentialType, source_id: str) -> FreshnessPolicy:
        """Return the source's own policy, or the credential type's default."""
        return self.policies.get(
            (credential_type, source_id), self.policies[(credential_type, None)]
        )


DEFAULT_FRESHNESS: Final = FreshnessPolicyTable(
    policies={
        (CredentialType.EXCLUSION, None): FreshnessPolicy(max_age=timedelta(days=7)),
        (CredentialType.NPI, None): FreshnessPolicy(max_age=timedelta(days=30)),
        (CredentialType.STATE_LICENSE, None): FreshnessPolicy(max_age=timedelta(days=1)),
        (CredentialType.BOARD_CERTIFICATION, None): FreshnessPolicy(max_age=timedelta(days=30)),
    }
)
"""The single place freshness windows are declared."""


# --- Matching ------------------------------------------------------------------------------

_FIELD_LABELS: Final = {
    MatchField.NPI: "NPI",
    MatchField.LAST_NAME: "last name",
    MatchField.FIRST_NAME: "first name",
    MatchField.MIDDLE_NAME: "middle name",
    MatchField.DATE_OF_BIRTH: "date of birth",
    MatchField.LICENSE: "license",
    MatchField.ADDRESS: "address",
    MatchField.ORGANIZATION_NAME: "organization name",
}

_LEVEL_LABELS: Final = {
    MatchLevel.EXACT: "Exact match",
    MatchLevel.HIGH: "High-confidence match",
    MatchLevel.POTENTIAL: "Potential match",
    MatchLevel.AMBIGUOUS: "Ambiguous match",
    MatchLevel.NONE: "No match",
}


def match_provider_to_record(provider: Provider, record: ExclusionRecord) -> MatchResult:
    """Decide how strongly ``record`` points at ``provider``, with the evidence for it.

    Both NPIs present decides alone: equal is EXACT, different is NONE. Otherwise names
    decide (the record's aliases count as names). For organizations, equal names are
    POTENTIAL, compared without legal designators such as "Inc" or "LLC". For individuals,
    equal last names where one first name is only the other's
    initial is AMBIGUOUS, and equal last and first names are POTENTIAL, then settled in this
    order, strongest identifier first:

    1. Date of birth (both present): different is NONE; equal is HIGH, even when the middle
       names conflict (the conflict stays in the evidence).
    2. Middle name (both present, compared on the record's own name, not an alias):
       conflicting full names or initials are AMBIGUOUS, because lists contain data-entry
       errors and a human decides; equal names, or an initial equal to the other's first
       letter, are HIGH.
    3. License (same number and state): HIGH.

    A licence that matches when the names do not (a licence-only candidate) is AMBIGUOUS, never
    NONE and never a finding on its own; a date of birth that differs still rules it out. The
    New York list showed why: licence numbers are unique only within a profession, not within
    a state. 46 numbers were shared by different people, 42 of them across professions (a
    chiropractor and a massage therapist with the same number), and 4 of those groups carried
    two different NPIs, so a licence number alone cannot identify a person. ``License.kind``
    is not compared yet because the sources name professions in different vocabularies;
    comparing it once they are normalized is the refinement that can settle these cases.

    A name-only match is never EXACT.
    """
    if provider.npi and record.npi:
        matched = provider.npi == record.npi
        evidence = (MatchEvidence(field=MatchField.NPI, matched=matched),)
        return _result(MatchLevel.EXACT if matched else MatchLevel.NONE, evidence)

    if provider.entity_kind is not EntityKind.ORGANIZATION and provider.person_name:
        return _match_individual(provider, provider.person_name, record)
    if provider.organization_name:
        return _match_organization(provider, provider.organization_name, record)
    return _result(MatchLevel.NONE, ())


def candidate_keys(record: ExclusionRecord) -> frozenset[str]:
    """Normalized names under which ``record`` can be found as a candidate.

    The last name, the organization name without its legal designators, and each alias whole,
    without legal designators and word by word, so a lookup by normalized last name or
    organization name finds every record the matcher could match by name.
    """
    keys: set[str] = set()
    if record.person_name:
        keys.add(normalize_name(record.person_name.last))
    if record.organization_name:
        keys.add(normalize_organization_name(record.organization_name))
    for alias in record.aliases:
        normalized = normalize_name(alias)
        keys.add(normalized)
        keys.add(normalize_organization_name(alias))
        keys.update(normalized.split())
    keys.discard("")
    return frozenset(keys)


def _match_individual(provider: Provider, name: PersonName, record: ExclusionRecord) -> MatchResult:
    last = normalize_name(name.last)
    first = normalize_name(name.first)
    if not last or not first:
        return _result(MatchLevel.NONE, ())

    level, evidence = _compare_person(last, first, record.person_name)
    middle = None
    if level is MatchLevel.POTENTIAL and record.person_name:
        middle = _compare_middle(name.middle, record.person_name.middle)
        if middle:
            evidence.append(middle)
    if level is MatchLevel.NONE:
        alias = _matching_alias(record.aliases, last, first)
        if alias is not None:
            level = MatchLevel.POTENTIAL
            evidence = [
                MatchEvidence(field=MatchField.LAST_NAME, matched=True, detail=f"alias {alias!r}"),
                MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail=f"alias {alias!r}"),
            ]
    if level is MatchLevel.NONE:
        return _license_only(provider, record, evidence, MatchField.LAST_NAME)

    same_birth_date = False
    if provider.date_of_birth and record.date_of_birth:
        same_birth_date = provider.date_of_birth == record.date_of_birth
        evidence.append(MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=same_birth_date))
        if not same_birth_date:
            return _result(MatchLevel.NONE, tuple(evidence))

    listed = record.license
    same_license = listed is not None and any(
        _same_license(own, listed) for own in provider.licenses
    )
    if same_license:
        evidence.append(MatchEvidence(field=MatchField.LICENSE, matched=True))

    if level is MatchLevel.POTENTIAL:
        if same_birth_date:
            level = MatchLevel.HIGH
        elif middle and not middle.matched:
            level = MatchLevel.AMBIGUOUS
        elif (middle and middle.matched) or same_license:
            level = MatchLevel.HIGH
    return _result(level, tuple(evidence))


def _compare_person(
    last: str, first: str, other: PersonName | None
) -> tuple[MatchLevel, list[MatchEvidence]]:
    if other is None or normalize_name(other.last) != last:
        return MatchLevel.NONE, []
    evidence = [MatchEvidence(field=MatchField.LAST_NAME, matched=True)]
    other_first = normalize_name(other.first)
    if other_first == first:
        evidence.append(MatchEvidence(field=MatchField.FIRST_NAME, matched=True))
        return MatchLevel.POTENTIAL, evidence
    if other_first and _initial_of(first, other_first):
        evidence.append(
            MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail="initial only")
        )
        return MatchLevel.AMBIGUOUS, evidence
    evidence.append(MatchEvidence(field=MatchField.FIRST_NAME, matched=False))
    return MatchLevel.NONE, evidence


def _compare_middle(own: str | None, other: str | None) -> MatchEvidence | None:
    """Middle-name evidence, or None when either side has no middle name."""
    own_middle = normalize_name(own) if own else ""
    other_middle = normalize_name(other) if other else ""
    if not own_middle or not other_middle:
        return None
    if own_middle == other_middle:
        return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=True)
    if _initial_of(own_middle, other_middle):
        return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=True, detail="initial only")
    return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=False)


def _initial_of(first: str, other: str) -> bool:
    """Whether one name is a single initial that starts the other."""
    shorter, longer = sorted((first, other), key=len)
    return len(shorter) == 1 and longer.startswith(shorter)


def _matching_alias(aliases: tuple[str, ...], last: str, first: str) -> str | None:
    """The first alias containing every word of the last and the first name, if any."""
    wanted = set(last.split()) | set(first.split())
    for alias in aliases:
        if wanted <= set(normalize_name(alias).split()):
            return alias
    return None


def _match_organization(
    provider: Provider, organization_name: str, record: ExclusionRecord
) -> MatchResult:
    """Equal names after dropping legal designators are POTENTIAL; the evidence says so."""
    wanted = normalize_organization_name(organization_name)
    if not wanted:
        return _result(MatchLevel.NONE, ())
    plain = normalize_name(organization_name)
    names: list[tuple[str, str | None]] = (
        [(record.organization_name, None)] if record.organization_name else []
    )
    names += [(alias, f"alias {alias!r}") for alias in record.aliases]
    for name, via in names:
        if normalize_organization_name(name) != wanted:
            continue
        notes = [via] if via else []
        if normalize_name(name) != plain:
            notes.append("legal suffix ignored")
        evidence = MatchEvidence(
            field=MatchField.ORGANIZATION_NAME, matched=True, detail="; ".join(notes) or None
        )
        return _result(MatchLevel.POTENTIAL, (evidence,))
    return _license_only(provider, record, [], MatchField.ORGANIZATION_NAME)


_LICENSE_ONLY_NOTE: Final = (
    "licence numbers are unique only within a profession, so a person must confirm"
)


def _license_only(
    provider: Provider,
    record: ExclusionRecord,
    name_evidence: list[MatchEvidence],
    name_field: MatchField,
) -> MatchResult:
    """The names did not match: a shared licence makes it AMBIGUOUS, otherwise NONE.

    ``name_evidence`` is what the name comparison found; when it found nothing, the evidence
    records ``name_field`` as not matched so the explanation says why a person must look.
    """
    listed = record.license
    if listed is None or not any(_same_license(own, listed) for own in provider.licenses):
        return _result(MatchLevel.NONE, tuple(name_evidence))
    evidence = [
        MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number"),
        *(name_evidence or [MatchEvidence(field=name_field, matched=False)]),
    ]
    if provider.date_of_birth and record.date_of_birth:
        same_birth_date = provider.date_of_birth == record.date_of_birth
        evidence.append(MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=same_birth_date))
        if not same_birth_date:
            return _result(MatchLevel.NONE, tuple(evidence))
    return _result(MatchLevel.AMBIGUOUS, tuple(evidence), note=_LICENSE_ONLY_NOTE)


def license_key(number: str) -> str:
    """A licence number as matching compares it: whitespace removed, upper case."""
    return "".join(number.split()).upper()


def _same_license(own: License, other: License) -> bool:
    return own.state == other.state and license_key(own.number) == license_key(other.number)


def _result(
    level: MatchLevel, evidence: tuple[MatchEvidence, ...], *, note: str | None = None
) -> MatchResult:
    return MatchResult(level=level, evidence=evidence, explanation=_explain(level, evidence, note))


def _explain(level: MatchLevel, evidence: tuple[MatchEvidence, ...], note: str | None) -> str:
    if not evidence:
        return f"{_LEVEL_LABELS[level]}: no identifying field matches."
    parts = []
    for item in evidence:
        part = f"{_FIELD_LABELS[item.field]} {'matches' if item.matched else 'differs'}"
        parts.append(f"{part} ({item.detail})" if item.detail else part)
    ending = f"; {note}" if note else ""
    return f"{_LEVEL_LABELS[level]}: {', '.join(parts)}{ending}."


# --- Verdict -------------------------------------------------------------------------------


def derive_verdict(latest_per_source: Sequence[VerificationResult]) -> ProviderVerdict:
    """Roll the latest result of each source up into the provider's verdict.

    The most serious answer wins: a found record is LISTED; otherwise an ambiguous match is
    NEEDS_REVIEW; otherwise a source that could not be checked is COULD_NOT_CHECK, never clean;
    NO_RECORDS only when every source answered with none; NOT_CHECKED without results. Should
    a source appear twice, its newest result counts.
    """
    newest: dict[str, VerificationResult] = {}
    for result in latest_per_source:
        kept = newest.get(result.source_id)
        if kept is None or (result.created_at, str(result.id)) > (kept.created_at, str(kept.id)):
            newest[result.source_id] = result
    results = tuple(newest[source_id] for source_id in sorted(newest))

    listed_in = tuple(r.source_id for r in results if r.finding is Finding.RECORD_FOUND)
    review_in = tuple(r.source_id for r in results if r.finding is Finding.AMBIGUOUS)
    unchecked = tuple(r.source_id for r in results if r.outcome is not Outcome.COMPLETED)
    if listed_in:
        verdict = Verdict.LISTED
    elif review_in:
        verdict = Verdict.NEEDS_REVIEW
    elif unchecked:
        verdict = Verdict.COULD_NOT_CHECK
    elif results:
        verdict = Verdict.NO_RECORDS
    else:
        verdict = Verdict.NOT_CHECKED

    list_dates = [
        r.provenance.source_updated_at
        for r in results
        if r.outcome is Outcome.COMPLETED and r.provenance.source_updated_at is not None
    ]
    return ProviderVerdict(
        verdict=verdict,
        sources_checked=len(results),
        listed_in=listed_in,
        review_in=review_in,
        unchecked=unchecked,
        oldest_list_updated_at=min(list_dates, default=None),
        last_checked_at=max((r.created_at for r in results), default=None),
        results=results,
    )
